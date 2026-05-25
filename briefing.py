#!/usr/bin/env python3
"""
매일 아침 금융 브리핑 봇
미국 주식 + 암호화폐 동향을 한국어로 요약해 텔레그램 전송
뉴스 한글 번역/해설은 Google Gemini API (무료) 사용
"""

import os
import json
import requests
import yfinance as yf
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")  # 선택, 없으면 영어 헤드라인만

KST = ZoneInfo("Asia/Seoul")


def arrow(pct: float) -> str:
    return "🔺" if pct > 0 else "🔻"


def fmt_pct(pct: float) -> str:
    sign = "+" if pct > 0 else ""
    return f"{sign}{pct:.2f}%"


def md_escape(text: str) -> str:
    """텔레그램 Markdown(v1)에서 링크 텍스트 안의 ']' 등을 안전하게"""
    return text.replace("[", "(").replace("]", ")")


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
        r1.raise_for_status()
        btc_krw = r1.json()[0]["trade_price"]

        # 바이낸스 직접 API 또는 CoinGecko fallback
        btc_usdt = None
        for url in [
            "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT",
            "https://api.binance.us/api/v3/ticker/price?symbol=BTCUSDT",
        ]:
            try:
                r2 = requests.get(url, timeout=8)
                btc_usdt = float(r2.json()["price"])
                break
            except Exception:
                continue

        if btc_usdt is None:
            return None

        # 환율: Frankfurter → ExchangeRate-API fallback
        usd_krw = None
        for fx_url in [
            "https://api.frankfurter.app/latest?from=USD&to=KRW",
            "https://open.er-api.com/v6/latest/USD",
        ]:
            try:
                r3 = requests.get(fx_url, timeout=8)
                data = r3.json()
                usd_krw = data.get("rates", {}).get("KRW")
                if usd_krw:
                    break
            except Exception:
                continue

        if usd_krw is None:
            return None

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
        results = []
        for item in items[:5]:
            title = item.findtext("title", "")
            if not title:
                continue
            # RSS 2.0: link가 텍스트 노드가 아닌 경우 guid로 fallback
            link = item.findtext("link", "").strip()
            if not link:
                link = item.findtext("guid", "").strip()
            results.append({"title": title, "link": link})
        return results
    except Exception as e:
        print(f"[WARN] 뉴스: {e}")
        return []


def gemini_translate_and_analyze(news: list, indices: dict, btc: dict | None) -> dict | None:
    """Gemini로 뉴스 한글 번역 + 경제 맥락 / 금리 영향 / 실생활 영향 해설"""
    if not GEMINI_API_KEY or not news:
        return None

    headlines = [n["title"] for n in news[:5]]
    sp_chg = indices.get("S&P 500", {}).get("change_pct", 0)
    nq_chg = indices.get("Nasdaq", {}).get("change_pct", 0)
    btc_chg = round(btc.get("price_change_percentage_24h") or 0, 2) if btc else None

    prompt = f"""당신은 경제 교육 전문가입니다. 투자 경험이 없는 일반인도 이해할 수 있도록 미국 시장 뉴스를 해설합니다.

## 오늘의 시장 데이터
- S&P 500: {sp_chg:+.2f}%
- Nasdaq: {nq_chg:+.2f}%
- BTC 24h: {btc_chg:+.2f}% (없으면 null)

## 미국 시장 뉴스 헤드라인 (영문)
{json.dumps(headlines, ensure_ascii=False, indent=2)}

## 작업 지시

### 1. 헤드라인 번역
각 헤드라인을 자연스러운 한국어로 번역. 직역보다 의미 전달 우선.

### 2. 경제적 해석 (2~3문장)
- 오늘 뉴스들이 경제 전체에서 어떤 의미인지 설명
- 연준(Fed), 인플레이션, 경기침체, 기업실적 등 관련 개념을 쉽게 설명
- "이 뉴스가 왜 중요한가"에 답할 것

### 3. 금리 시사점 (2~3문장)
- 오늘 뉴스가 향후 금리 방향에 미칠 영향 설명
- 금리가 오른다/내린다/그대로라면 어떤 시그널인지
- 전문용어는 반드시 괄호로 쉬운 설명 추가 (예: "기준금리(중앙은행이 설정하는 기본 이자율)")

### 4. 나에게 미치는 영향 (항목별, 각 1~2문장)
실제 생활과 연결되는 영향을 구체적으로:
- 대출/모기지: 변동금리 대출이 있는 사람이라면?
- 예금/적금: 은행 이자가 어떻게 바뀔 가능성?
- 주식 투자: 어떤 섹터(분야)에 유리하거나 불리한가?
- 환율: 원/달러 환율에 미칠 영향 (해외직구, 여행, 달러 자산)
- 물가: 일상 소비 물가에 영향이 있다면?

## 출력 형식 (반드시 유효한 JSON만, 다른 텍스트 금지)
{{
  "translations": ["번역1", "번역2", "번역3", "번역4", "번역5"],
  "economic_interpretation": "경제적 해석 2~3문장",
  "rate_outlook": "금리 시사점 2~3문장",
  "personal_impact": {{
    "대출": "대출/모기지 영향",
    "예금": "예금/적금 영향",
    "주식": "주식 투자 영향",
    "환율": "원달러 환율 영향",
    "물가": "일상 물가 영향"
  }}
}}
"""

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.3,
        },
    }

    try:
        r = requests.post(url, json=payload, timeout=30)
        r.raise_for_status()
        text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(text)
    except Exception as e:
        print(f"[WARN] Gemini 분석: {e}")
        return None


def one_liner(indices: dict, btc_chg: float | None) -> str:
    sp = indices.get("S&P 500", {}).get("change_pct", 0)
    nq = indices.get("Nasdaq", {}).get("change_pct", 0)

    if sp > 1 and nq > 1:
        mood, comment = "강세", "위험선호 심리 우세"
    elif sp < -1 and nq < -1:
        mood, comment = "약세", "매도 압력 지속"
    elif abs(sp) < 0.3:
        mood, comment = "혼조", "방향성 탐색 중"
    else:
        mood, comment = "보합", "관망세"

    crypto_note = ""
    if btc_chg is not None:
        if btc_chg > 3:
            crypto_note = ", 코인도 동반 상승"
        elif btc_chg < -3:
            crypto_note = ", 코인은 하락 동반"

    return f"주식 {mood} ({comment}{crypto_note})"


def format_briefing(indices, movers, afterhours, coins, kimchi, news, ai) -> str:
    now_kst = datetime.now(KST).strftime("%H:%M KST")
    lines = []

    # ── 미국 주식 ──
    if indices:
        lines.append("*📈 미국 주식 시장*")
        for name, d in indices.items():
            a = arrow(d["change_pct"])
            lines.append(f"• {name}: {d['close']:,.2f} {a} {fmt_pct(d['change_pct'])} — {d['date']} 마감")

        big = [s for s in movers if abs(s["change_pct"]) >= 1.5][:6]
        if big:
            lines.append("")
            lines.append("*주목 종목*")
            parts = [f"{s['symbol']} {arrow(s['change_pct'])} {fmt_pct(s['change_pct'])}" for s in big]
            lines.append("• " + " · ".join(parts))

        if afterhours:
            lines.append("")
            lines.append("*시간외 거래*")
            for s in afterhours[:4]:
                lines.append(f"• {s['symbol']} {arrow(s['change_pct'])} {fmt_pct(s['change_pct'])} (after-hours, ${s['post_market']})")

        # 헤드라인 (한글 번역 + 출처 링크)
        if news:
            lines.append("")
            lines.append("*📰 주요 헤드라인*")
            translations = ai.get("translations", []) if ai else []
            for i, n in enumerate(news[:5]):
                title = translations[i] if i < len(translations) else n["title"]
                title = md_escape(title)
                link = n.get("link", "")
                if link:
                    lines.append(f"• [{title}]({link})")
                else:
                    lines.append(f"• {title}")

        # 경제적 해석
        if ai and ai.get("economic_interpretation"):
            lines.append("")
            lines.append("*🌐 경제적 해석*")
            lines.append(ai["economic_interpretation"])

        # 금리 시사점
        if ai and ai.get("rate_outlook"):
            lines.append("")
            lines.append("*📊 금리 전망*")
            lines.append(ai["rate_outlook"])

        # 실생활 영향
        if ai and ai.get("personal_impact"):
            lines.append("")
            lines.append("*💬 나에게 미치는 영향*")
            icons = {"대출": "🏠", "예금": "🏦", "주식": "📈", "환율": "💱", "물가": "🛒"}
            for key, val in ai["personal_impact"].items():
                icon = icons.get(key, "•")
                lines.append(f"{icon} *{key}:* {val}")

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

        if kimchi:
            sign = "+" if kimchi["premium_pct"] > 0 else ""
            lines.append("")
            lines.append(
                f"*김치프리미엄: {sign}{kimchi['premium_pct']:.2f}%*"
                f" (업비트 ₩{kimchi['upbit_btc_krw']:,} vs 바이낸스 ${kimchi['binance_btc_usdt']:,} · 환율 {kimchi['usd_krw']:,}원)"
            )

    btc_chg = btc.get("price_change_percentage_24h") if btc else None
    lines.append("")
    lines.append("---")
    lines.append(f"*오늘 시장 한마디:* {one_liner(indices, btc_chg)}")

    return "\n".join(lines)


def send_telegram(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
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

    btc = next((c for c in coins if c["symbol"] == "btc"), None)
    print("🤖 Gemini 뉴스 번역 + 해설 생성..." if GEMINI_API_KEY else "⚠️  GEMINI_API_KEY 없음 — 영문 헤드라인만 표시")
    ai = gemini_translate_and_analyze(news, indices, btc)

    print("📝 브리핑 포맷팅...")
    briefing = format_briefing(indices, movers, afterhours, coins, kimchi, news, ai)

    print("\n--- 생성된 브리핑 ---")
    print(briefing)
    print("---------------------\n")

    print("📨 텔레그램 전송...")
    send_telegram(briefing)


if __name__ == "__main__":
    main()
