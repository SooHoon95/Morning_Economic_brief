#!/usr/bin/env python3
"""
텔레그램 웹훅 등록 스크립트 — Vercel 배포 후 1회만 실행
사용법: python setup_webhook.py https://YOUR_PROJECT.vercel.app
"""
import sys
import requests
from dotenv import load_dotenv
import os

load_dotenv()

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]

if len(sys.argv) < 2:
    print("사용법: python setup_webhook.py https://YOUR_PROJECT.vercel.app")
    sys.exit(1)

vercel_url = sys.argv[1].rstrip("/")
webhook_url = f"{vercel_url}/api/webhook"

r = requests.get(
    f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook",
    params={"url": webhook_url, "allowed_updates": ["message"]},
    timeout=10,
)
result = r.json()

if result.get("ok"):
    print(f"✅ 웹훅 등록 완료: {webhook_url}")
else:
    print(f"❌ 실패: {result}")
