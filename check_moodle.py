
from pathlib import Path
from playwright.sync_api import sync_playwright

URL = "https://fall2026.auplmscs.com/my/"
PROFILE = Path(__file__).with_name("moodle_browser_profile")

with sync_playwright() as p:
    context = p.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE),
        headless=False
    )

    page = context.pages[0] if context.pages else context.new_page()

    try:
        page.goto(URL, wait_until="domcontentloaded", timeout=60000)
    except Exception as e:
        print("Navigation message:", e)

    print("\nLog in to Moodle in the opened browser if asked.")
    print("Wait until your dashboard and courses are visible.")
    input("After the dashboard appears, press ENTER here to test it...")

    print("\nFinal URL:", page.url)
    print("Page title:", page.title())

    try:
        print("\nDashboard text:")
        print(page.locator("body").inner_text(timeout=15000)[:4000])
    except Exception as e:
        print("Could not read page:", e)

    context.close()
