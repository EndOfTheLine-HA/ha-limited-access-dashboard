"""Browser acceptance against preview_editor.py; does not connect to HA."""

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

output = Path(sys.argv[1])
output.mkdir(parents=True, exist_ok=True)
with sync_playwright() as playwright:
    browser = playwright.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 1100})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto("http://127.0.0.1:8768/editor")
    page.get_by_role("heading", name="Edit guest dashboard", exact=True).wait_for()
    page.get_by_label("Tab title", exact=True).fill("Lights")
    page.get_by_label("Tab title", exact=True).press("Tab")
    page.get_by_label("Columns", exact=True).select_option("2")
    page.get_by_role("button", name="Save dashboard", exact=True).click()
    page.get_by_role("status").filter(has_text="Saved.").wait_for()
    page.screenshot(path=str(output / "owner-editor.png"), full_page=True)
    page.reload()
    assert page.get_by_label("Tab title", exact=True).input_value() == "Lights"
    page.goto("http://127.0.0.1:8768/limited-access-dashboard/demo")
    page.get_by_role("tab", name="Lights", exact=True).wait_for()
    assert page.get_by_role("button", name="Toggle", exact=True).count() == 2
    page.wait_for_timeout(10500)
    assert all(button.is_enabled() for button in page.get_by_role("button", name="Toggle").all())
    page.get_by_role("tab", name="Sensors", exact=True).click()
    page.get_by_text("Custom HACS fixture: Room temperature: 21", exact=True).wait_for()
    page.screenshot(path=str(output / "guest-custom-card.png"), full_page=True)
    page.get_by_role("button", name="Customize appearance").click()
    assert page.get_by_role("button", name="+ Lovelace / HACS card").count() == 0
    assert page.get_by_label("Allow guest customization").count() == 0
    page.get_by_label("Tab title", exact=True).fill("Guest favorites")
    page.get_by_label("Tab title", exact=True).press("Tab")
    page.get_by_role("button", name="Save dashboard", exact=True).click()
    page.get_by_role("status").filter(has_text="Saved.").wait_for()
    page.reload()
    page.get_by_role("tab", name="Guest favorites", exact=True).wait_for()
    page.set_viewport_size({"width": 390, "height": 844})
    page.screenshot(path=str(output / "guest-mobile.png"), full_page=True)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert not errors, errors
    browser.close()
print(
    "Owner save/reload, tabs, custom module, guest customization, polling and mobile checks passed"
)
