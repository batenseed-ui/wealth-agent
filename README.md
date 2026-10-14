# Wealth Advisory Agent

A small web app that scores your financial health, runs three financial calculators, and gives written
guidance. Front end: plain HTML, CSS and JavaScript (no React). Back end: Python Flask.

## Run it

```bash
pip install -r requirements.txt
python3 app.py            # http://127.0.0.1:5000  (set PORT=... to change)
```

### Optional: real AI advice

Set an Anthropic API key before starting the server:

```bash
export ANTHROPIC_API_KEY=your-key
export WEALTH_AGENT_MODEL=claude-sonnet-5-5   # optional, this is the default
python3 app.py
```

Without a key, or if the AI call fails or times out, **Get AI Wealth Advice** still works: it shows
rule-based guidance computed from your numbers and labels it "Rule-based fallback" with the reason.

## What the buttons do

| Button | Endpoint | Result |
| --- | --- | --- |
| Analyze My Finances | `POST /api/analyze` | score, per-component points, prioritised actions |
| Get AI Wealth Advice | `POST /api/advice` | the same score plus Claude's written advice (or labelled fallback) |

Other endpoints: `GET /api/ping`, `GET /api/methodology`, `POST /api/calc/<compound|loan|goal>`.

## How the score works (out of 100)

| Component | Points | Full points when | How it is calculated |
| --- | --- | --- | --- |
| Savings rate | 20 | you save 20%+ of income | `(income - expenses - debt payments) / income`, `min(rate / 20%, 1) x 20` |
| Emergency fund | 20 | cash covers 6 months | `cash / (expenses + debt payments)`, `min(months / 6, 1) x 20` |
| Debt burden | 20 | debt payments <= 10% of income | linear from 20 points at 10% to 0 points at 43% |
| Net worth vs. age | 15 | net worth reaches the age benchmark | `(cash + investments - debt) / (max(0.5, (age - 25) x 0.25) x annual income)`, capped at 1, x 15 |
| Retirement readiness | 15 | projected pot covers the need | pot = investments + monthly contributions grown at 4% real to retirement age; need = 25 x (80% x annual living expenses); `min(pot / need, 1) x 15` |
| Protection | 10 | both covers held | 5 for health insurance + 5 for life / income protection |

Grades: **Excellent** 80+, **Good** 65+, **Fair** 50+, **Needs attention** 35+, **Critical** below 35.

Limits: it uses only what you enter, ignores taxes, property, pensions and dependants, and the 4% real
return and 4% withdrawal rate are simple planning assumptions. It is educational, not financial advice.
The same method is shown on the page and served at `/api/methodology`.

## Tests

```bash
python3 -m unittest discover -s tests -t . -v   # 42 API, scoring and live-HTTP tests (stdlib only)
python3 tests/browser_test.py                    # 28 headless-Chromium checks (needs playwright)
```

Not covered by automated tests: a real call to the Anthropic API (it needs your key; the request and
response handling is tested with the network call mocked).
