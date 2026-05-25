from http.server import BaseHTTPRequestHandler
import json
import os
import requests
from datetime import datetime
from zoneinfo import ZoneInfo

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
ALLOWED_CHAT_ID = int(os.environ.get("TELEGRAM_CHAT_ID", "0"))
KST = ZoneInfo("Asia/Seoul")

HELP_TEXT = (
    "*금융 브리핑 봇입니다* 🤖\n\n"
    "궁금한 것을 자유롭게 질문하세요!\n\n"
    "*질문 예시*\n"
    "• 오늘 나스닥 왜 떨어졌어?\n"
    "• 금리 인하되면 내 주담대 이자 줄어?\n"
    "• 지금 달러 사도 돼?\n"
    "• 비트코인이 주식이랑 같이 움직이는 이유?\n"
    "• ETF가 뭐야?\n"
    "• 인플레이션이 나한테 왜 나쁜 거야?"
)


def fetch_market_context() -> dict:
    ctx = {}

    # 코인 (CoinGecko simple — 빠름)
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/simple/price"
            "?ids=bitcoin,ethereum&vs_currencies=usd&include_24hr_change=true",
            timeout=5,
        )
        d = r.json()
        ctx["BTC"] = {"price": d["bitcoin"]["usd"], "change_24h": round(d["bitcoin"]["usd_24h_change"], 2)}
        ctx["ETH"] = {"price": d["ethereum"]["usd"], "change_24h": round(d["ethereum"]["usd_24h_change"], 2)}
    except Exception:
        pass

    # 환율
    try:
        r = requests.get("https://api.frankfurter.app/latest?from=USD&to=KRW", timeout=5)
        ctx["USD_KRW"] = round(r.json()["rates"]["KRW"], 1)
    except Exception:
        pass

    # 주요 지수 (Yahoo Finance 직접 호출 — yfinance 없이 빠름)
    for name, sym in [("S&P500", "%5EGSPC"), ("Nasdaq", "%5EIXIC")]:
        try:
            r = requests.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=1d&range=5d",
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=5,
            )
            closes = r.json()["chart"]["result"][0]["indicators"]["quote"][0]["close"]
            closes = [c for c in closes if c is not None]
            if len(closes) >= 2:
                chg = (closes[-1] - closes[-2]) / closes[-2] * 100
                ctx[name] = {"price": round(closes[-1], 2), "change_pct": round(chg, 2)}
        except Exception:
            pass

    return ctx


def ask_gemini(question: str, market_ctx: dict) -> str:
    now_kst = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    prompt = f"""당신은 한국 거주자를 위한 금융 어드바이저 챗봇입니다.
투자 초보자도 이해할 수 있도록 쉽고 친절하게 답변합니다.

현재 시각: {now_kst} KST
시장 데이터: {json.dumps(market_ctx, ensure_ascii=False)}

사용자 질문: {question}

답변 규칙:
- 한국어, 텔레그램 Markdown v1 (*굵게*, _이탤릭_)
- 5~7문장 이내 간결하게
- 전문용어는 괄호로 쉽게 설명
- 한국 생활 맥락 우선 (주담대, KOSPI, 원화, 전세 등)
- 미국 투자심리가 한국에 미치는 영향 연결
- 투자 권유 아닌 정보 제공 (필요시 "최종 판단은 본인이" 언급)
- 모르는 것은 솔직하게
"""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.4, "maxOutputTokens": 600},
    }
    try:
        r = requests.post(url, json=payload, timeout=25)
        r.raise_for_status()
        return r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception:
        return "⚠️ 답변 생성 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."


def send_message(chat_id: int, text: str):
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    r = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json=payload, timeout=10)
    if not r.json().get("ok"):
        payload.pop("parse_mode")
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json=payload, timeout=10)


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        self.send_response(200)
        self.end_headers()  # 텔레그램에 즉시 200 응답 → 재시도 방지
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            msg = body.get("message") or body.get("edited_message")
            if not msg:
                return
            chat_id = msg["chat"]["id"]
            text = msg.get("text", "").strip()
            if not text:
                return
            if chat_id != ALLOWED_CHAT_ID:
                send_message(chat_id, "⛔ 접근 권한이 없습니다.")
                return
            if text.lower() in ("/start", "/help"):
                send_message(chat_id, HELP_TEXT)
                return
            market_ctx = fetch_market_context()
            answer = ask_gemini(text, market_ctx)
            send_message(chat_id, answer)
        except Exception as e:
            print(f"[ERROR] {e}")

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Briefing bot webhook is running.")

    def log_message(self, format, *args):
        pass
