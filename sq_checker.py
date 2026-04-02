"""
Singapore Airlines Flight Checker — CI/GitHub Actions version
BOM → SFO | April 2, 2026
Alerts via Email and/or Telegram
No looping — runs once per GHA schedule trigger
"""

import asyncio, os, re, smtplib, sys, requests
from email.mime.text import MIMEText
from playwright.async_api import async_playwright, TimeoutError as PWTimeout

# ─── CONFIG ───────────────────────────────────────────────────────────────────
ORIGIN      = "BOM"
DESTINATION = "SFO"
DEP_DATE    = "02 Apr 2026"
URL_DATE    = "02042026"

SEARCH_URL = (
    "https://www.singaporeair.com/booking/flightSearch"
    f"?tripType=O&fromCity={ORIGIN}&toCity={DESTINATION}"
    f"&departDate={URL_DATE}&adults=1&children=0&infants=0&cabinClass=Y"
)

# ── Read from GitHub Secrets (set as env vars in the workflow) ────────────────
ALERT_EMAIL_TO      = os.getenv("ALERT_EMAIL_TO", "")
ALERT_EMAIL_FROM    = os.getenv("ALERT_EMAIL_FROM", "")
ALERT_EMAIL_APP_PWD = os.getenv("ALERT_EMAIL_APP_PWD", "")
TELEGRAM_BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID    = os.getenv("TELEGRAM_CHAT_ID", "")


# ─── ALERTS ───────────────────────────────────────────────────────────────────

def send_email(subject, body):
    if not ALERT_EMAIL_TO:
        return
    try:
        msg = MIMEText(body)
        msg["Subject"], msg["From"], msg["To"] = subject, ALERT_EMAIL_FROM, ALERT_EMAIL_TO
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(ALERT_EMAIL_FROM, ALERT_EMAIL_APP_PWD)
            s.send_message(msg)
        print("✅ Email sent!")
    except Exception as e:
        print(f"⚠ Email failed: {e}")


def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        r = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML"
        }, timeout=10)
        r.raise_for_status()
        print("✅ Telegram sent!")
    except Exception as e:
        print(f"⚠ Telegram failed: {e}")


def alert(flights):
    body = (
        f"✈ Singapore Airlines flights found!\n"
        f"{ORIGIN} → {DESTINATION} | {DEP_DATE}\n\n"
        + "\n\n---\n\n".join(flights[:5])
        + f"\n\nBook now: {SEARCH_URL}"
    )
    print("\n" + "═" * 60)
    print("🚨 FLIGHTS FOUND!")
    print(body)
    print("═" * 60)
    send_email(f"✈ SQ Flights Found: {ORIGIN}→{DESTINATION}", body)
    send_telegram(body[:4096])  # Telegram max message length


# ─── SCRAPER ──────────────────────────────────────────────────────────────────

async def check_flights():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/123.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
            locale="en-IN",
        )
        page = await context.new_page()

        print(f"→ Navigating to SQ search...")
        try:
            await page.goto(SEARCH_URL, wait_until="networkidle", timeout=50_000)
        except PWTimeout:
            print("⚠ Page load timed out.")
            await page.screenshot(path="screenshot.png")
            await browser.close()
            return None

        await page.wait_for_timeout(7_000)  # let Angular render

        body_text = (await page.inner_text("body")).lower()

        # Screenshot always saved (uploaded as GHA artifact)
        await page.screenshot(path="screenshot.png", full_page=False)

        if "access denied" in body_text or "captcha" in body_text:
            print("⚠ Bot detection triggered.")
            await browser.close()
            return None

        if "no flights available" in body_text or "no result" in body_text:
            await browser.close()
            return []

        # Try card selectors
        flights = []
        for sel in [
            ".flight-result-card",
            ".flightResult",
            "[class*='flight-result']",
            "[class*='flightResult']",
            ".result-item",
        ]:
            cards = await page.query_selector_all(sel)
            if cards:
                print(f"Found {len(cards)} card(s) via: {sel}")
                for card in cards:
                    txt = (await card.inner_text()).strip()
                    if txt:
                        flights.append(txt[:400])
                break

        # Fallback: regex on raw page text
        if not flights:
            times  = re.findall(r'\b\d{2}:\d{2}\b', body_text)
            prices = re.findall(r'(?:usd|sgd|inr)\s*[\d,]+(?:\.\d{2})?', body_text)
            if times or prices:
                print("Fallback: extracted times/prices from page text")
                flights = [f"Departure times: {times[:10]}\nPrices: {prices[:5]}"]

        await browser.close()
        return flights


# ─── MAIN ─────────────────────────────────────────────────────────────────────

async def main():
    print(f"SQ Checker | {ORIGIN}→{DESTINATION} | {DEP_DATE}")
    result = await check_flights()

    if result is None:
        print("Check inconclusive (bot block or timeout).")
        sys.exit(1)             # non-zero exit marks the GHA step as failed
    elif len(result) == 0:
        print("No SQ flights found this run.")
    else:
        alert(result)


if __name__ == "__main__":
    asyncio.run(main())
