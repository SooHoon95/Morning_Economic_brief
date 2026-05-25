#!/usr/bin/env python3
"""
매일 아침 금융 브리핑 봇 (완전 무료 버전)
미국 주식 + 암호화폐 동향을 한국어로 요약해 텔레그램 전송
"""

import os
import requests
import yfinance as yf
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

KST = ZoneInfo("Asia/Seoul")


def arrow(pct: float) -> str:
    return "🔺" if pct > 0 else "🔻"


def fmt_pct(pct: float) -> str:
    sign = "+" if pct > 0 else ""
    return f"{sign}{pct:.2f}%"


def fetch_stock_indices() -> dict:
    symbols = {"S&P 500": "^GSPC", "Nasdaq": "^IXIC", "Dow Jones": "^DJI"}
    results = {}
    for name, sym in symbols.items():
        try:
            hist = yf.Ticker(sym).history(period="5d")
            if len(hist) >= 2:
                prev, last = hist["Close"].iloc[-2], hist["Close"].iloc[-1]
                results[name] = {
                    "close": round(float(last), 2),
                    "change_pct": round((last - prev) / prev * 100, 2),
                    "date": hist.index[-1].strftime("%Y-%m-%d"),
                }
        except Exception as e:
            print(f"[WARN] {name}: {e}")
    return results


def fetch_notable_stocks() -> list:
    symbols = [
        "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA",
        "NFLX", "AMD", "INTC", "BABA", "ORCL", "COIN", "MSTR",
    ]
    movers = []
    for sym in symbols:
        try:
            hist = yf.Ticker(sym).history(period="5d")
            if len(hist) >= 2:
                prev, last = hist["Close"].iloc[-2], hist["Close"].iloc[-1]
                chg = (last - prev) / prev * 100
                movers.append({
                    "symbol": sym,
                    "close": round(float(last), 2),
                    "change_pct": round(chg, 2),
                })
        except Exception as e:
            print(f"[WARN] {sym}: {e}")
    return sorted(movers, key=lambda x: abs(x["change_pct"]), reverse=True)


def fetch_afterhours_movers() -> list:
    symbols = ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "AMD"]
    movers = []
    for sym in symbols:
        try:
            info = yf.Ticker(sym).fast_info
            regular = getattr(info, "last_price", None)
            post = getattr(info, "post_market_price", None)
            if regular and post and regular > 0:
                chg = (post - regular) / regular * 100
                if abs(chg) >= 1.5:
                    movers.append({
                        "symbol": sym,
                        "regular_close": round(float(regular), 2),
                        "post_market": round(float(post), 2),
                        "change_pct": round(chg, 2),
                    })
        except Exception:
            pass
    return sorted(movers, key=lambda x: abs(x["change_pct"]), reverse=True)


def fetch_crypto_data() -> list:
    url = "https://api.coingecko.com/api/v3/coins/markets"
    params = {
        "vs_currency": "usd",
        "order": "market_cap_desc",
        "per_page": 50,
        "page": 1,
        "sparkline": False,
        "price_change_percentage": "24h",
    }
    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[WARN] 코인 데이터: {e}")
        return []


def fetch_kimchi_premium() -> dict | None:
    try:
        r1 = requests.get("https://api.upbit.com/v1/ticker?markets=KRW-BTC", timeout=10)
        btc_krw = r1.json()[0]["trade_price"]

        r2 = requests.get("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT", timeout=10)
        btc_usdt = float(r2.json()["price"])

        r3 = requests.get("https://api.frankfurter.app/latest?from=USD&to=KRW", timeout=10)
        usd_krw = r3.json()["rates"]["KRW"]

        premium = (btc_krw - btc_usdt * usd_krw) / (btc_usdt * usd_krw) * 100
        return {
            "upbit_btc_krw": int(btc_krw),
            "binance_btc_usdt": round(btc_usdt, 2),
            "usd_krw": round(usd_krw, 1),
            "premium_pct": round(premium, 2),
        }
    except Exception as e:
        print(f"[WARN] 김치프리미엄: {e}")
        return None


def fetch_market_news() -> list:
    try:
        r = requests.get(
            "https://feeds.finance.yahoo.com/rss/2.0/headline?s=^GSPC&region=US&lang=en-US",
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        import xml.etree.ElementTree as ET
        root = ET.fromstring(r.text)
        items = root.findall(".//item")
        return [
            {"title": item.findtext("title", ""), "link": item.findtext("link", "")}
            for item in items[:5]
            if item.findtext("title")
        ]
    except Exception as e:
        print(f"[WARN] 뉴스: {e}")
        return []


def one_liner(indices: dict, btc_chg: float | None) -> str:
    """오늘 시장 한마디 자동 생성"""
    sp = indices.get("S&P 500", {}).get("change_pct", 0)
    nq = indices.get("Nasdaq", {}).get("change_pct", 0)

    if sp > 1 and nq > 1:
        mood = "강세"
        comment = "위험선호 심리 우세"
    elif sp < -1 and nq < -1:
        mood = "약세"
        comment = "매도 압력 지속"
    elif abs(sp) < 0.3:
        mood = "혼조"
        comment = "방향성 탐색 중"
    else:
        mood = "보합"
        comment = "관망세"

    crypto_note = ""
    if btc_chg is not None:
        if btc_chg > 3:
            crypto_note = ", 코인도 동반 상승"
        elif btc_chg < -3:
            crypto_note = ", 코인은 하락 동반"

    return f"주식 {mood} ({comment}{crypto_note})"


def format_briefing(indices, movers, afterhours, coins, kimchi, news) -> str:
    now_kst = datetime.now(KST).strftime("%H:%M KST")
    today = datetime.now(KST).strftime("%Y-%m-%d")
    lines = []

    # ── 미국 주식 ──
    if indices:
        lines.append("*📈 미국 주식 시장*")
        for name, d in indices.items():
            a = arrow(d["change_pct"])
            lines.append(f"• {name}: {d['close']:,.2f} {a} {fmt_pct(d['change_pct'])} — {d['date']} 마감")

        # 주목 종목 (1.5% 이상 변동만)
        big = [s for s in movers if abs(s["change_pct"]) >= 1.5][:6]
        if big:
            lines.append("")
            lines.append("*주목 종목*")
            parts = [f"{s['symbol']} {arrow(s['change_pct'])} {fmt_pct(s['change_pct'])}" for s in big]
            lines.append("• " + " · ".join(parts))

        # 시간외
        if afterhours:
            lines.append("")
            lines.append("*시간외 거래*")
            for s in afterhours[:4]:
                lines.append(f"• {s['symbol']} {arrow(s['change_pct'])} {fmt_pct(s['change_pct'])} (after-hours, ${s['post_market']})")

        # 뉴스 헤드라인
        if news:
            lines.append("")
            lines.append("*주요 헤드라인*")
            for n in news[:3]:
                lines.append(f"• {n['title']}")

    # ── 암호화폐 ──
    btc = next((c for c in coins if c["symbol"] == "btc"), None)
    eth = next((c for c in coins if c["symbol"] == "eth"), None)

    if btc or eth:
        lines.append("")
        lines.append("*₿ 암호화폐*")
        if btc:
            chg = btc.get("price_change_percentage_24h") or 0
            lines.append(f"• BTC: ${btc['current_price']:,.0f} {arrow(chg)} {fmt_pct(chg)} — CoinGecko 기준 {now_kst}")
        if eth:
            chg = eth.get("price_change_percentage_24h") or 0
            lines.append(f"• ETH: ${eth['current_price']:,.2f} {arrow(chg)} {fmt_pct(chg)}")

        # 알트 5% 이상
        big_alts = [
            c for c in coins
            if c["symbol"] not in ("btc", "eth")
            and abs(c.get("price_change_percentage_24h") or 0) >= 5
        ]
        if big_alts:
            lines.append("")
            lines.append("*알트 주요 변동 (24h ±5% 이상)*")
            parts = [
                f"{c['symbol'].upper()} {arrow(c['price_change_percentage_24h'])} {fmt_pct(c['price_change_percentage_24h'])}"
                for c in big_alts[:8]
            ]
            lines.append("• " + " | ".join(parts))

        # 김치프리미엄
        if kimchi:
            sign = "+" if kimchi["premium_pct"] > 0 else ""
            lines.append("")
            lines.append(
                f"*김치프리미엄: {sign}{kimchi['premium_pct']:.2f}%*"
                f" (업비트 ₩{kimchi['upbit_btc_krw']:,} vs 바이낸스 ${kimchi['binance_btc_usdt']:,} · 환율 {kimchi['usd_krw']:,}원)"
            )

    # ── 한마디 ──
    btc_chg = btc.get("price_change_percentage_24h") if btc else None
    lines.append("")
    lines.append("---")
    lines.append(f"*오늘 시장 한마디:* {one_liner(indices, btc_chg)}")

    return "\n".join(lines)


def send_telegram(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"}
    r = requests.post(url, json=payload, timeout=15)
    result = r.json()
    if not result.get("ok"):
        payload.pop("parse_mode")
        r = requests.post(url, json=payload, timeout=15)
        result = r.json()
    if not result.get("ok"):
        raise RuntimeError(f"텔레그램 전송 실패: {result}")
    print(f"✅ 전송 완료 (message_id: {result['result']['message_id']})")


def main():
    print(f"[{datetime.now(KST).strftime('%Y-%m-%d %H:%M KST')}] 브리핑 생성 시작")

    print("📊 주식 지수 수집...")
    indices = fetch_stock_indices()

    print("📊 개별 종목 수집...")
    movers = fetch_notable_stocks()

    print("🌙 시간외 거래 수집...")
    afterhours = fetch_afterhours_movers()

    print("₿ 암호화폐 데이터 수집...")
    coins = fetch_crypto_data()

    print("🇰🇷 김치프리미엄 계산...")
    kimchi = fetch_kimchi_premium()

    print("📰 시장 뉴스 수집...")
    news = fetch_market_news()

    print("📝 브리핑 포맷팅...")
    briefing = format_briefing(indices, movers, afterhours, coins, kimchi, news)

    print("\n--- 생성된 브리핑 ---")
    print(briefing)
    print("---------------------\n")

    print("📨 텔레그램 전송...")
    send_telegram(briefing)


if __name__ == "__main__":
    main()
