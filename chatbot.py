#!/usr/bin/env python3
"""
텔레그램 금융 Q&A 챗봇
사용자 질문에 Gemini가 한국 투자자 관점으로 실시간 답변
"""

import os
import json
import time
import requests
import yfinance as yf
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
ALLOWED_CHAT_ID = int(os.environ["TELEGRAM_CHAT_ID"])

KST = ZoneInfo("Asia/Seoul")
BASE_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"


# ── 텔레그램 API 헬퍼 ──────────────────────────────────────

def get_updates(offset=None):
    try:
        r = requests.get(
            f"{BASE_URL}/getUpdates",
            params={"timeout": 30, "offset": offset},
            timeout=35,
        )
        return r.json().get("result", [])
    except Exception:
        return []


def send_message(chat_id: int, text: str):
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    r = requests.post(f"{BASE_URL}/sendMessage", json=payload, timeout=15)
    if not r.json().get("ok"):
        payload.pop("parse_mode")
        requests.post(f"{BASE_URL}/sendMessage", json=payload, timeout=15)


def send_typing(chat_id: int):
    requests.post(
        f"{BASE_URL}/sendChatAction",
        json={"chat_id": chat_id, "action": "typing"},
        timeout=5,
    )


# ── 시장 데이터 ─────────────────────────────────────────────

def fetch_market_context() -> dict:
    ctx = {"timestamp_kst": datetime.now(KST).strftime("%Y-%m-%d %H:%M")}

    # 주요 지수
    for name, sym in [("S&P500", "^GSPC"), ("Nasdaq", "^IXIC"), ("Dow", "^DJI")]:
        try:
            hist = yf.Ticker(sym).history(period="5d")
            if len(hist) >= 2:
                prev, last = hist["Close"].iloc[-2], hist["Close"].iloc[-1]
                ctx[name] = {
                    "price": round(float(last), 2),
                    "change_pct": round((last - prev) / prev * 100, 2),
                }
        except Exception:
            pass

    # 코인
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/simple/price"
            "?ids=bitcoin,ethereum&vs_currencies=usd&include_24hr_change=true",
            timeout=10,
        )
        data = r.json()
        ctx["BTC"] = {
            "price": data["bitcoin"]["usd"],
            "change_24h": round(data["bitcoin"]["usd_24h_change"], 2),
        }
        ctx["ETH"] = {
            "price": data["ethereum"]["usd"],
            "change_24h": round(data["ethereum"]["usd_24h_change"], 2),
        }
    except Exception:
        pass

    # 환율
    try:
        r = requests.get("https://api.frankfurter.app/latest?from=USD&to=KRW", timeout=8)
        ctx["USD_KRW"] = round(r.json()["rates"]["KRW"], 1)
    except Exception:
        pass

    return ctx


# ── Gemini 답변 ──────────────────────────────────────────────

def ask_gemini(question: str, market_ctx: dict) -> str:
    prompt = f"""당신은 한국 거주자를 위한 금융 어드바이저 챗봇입니다.
투자 경험이 없는 초보자도 이해할 수 있도록 쉽고 친절하게 답변합니다.

## 현재 시장 데이터 ({market_ctx.get('timestamp_kst', '')} KST 기준)
{json.dumps({k: v for k, v in market_ctx.items() if k != 'timestamp_kst'}, ensure_ascii=False, indent=2)}

## 사용자 질문
{question}

## 답변 규칙
- 한국어로 답변
- 텔레그램 Markdown v1 형식 (*굵게*, _이탤릭_)
- 질문에 직접 답하되 5~8문장 이내로 간결하게
- 전문용어는 괄호로 쉽게 설명 (예: 기준금리(중앙은행이 정하는 기본 이자율))
- 한국 생활 맥락 우선 (주담대, 전세, KOSPI, 원화 등)
- 미국 투자심리와 한국 영향 모두 연결해서 설명
- 투자 권유 아닌 정보 제공 — 필요시 "최종 판단은 본인이" 언급
- 모르는 것은 솔직하게 모른다고 답변
"""

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.4, "maxOutputTokens": 800},
    }

    try:
        r = requests.post(url, json=payload, timeout=30)
        r.raise_for_status()
        return r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as e:
        return f"⚠️ 답변 생성 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."


# ── 메시지 처리 ──────────────────────────────────────────────

HELP_TEXT = (
    "*금융 브리핑 봇입니다* 🤖\n\n"
    "궁금한 것을 자유롭게 질문하세요.\n"
    "현재 시장 데이터를 참고해서 답변합니다.\n\n"
    "*질문 예시*\n"
    "• 오늘 나스닥 왜 떨어졌어?\n"
    "• 금리 인하되면 내 주담대 이자 줄어?\n"
    "• 지금 달러 사도 돼?\n"
    "• 비트코인이 주식이랑 같이 움직이는 이유가 뭐야?\n"
    "• ETF가 뭐야?\n"
    "• 인플레이션이 나한테 왜 나쁜 거야?"
)


def handle_update(update: dict):
    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return

    chat_id = msg["chat"]["id"]
    text = msg.get("text", "").strip()

    if chat_id != ALLOWED_CHAT_ID:
        send_message(chat_id, "⛔ 접근 권한이 없습니다.")
        return

    if not text:
        return

    if text.lower() in ("/start", "/help"):
        send_message(chat_id, HELP_TEXT)
        return

    # 일반 질문
    print(f"[{datetime.now(KST).strftime('%H:%M:%S')}] 질문: {text[:60]}")
    send_typing(chat_id)

    market_ctx = fetch_market_context()
    answer = ask_gemini(text, market_ctx)
    send_message(chat_id, answer)
    print(f"[{datetime.now(KST).strftime('%H:%M:%S')}] 답변 완료")


# ── 메인 루프 ────────────────────────────────────────────────

def main():
    print(f"[{datetime.now(KST).strftime('%Y-%m-%d %H:%M KST')}] 챗봇 시작 — 질문 대기 중")
    offset = None
    while True:
        try:
            updates = get_updates(offset)
            for update in updates:
                offset = update["update_id"] + 1
                handle_update(update)
        except KeyboardInterrupt:
            print("챗봇 종료")
            break
        except Exception as e:
            print(f"[ERROR] {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
