"""Tests for the Wealth Advisory Agent.

Run from the project folder:  python3 -m unittest discover -s tests -t . -v
Uses only the standard library plus Flask.
"""

import json
import os
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

from werkzeug.serving import make_server

import app as wealth_app
import scoring

SAMPLE = {
    "age": 34, "retirement_age": 62, "monthly_income": 5500, "monthly_expenses": 3200,
    "monthly_debt_payments": 600, "monthly_investment": 400, "liquid_savings": 9000,
    "investments": 42000, "total_debt": 38000, "risk_tolerance": "medium",
    "has_health_insurance": True, "has_life_insurance": False,
}
# Hand-calculated expected component points for SAMPLE (see scoring methodology).
SAMPLE_EXPECTED = {
    "savings_rate": 20.0,     # surplus 1700 / 5500 = 30.9% >= 20%
    "emergency_fund": 7.9,    # 9000 / 3800 = 2.37 months -> 2.37/6 * 20
    "debt_burden": 19.4,      # DTI 10.9% -> 20 * (0.43-0.109)/0.33
    "net_worth": 1.3,         # 13000 / (2.25 x 66000) * 15
    "retirement": 7.3,        # ~375.5k projected / 768k needed * 15
    "protection": 5.0,
}

PERFECT = {
    "age": 45, "retirement_age": 65, "monthly_income": 10000, "monthly_expenses": 3000,
    "monthly_debt_payments": 500, "monthly_investment": 3000, "liquid_savings": 100000,
    "investments": 1500000, "total_debt": 0, "risk_tolerance": "high",
    "has_health_insurance": True, "has_life_insurance": True,
}


def post(client, path, payload):
    return client.post(path, json=payload)


class BaseCase(unittest.TestCase):
    def setUp(self):
        wealth_app.app.config["TESTING"] = True
        self.client = wealth_app.app.test_client()
        self._env = mock.patch.dict(os.environ, {}, clear=False)
        self._env.start()
        os.environ.pop("ANTHROPIC_API_KEY", None)

    def tearDown(self):
        self._env.stop()


class PageTests(BaseCase):
    def test_index_has_both_main_buttons_and_no_react(self):
        html = self.client.get("/").get_data(as_text=True)
        self.assertIn("Analyze My Finances", html)
        self.assertIn("Get AI Wealth Advice", html)
        self.assertNotIn("react", html.lower())

    def test_static_assets_served(self):
        for path in ("/static/app.js", "/static/style.css"):
            r = self.client.get(path)
            self.assertEqual(r.status_code, 200, path)
            r.close()

    def test_front_end_calls_the_flask_endpoints_and_handles_failures(self):
        with open("static/app.js", encoding="utf-8") as fh:
            js = fh.read()
        for needle in ("/api/analyze", "/api/advice", "/api/calc/", "/api/methodology", "catch", "localStorage", "AbortController"):
            self.assertIn(needle, js)

    def test_ping(self):
        self.assertEqual(self.client.get("/api/ping").get_json()["ok"], True)


class ScoringTests(BaseCase):
    def test_weights_sum_to_100(self):
        self.assertEqual(sum(scoring.WEIGHTS.values()), 100)

    def test_sample_profile_matches_hand_calculation(self):
        r = post(self.client, "/api/analyze", SAMPLE)
        self.assertEqual(r.status_code, 200)
        a = r.get_json()["analysis"]
        got = {c["key"]: c["points"] for c in a["components"]}
        for key, expected in SAMPLE_EXPECTED.items():
            self.assertAlmostEqual(got[key], expected, delta=0.15, msg=key)
        self.assertAlmostEqual(a["score"], 60.9, delta=0.4)
        self.assertEqual(a["grade"], "Fair")  # bands: 80 Excellent, 65 Good, 50 Fair, 35 Needs attention
        self.assertAlmostEqual(a["score"], sum(got.values()), places=1)

    def test_grade_band_boundaries(self):
        for score, grade in ((100, "Excellent"), (80, "Excellent"), (79.9, "Good"), (65, "Good"), (64.9, "Fair"),
                             (50, "Fair"), (49.9, "Needs attention"), (35, "Needs attention"), (34.9, "Critical"), (0, "Critical")):
            self.assertEqual(scoring.grade_for(score), grade, score)

    def test_perfect_profile_scores_100(self):
        a = post(self.client, "/api/analyze", PERFECT).get_json()["analysis"]
        self.assertEqual(a["score"], 100.0)
        self.assertEqual(a["grade"], "Excellent")
        g = post(self.client, "/api/analyze", PERFECT).get_json()["guidance"]
        self.assertEqual(g["recommendations"], [])

    def test_deficit_and_heavy_debt_profile_scores_low(self):
        bad = dict(SAMPLE, monthly_expenses=5000, monthly_debt_payments=2500, liquid_savings=0,
                   investments=0, monthly_investment=0, total_debt=300000,
                   has_health_insurance=False, has_life_insurance=False)
        r = post(self.client, "/api/analyze", bad).get_json()
        a = r["analysis"]
        got = {c["key"]: c["points"] for c in a["components"]}
        self.assertEqual(got["savings_rate"], 0)
        self.assertEqual(got["emergency_fund"], 0)
        self.assertEqual(got["debt_burden"], 0)
        self.assertEqual(got["net_worth"], 0)
        self.assertEqual(got["protection"], 0)
        self.assertEqual(a["grade"], "Critical")
        self.assertTrue(r["guidance"]["recommendations"])
        top = r["guidance"]["recommendations"][0]
        self.assertEqual(top["title"], "Spending exceeds income")
        self.assertIn("2,000", top["action"])  # monthly shortfall: 5500 - 5000 - 2500 = -2000

    def test_zero_outgoings_does_not_crash_and_is_json_safe(self):
        p = dict(SAMPLE, monthly_expenses=0, monthly_debt_payments=0, total_debt=0)
        r = post(self.client, "/api/analyze", p)
        self.assertEqual(r.status_code, 200)
        a = r.get_json()["analysis"]
        self.assertIsNone(a["metrics"]["emergency_months"])
        ef = [c for c in a["components"] if c["key"] == "emergency_fund"][0]
        self.assertEqual(ef["points"], 20)

    def test_scores_are_between_0_and_100(self):
        for p in (SAMPLE, PERFECT):
            s = post(self.client, "/api/analyze", p).get_json()["analysis"]["score"]
            self.assertTrue(0 <= s <= 100)

    def test_string_numbers_are_accepted(self):
        p = {k: (str(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else v) for k, v in SAMPLE.items()}
        r = post(self.client, "/api/analyze", p)
        self.assertEqual(r.status_code, 200)

    def test_methodology_endpoint_is_transparent(self):
        m = self.client.get("/api/methodology").get_json()["methodology"]
        self.assertEqual(sum(c["max"] for c in m["components"]), 100)
        for c in m["components"]:
            self.assertTrue(c["formula"] and c["target"])


class ValidationTests(BaseCase):
    def check_400(self, payload, field=None):
        r = post(self.client, "/api/analyze", payload)
        self.assertEqual(r.status_code, 400)
        body = r.get_json()
        self.assertFalse(body["ok"])
        if field:
            self.assertIn(field, body["field_errors"])
        return body

    def test_empty_object_reports_every_required_field(self):
        body = self.check_400({})
        for f in ("age", "monthly_income", "liquid_savings", "total_debt"):
            self.assertIn(f, body["field_errors"])

    def test_negative_number(self):
        self.check_400(dict(SAMPLE, liquid_savings=-5), "liquid_savings")

    def test_zero_income(self):
        self.check_400(dict(SAMPLE, monthly_income=0), "monthly_income")

    def test_text_instead_of_number(self):
        self.check_400(dict(SAMPLE, age="abc"), "age")

    def test_nan_and_infinity_strings(self):
        self.check_400(dict(SAMPLE, monthly_income="nan"), "monthly_income")
        self.check_400(dict(SAMPLE, monthly_income="inf"), "monthly_income")

    def test_boolean_as_number(self):
        self.check_400(dict(SAMPLE, age=True), "age")

    def test_retirement_before_age(self):
        self.check_400(dict(SAMPLE, retirement_age=30), "retirement_age")

    def test_bad_risk_tolerance(self):
        self.check_400(dict(SAMPLE, risk_tolerance="yolo"), "risk_tolerance")

    def test_invalid_json_and_non_object(self):
        r = self.client.post("/api/analyze", data="not json", content_type="application/json")
        self.assertEqual(r.status_code, 400)
        self.assertFalse(r.get_json()["ok"])
        r = self.client.post("/api/analyze", json=[1, 2, 3])
        self.assertEqual(r.status_code, 400)

    def test_wrong_method_and_unknown_route_return_json(self):
        r = self.client.get("/api/analyze")
        self.assertEqual(r.status_code, 405)
        self.assertFalse(r.get_json()["ok"])
        r = self.client.get("/api/nope")
        self.assertEqual(r.status_code, 404)
        self.assertFalse(r.get_json()["ok"])

    def test_oversized_body_rejected(self):
        r = self.client.post("/api/analyze", data="x" * (70 * 1024), content_type="application/json")
        self.assertEqual(r.status_code, 413)
        self.assertFalse(r.get_json()["ok"])


class AdviceTests(BaseCase):
    def test_without_api_key_falls_back_to_rules(self):
        r = post(self.client, "/api/advice", SAMPLE)
        self.assertEqual(r.status_code, 200)
        b = r.get_json()
        self.assertEqual(b["source"], "rule-based")
        self.assertIn("ANTHROPIC_API_KEY", b["notice"])
        self.assertIsNone(b["advice_text"])
        self.assertTrue(b["guidance"]["recommendations"])
        self.assertAlmostEqual(b["analysis"]["score"], 60.9, delta=0.4)

    def test_ai_text_is_returned_when_available(self):
        with mock.patch.object(wealth_app, "call_claude", return_value="Do X, then Y.") as m:
            b = post(self.client, "/api/advice", SAMPLE).get_json()
        self.assertEqual(b["source"], "claude")
        self.assertEqual(b["advice_text"], "Do X, then Y.")
        self.assertIsNone(b["notice"])
        profile, analysis = m.call_args.args
        self.assertAlmostEqual(analysis["score"], 60.9, delta=0.4)
        self.assertEqual(profile["age"], 34)

    def test_ai_failure_falls_back(self):
        with mock.patch.object(wealth_app, "call_claude", side_effect=wealth_app.AIUnavailable("The AI service returned HTTP 529.")):
            b = post(self.client, "/api/advice", SAMPLE).get_json()
        self.assertEqual(b["source"], "rule-based")
        self.assertIn("529", b["notice"])

    def test_network_error_to_provider_falls_back(self):
        os.environ["ANTHROPIC_API_KEY"] = "test-key"
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("down")):
            b = post(self.client, "/api/advice", SAMPLE).get_json()
        self.assertEqual(b["source"], "rule-based")
        self.assertIn("reach", b["notice"].lower())

    def test_provider_http_error_falls_back(self):
        os.environ["ANTHROPIC_API_KEY"] = "test-key"
        err = urllib.error.HTTPError("https://x", 401, "unauthorized", {}, None)
        with mock.patch("urllib.request.urlopen", side_effect=err):
            b = post(self.client, "/api/advice", SAMPLE).get_json()
        self.assertEqual(b["source"], "rule-based")
        self.assertIn("401", b["notice"])

    def test_provider_success_path_parses_text_blocks(self):
        os.environ["ANTHROPIC_API_KEY"] = "test-key"
        fake = mock.MagicMock()
        fake.__enter__.return_value = fake
        fake.read.return_value = json.dumps({"content": [{"type": "text", "text": "Hello advice"}]}).encode()
        with mock.patch("urllib.request.urlopen", return_value=fake) as uo:
            b = post(self.client, "/api/advice", SAMPLE).get_json()
        self.assertEqual(b["source"], "claude")
        self.assertEqual(b["advice_text"], "Hello advice")
        sent = uo.call_args.args[0]
        self.assertEqual(sent.get_header("X-api-key"), "test-key")
        self.assertEqual(json.loads(sent.data)["model"], wealth_app.ANTHROPIC_MODEL)

    def test_invalid_input_never_reaches_the_ai(self):
        with mock.patch.object(wealth_app, "call_claude") as m:
            r = post(self.client, "/api/advice", dict(SAMPLE, age=-1))
        self.assertEqual(r.status_code, 400)
        m.assert_not_called()


class CalculatorTests(BaseCase):
    def test_compound_known_value(self):
        r = post(self.client, "/api/calc/compound", {"initial": 1000, "monthly": 0, "rate": 12, "years": 1}).get_json()
        self.assertAlmostEqual(r["result"]["final_balance"], 1126.83, places=2)
        self.assertEqual(len(r["result"]["schedule"]), 1)

    def test_compound_with_contributions_zero_rate(self):
        r = post(self.client, "/api/calc/compound", {"initial": 100, "monthly": 50, "rate": 0, "years": 2}).get_json()
        self.assertEqual(r["result"]["final_balance"], 1300)
        self.assertEqual(r["result"]["interest_earned"], 0)

    def test_loan_known_value(self):
        r = post(self.client, "/api/calc/loan", {"principal": 100000, "rate": 6, "years": 30}).get_json()
        self.assertAlmostEqual(r["result"]["monthly_payment"], 599.55, places=2)
        self.assertAlmostEqual(r["result"]["total_interest"], 115838.19, delta=1)

    def test_loan_zero_interest(self):
        r = post(self.client, "/api/calc/loan", {"principal": 12000, "rate": 0, "years": 1}).get_json()
        self.assertEqual(r["result"]["monthly_payment"], 1000)
        self.assertEqual(r["result"]["total_interest"], 0)

    def test_goal(self):
        r = post(self.client, "/api/calc/goal", {"target": 1200, "current": 0, "monthly": 100, "rate": 0}).get_json()
        self.assertEqual(r["result"]["months"], 12)
        r = post(self.client, "/api/calc/goal", {"target": 100, "current": 100, "monthly": 0, "rate": 0}).get_json()
        self.assertEqual(r["result"]["months"], 0)
        r = post(self.client, "/api/calc/goal", {"target": 1e6, "current": 0, "monthly": 0, "rate": 0}).get_json()
        self.assertFalse(r["result"]["reached"])

    def test_calculator_validation_and_unknown(self):
        r = post(self.client, "/api/calc/loan", {"principal": "", "rate": -1, "years": 0})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(set(r.get_json()["field_errors"]), {"principal", "rate", "years"})
        r = post(self.client, "/api/calc/bitcoin", {})
        self.assertEqual(r.status_code, 404)
        self.assertFalse(r.get_json()["ok"])


class LiveServerTests(unittest.TestCase):
    """Real HTTP against a running Flask (werkzeug) server, not just the test client."""

    @classmethod
    def setUpClass(cls):
        os.environ.pop("ANTHROPIC_API_KEY", None)
        cls.server = make_server("127.0.0.1", 0, wealth_app.app, threaded=True)
        cls.port = cls.server.server_port
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join(timeout=5)

    def url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    def request(self, path, payload=None):
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(self.url(path), data=data, headers={"Content-Type": "application/json"},
                                     method="POST" if payload is not None else "GET")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def test_page_and_assets_over_http(self):
        status, body = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn(b"Analyze My Finances", body)
        self.assertEqual(self.request("/static/app.js")[0], 200)

    def test_analyze_over_http(self):
        status, body = self.request("/api/analyze", SAMPLE)
        self.assertEqual(status, 200)
        self.assertAlmostEqual(json.loads(body)["analysis"]["score"], 60.9, delta=0.4)

    def test_advice_over_http_falls_back_without_key(self):
        status, body = self.request("/api/advice", SAMPLE)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["source"], "rule-based")

    def test_validation_error_over_http(self):
        status, body = self.request("/api/analyze", {"age": "x"})
        self.assertEqual(status, 400)
        self.assertIn("age", json.loads(body)["field_errors"])

    def test_calculator_over_http(self):
        status, body = self.request("/api/calc/loan", {"principal": 100000, "rate": 6, "years": 30})
        self.assertEqual(status, 200)
        self.assertAlmostEqual(json.loads(body)["result"]["monthly_payment"], 599.55, places=2)


if __name__ == "__main__":
    unittest.main()
