"""End-to-end browser test (Playwright + headless Chromium) against a real Flask server.

Run from the project folder:  python3 tests/browser_test.py
Checks the two main buttons, field-error display, input persistence across reloads,
and the failed-fetch path (server down, then recovery via the Retry button).
"""

import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server

import app as wealth_app

os.environ.pop("ANTHROPIC_API_KEY", None)
PORT = 5071
BASE = f"http://127.0.0.1:{PORT}"

FIELDS = {
    "age": "34", "retirement_age": "62", "monthly_income": "5500", "monthly_expenses": "3200",
    "monthly_debt_payments": "600", "monthly_investment": "400", "liquid_savings": "9000",
    "investments": "42000", "total_debt": "38000",
}

results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition)))
    print(("PASS  " if condition else "FAIL  ") + name + (f"  [{detail}]" if detail and not condition else ""))


class Server:
    def start(self):
        self.srv = make_server("127.0.0.1", PORT, wealth_app.app, threaded=True)
        self.t = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.t.start()

    def stop(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.t.join(timeout=5)


def fill(page):
    for name, value in FIELDS.items():
        page.fill(f"#profile-form [name={name}]", value)
    page.check("#profile-form [name=has_health_insurance]")


def main():
    server = Server()
    server.start()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        console_errors = []
        page.on("pageerror", lambda e: console_errors.append(str(e)))

        page.goto(BASE)
        page.wait_for_selector("#method-body ul")  # methodology loaded from /api/methodology
        check("methodology rendered from server", page.locator("#method-body li").count() == 6)

        # --- validation: empty form -> field errors, no crash
        page.click("#btn-analyze")
        page.wait_for_selector("[data-error-for=age]:not(:empty)")
        check("empty form shows field errors", "required" in page.inner_text("[data-error-for=monthly_income]"))
        check("score card still hidden after validation error", page.locator("#score-card").is_hidden())
        check("buttons re-enabled after error", page.locator("#btn-analyze").is_enabled())

        # --- Analyze My Finances
        fill(page)
        page.click("#btn-analyze")
        page.wait_for_selector("#score-card:not([hidden])")
        check("analyze shows score 60.9", page.inner_text("#score-num") == "60.9", page.inner_text("#score-num"))
        check("analyze shows grade Fair", page.inner_text("#score-grade") == "Fair")
        check("six breakdown rows", page.locator("#breakdown-body tr").count() == 6)
        check("field errors cleared on success", page.inner_text("[data-error-for=age]") == "")
        check("advice card not shown by Analyze", page.locator("#advice-card").is_hidden())

        # --- Get AI Wealth Advice (no API key on this server -> labelled fallback)
        page.click("#btn-advice")
        page.wait_for_selector("#advice-card:not([hidden])")
        check("advice card shows fallback badge", page.inner_text("#advice-badge") == "Rule-based fallback")
        check("fallback notice explains why", "ANTHROPIC_API_KEY" in page.inner_text("#advice-notice"))
        check("advice text present", len(page.inner_text("#advice-text")) > 50)

        # --- persistence across reload
        page.reload()
        page.wait_for_selector("#method-body ul")
        check("inputs restored after reload", page.input_value("#profile-form [name=monthly_income]") == "5500")
        check("checkbox restored after reload", page.is_checked("#profile-form [name=has_health_insurance]"))

        # --- failed fetch: stop the server, click each button
        server.stop()
        page.click("#btn-analyze")
        page.wait_for_selector("#banner:not([hidden])")
        check("analyze: banner explains the connection failure", "Could not reach the server" in page.inner_text("#banner-text"))
        check("analyze: retry button offered", page.locator("#banner-retry").is_visible())
        check("analyze: inputs preserved after failure", page.input_value("#profile-form [name=age]") == "34"
              and page.input_value("#profile-form [name=total_debt]") == "38000")
        check("analyze: button usable again", page.locator("#btn-analyze").is_enabled()
              and page.inner_text("#btn-analyze") == "Analyze My Finances")

        page.click("#banner-close")
        check("banner dismissable", page.locator("#banner").is_hidden())

        page.click("#btn-advice")
        page.wait_for_selector("#banner:not([hidden])")
        check("advice: banner on connection failure", "Could not reach the server" in page.inner_text("#banner-text"))
        check("advice: button label restored", page.inner_text("#btn-advice") == "Get AI Wealth Advice")

        # --- recovery: restart server, Retry re-runs the advice request
        server.start()
        page.click("#banner-retry")
        page.wait_for_function("document.querySelector('#banner').hidden === true")
        page.wait_for_function("document.querySelector('#advice-text').textContent.length > 50")
        check("retry succeeds once server is back", page.inner_text("#score-num") == "60.9")

        # --- calculators
        page.fill("form[data-calc=loan] [name=principal]", "100000")
        page.fill("form[data-calc=loan] [name=rate]", "6")
        page.fill("form[data-calc=loan] [name=years]", "30")
        page.click("form[data-calc=loan] button[type=submit]")
        page.wait_for_selector("form[data-calc=loan] .calc-out dl")
        check("loan calculator shows 599.55", "599.55" in page.inner_text("form[data-calc=loan] .calc-out"))

        page.fill("form[data-calc=compound] [name=initial]", "1000")
        page.fill("form[data-calc=compound] [name=monthly]", "0")
        page.fill("form[data-calc=compound] [name=rate]", "12")
        page.fill("form[data-calc=compound] [name=years]", "1")
        page.click("form[data-calc=compound] button[type=submit]")
        page.wait_for_selector("form[data-calc=compound] .calc-out dl")
        # The page displays whole currency units here; the exact 1126.83 is covered by the API tests.
        check("compound calculator shows $1,127 final balance", "$1,127" in page.inner_text("form[data-calc=compound] .calc-out"),
              page.inner_text("form[data-calc=compound] .calc-out"))

        page.fill("form[data-calc=goal] [name=target]", "1200")
        page.fill("form[data-calc=goal] [name=current]", "0")
        page.fill("form[data-calc=goal] [name=monthly]", "100")
        page.fill("form[data-calc=goal] [name=rate]", "0")
        page.click("form[data-calc=goal] button[type=submit]")
        page.wait_for_selector("form[data-calc=goal] .calc-out dl")
        check("goal calculator shows 12 months", "12 months" in page.inner_text("form[data-calc=goal] .calc-out"))

        # calculator validation error
        page.fill("form[data-calc=loan] [name=years]", "0")
        page.click("form[data-calc=loan] button[type=submit]")
        page.wait_for_selector("form[data-calc=loan] [data-error-for=years]:not(:empty)")
        check("calculator shows its own field error",
              "between" in page.inner_text("form[data-calc=loan] [data-error-for=years]"),
              page.inner_text("form[data-calc=loan] [data-error-for=years]"))

        page.reload()
        check("calculator inputs persisted", page.input_value("form[data-calc=loan] [name=principal]") == "100000")

        check("no uncaught JS errors", not console_errors, "; ".join(console_errors))
        browser.close()
    server.stop()

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} browser checks passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
