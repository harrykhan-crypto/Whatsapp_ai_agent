
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE_URL = "https://fall2026.auplmscs.com/my/"
PROFILE_DIR = Path(__file__).with_name("moodle_browser_profile")

with sync_playwright() as p:
    context = p.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE_DIR),
        headless=False
    )

    page = context.pages[0] if context.pages else context.new_page()
    page.goto(BASE_URL, wait_until="domcontentloaded")

    print("\nA browser window has opened.")
    print("Log in to Moodle manually in that window.")
    print("Complete any verification the website requests.")
    input("\nAfter your Moodle dashboard is visible, press ENTER here... ")

    print("\n--- MOODLE SESSION CHECK ---")
    print("Page title:", page.title())
    print("Current URL:", page.url)

    print("\n--- VISIBLE PAGE LINKS ---")
    links = page.locator("a").all()

    for link in links[:100]:
        try:
            name = " ".join(link.inner_text(timeout=1000).split())
            href = link.get_attribute("href")

            if name and href:
                print(f"{name} -> {href}")
        except Exception:
            continue

    input("\nPress ENTER to close the browser...")
    context.close()
