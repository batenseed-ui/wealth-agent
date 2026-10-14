"""Financial health scoring for the Wealth Advisory Agent.

Everything here is deterministic and documented in METHODOLOGY so the score can
be audited: six components, fixed weights that add up to 100 points.
"""

from __future__ import annotations

import math

# --- Assumptions (shown to the user via /api/methodology) -------------------
REAL_RETURN = 0.04          # long-run investment return after inflation
SAFE_WITHDRAWAL = 0.04      # "4% rule" => need 25x annual retirement spending
RETIREMENT_SPEND_RATIO = 0.80  # retirement spending as share of today's living costs
TARGET_SAVINGS_RATE = 0.20
TARGET_EMERGENCY_MONTHS = 6
DTI_FULL_POINTS = 0.10      # debt payments <= 10% of income => full points
DTI_ZERO_POINTS = 0.43      # debt payments >= 43% of income => zero points

WEIGHTS = {
    "savings_rate": 20,
    "emergency_fund": 20,
    "debt_burden": 20,
    "net_worth": 15,
    "retirement": 15,
    "protection": 10,
}
assert sum(WEIGHTS.values()) == 100

GRADES = [(80, "Excellent"), (65, "Good"), (50, "Fair"), (35, "Needs attention"), (0, "Critical")]

NUMERIC_FIELDS = {
    # name: (label, minimum, maximum, required)
    "age": ("Age", 18, 100, True),
    "retirement_age": ("Retirement age", 19, 100, True),
    "monthly_income": ("Monthly income (after tax)", 0.01, 1e9, True),
    "monthly_expenses": ("Monthly living expenses", 0, 1e9, True),
    "monthly_debt_payments": ("Monthly debt payments", 0, 1e9, True),
    "total_debt": ("Total debt", 0, 1e11, True),
    "liquid_savings": ("Cash savings", 0, 1e11, True),
    "investments": ("Investments", 0, 1e11, True),
    "monthly_investment": ("Monthly investment contribution", 0, 1e9, True),
}
BOOL_FIELDS = ["has_health_insurance", "has_life_insurance"]
RISK_LEVELS = ("low", "medium", "high")

METHODOLOGY = {
    "summary": (
        "The score is out of 100 and is the sum of six components. Each component is "
        "a simple ratio compared with a stated target, scaled linearly and capped at "
        "its maximum points. No hidden weighting, no machine learning."
    ),
    "assumptions": {
        "real_return": f"{REAL_RETURN:.0%} per year after inflation (used in the retirement projection)",
        "safe_withdrawal_rate": f"{SAFE_WITHDRAWAL:.0%} (so the target nest egg is 25x annual retirement spending)",
        "retirement_spending": f"{RETIREMENT_SPEND_RATIO:.0%} of today's annual living expenses (debt payments excluded)",
    },
    "components": [
        {
            "key": "savings_rate", "name": "Savings rate", "max": WEIGHTS["savings_rate"],
            "formula": "rate = (income - expenses - debt payments) / income; points = min(rate / 20%, 1) x 20",
            "target": "Save 20% or more of income. A deficit scores 0.",
        },
        {
            "key": "emergency_fund", "name": "Emergency fund", "max": WEIGHTS["emergency_fund"],
            "formula": "months = cash savings / (expenses + debt payments); points = min(months / 6, 1) x 20",
            "target": "Cover 6 months of outgoings in cash.",
        },
        {
            "key": "debt_burden", "name": "Debt burden", "max": WEIGHTS["debt_burden"],
            "formula": "DTI = debt payments / income; 20 points at DTI <= 10%, 0 points at DTI >= 43%, linear in between",
            "target": "Keep debt payments at or below 10% of income.",
        },
        {
            "key": "net_worth", "name": "Net worth vs. age", "max": WEIGHTS["net_worth"],
            "formula": ("net worth = cash + investments - debt; benchmark = max(0.5, (age - 25) x 0.25) x annual income; "
                        "points = clamp(net worth / benchmark, 0, 1) x 15"),
            "target": "Reach the age-based multiple of annual income (e.g. 3.75x at 40, 8.75x at 60).",
        },
        {
            "key": "retirement", "name": "Retirement readiness", "max": WEIGHTS["retirement"],
            "formula": ("projected = investments and monthly contributions grown at 4% real for the years to retirement; "
                        "needed = 25 x (80% x annual expenses); points = min(projected / needed, 1) x 15"),
            "target": "Be on track to fund 80% of today's living costs from investments.",
        },
        {
            "key": "protection", "name": "Protection", "max": WEIGHTS["protection"],
            "formula": "5 points for health insurance + 5 points for life / income protection",
            "target": "Hold both covers.",
        },
    ],
    "grades": [{"min": m, "label": g} for m, g in GRADES],
    "limits": (
        "This is an educational estimate from the numbers you entered. It ignores taxes, "
        "property, pensions, dependants and local rules, and it is not financial advice."
    ),
}


# --- helpers ----------------------------------------------------------------
def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _status(points: float, maximum: float) -> str:
    ratio = points / maximum if maximum else 0
    if ratio >= 0.8:
        return "good"
    if ratio >= 0.5:
        return "ok"
    return "poor"


def grade_for(score: float) -> str:
    for minimum, label in GRADES:
        if score >= minimum:
            return label
    return "Critical"


def _to_bool(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def validate_profile(data):
    """Return (clean_profile, errors). errors maps field name -> message."""
    errors: dict[str, str] = {}
    clean: dict = {}
    if not isinstance(data, dict):
        return {}, {"_": "Request body must be a JSON object."}

    for name, (label, lo, hi, required) in NUMERIC_FIELDS.items():
        raw = data.get(name)
        if raw is None or (isinstance(raw, str) and raw.strip() == ""):
            if required:
                errors[name] = f"{label} is required."
            continue
        if isinstance(raw, bool):
            errors[name] = f"{label} must be a number."
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            errors[name] = f"{label} must be a number."
            continue
        if math.isnan(value) or math.isinf(value):
            errors[name] = f"{label} must be a finite number."
            continue
        if value < lo or value > hi:
            if lo > 0 and value <= 0:
                errors[name] = f"{label} must be greater than 0."
            elif value < lo:
                errors[name] = f"{label} must be at least {lo:g}."
            else:
                errors[name] = f"{label} is too large."
            continue
        clean[name] = value

    if "age" in clean and "retirement_age" in clean and clean["retirement_age"] <= clean["age"]:
        errors["retirement_age"] = "Retirement age must be greater than your current age."

    for name in BOOL_FIELDS:
        clean[name] = _to_bool(data.get(name, False))

    risk = str(data.get("risk_tolerance", "medium")).lower()
    if risk not in RISK_LEVELS:
        errors["risk_tolerance"] = "Risk tolerance must be low, medium or high."
    else:
        clean["risk_tolerance"] = risk

    return clean, errors


def project_investments(current: float, monthly: float, years: float, annual_rate: float = REAL_RETURN) -> float:
    months = int(round(years * 12))
    i = annual_rate / 12
    if months <= 0:
        return current
    if i == 0:
        return current + monthly * months
    growth = (1 + i) ** months
    return current * growth + monthly * (growth - 1) / i


def compute_metrics(p: dict) -> dict:
    income = p["monthly_income"]
    outgoings = p["monthly_expenses"] + p["monthly_debt_payments"]
    surplus = income - outgoings
    years_to_retire = p["retirement_age"] - p["age"]
    projected = project_investments(p["investments"], p["monthly_investment"], years_to_retire)
    needed = max(1.0, p["monthly_expenses"] * 12 * RETIREMENT_SPEND_RATIO / SAFE_WITHDRAWAL)
    net_worth = p["liquid_savings"] + p["investments"] - p["total_debt"]
    benchmark_multiple = max(0.5, (p["age"] - 25) * 0.25)
    return {
        "monthly_surplus": surplus,
        "savings_rate": surplus / income,
        "emergency_months": (p["liquid_savings"] / outgoings) if outgoings > 0 else float("inf"),
        "debt_to_income": p["monthly_debt_payments"] / income,
        "net_worth": net_worth,
        "net_worth_benchmark_multiple": benchmark_multiple,
        "net_worth_benchmark": benchmark_multiple * income * 12,
        "years_to_retirement": years_to_retire,
        "projected_retirement_pot": projected,
        "needed_retirement_pot": needed,
        "retirement_funded_ratio": projected / needed,
    }


def score_profile(p: dict) -> dict:
    m = compute_metrics(p)
    income = p["monthly_income"]
    W = WEIGHTS

    pts_sr = _clamp(m["savings_rate"] / TARGET_SAVINGS_RATE) * W["savings_rate"]
    pts_ef = _clamp(m["emergency_months"] / TARGET_EMERGENCY_MONTHS) * W["emergency_fund"]
    pts_dti = _clamp((DTI_ZERO_POINTS - m["debt_to_income"]) / (DTI_ZERO_POINTS - DTI_FULL_POINTS)) * W["debt_burden"]
    pts_nw = _clamp(m["net_worth"] / m["net_worth_benchmark"]) * W["net_worth"]
    pts_ret = _clamp(m["retirement_funded_ratio"]) * W["retirement"]
    pts_prot = (5 if p["has_health_insurance"] else 0) + (5 if p["has_life_insurance"] else 0)

    em = m["emergency_months"]
    em_text = "no outgoings" if math.isinf(em) else f"{em:.1f} months"
    rows = [
        ("savings_rate", pts_sr, f"Saving {m['savings_rate'] * 100:.1f}% of income (target {TARGET_SAVINGS_RATE:.0%})"),
        ("emergency_fund", pts_ef, f"Cash covers {em_text} of outgoings (target {TARGET_EMERGENCY_MONTHS})"),
        ("debt_burden", pts_dti, f"Debt payments are {m['debt_to_income'] * 100:.1f}% of income (full points at {DTI_FULL_POINTS:.0%} or less)"),
        ("net_worth", pts_nw, f"Net worth is {m['net_worth'] / (income * 12):.2f}x annual income "
                              f"vs. a {m['net_worth_benchmark_multiple']:.2f}x benchmark for your age"),
        ("retirement", pts_ret, f"Projected pot covers {m['retirement_funded_ratio'] * 100:.0f}% of the amount needed"),
        ("protection", pts_prot, f"Health insurance: {'yes' if p['has_health_insurance'] else 'no'}; "
                                 f"life/income protection: {'yes' if p['has_life_insurance'] else 'no'}"),
    ]
    names = {c["key"]: c["name"] for c in METHODOLOGY["components"]}
    components = [
        {
            "key": key,
            "name": names[key],
            "points": round(points, 1),
            "max": W[key],
            "status": _status(points, W[key]),
            "detail": detail,
        }
        for key, points, detail in rows
    ]
    total = round(sum(c["points"] for c in components), 1)
    # JSON cannot carry infinity.
    if math.isinf(m["emergency_months"]):
        m["emergency_months"] = None
    return {
        "score": total,
        "grade": grade_for(total),
        "components": components,
        "metrics": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in m.items()},
    }


# --- rule-based advice (also the fallback when the AI call fails) -----------
def rule_based_advice(p: dict, analysis: dict) -> dict:
    m = analysis["metrics"]
    comps = {c["key"]: c for c in analysis["components"]}
    items: list[dict] = []

    def add(key: str, title: str, action: str):
        c = comps[key]
        items.append({
            "area": c["name"],
            "title": title,
            "action": action,
            "points_available": round(c["max"] - c["points"], 1),
        })

    if comps["savings_rate"]["status"] != "good":
        target = p["monthly_income"] * TARGET_SAVINGS_RATE
        gap = target - m["monthly_surplus"]
        if m["monthly_surplus"] < 0:
            add("savings_rate", "Spending exceeds income",
                f"You are short by {abs(m['monthly_surplus']):,.0f} a month. Trim expenses or raise income "
                f"before anything else; the 20% target would be {target:,.0f} a month.")
        else:
            add("savings_rate", "Raise your savings rate",
                f"You keep {m['monthly_surplus']:,.0f} a month. Finding another {gap:,.0f} gets you to the 20% target.")
    if comps["emergency_fund"]["status"] != "good":
        outgoings = p["monthly_expenses"] + p["monthly_debt_payments"]
        short = max(0.0, outgoings * TARGET_EMERGENCY_MONTHS - p["liquid_savings"])
        add("emergency_fund", "Build the emergency fund",
            f"A 6-month buffer is {outgoings * TARGET_EMERGENCY_MONTHS:,.0f}; you are {short:,.0f} short. "
            "Keep it in an easy-access account, separate from investments.")
    if comps["debt_burden"]["status"] != "good":
        excess = max(0.0, p["monthly_debt_payments"] - p["monthly_income"] * DTI_FULL_POINTS)
        add("debt_burden", "Reduce debt payments",
            f"Debt payments are {m['debt_to_income'] * 100:.0f}% of income. Getting to 10% means cutting about "
            f"{excess:,.0f} a month: pay highest-interest balances first or refinance if cheaper.")
    if comps["retirement"]["status"] != "good":
        gap = max(0.0, m["needed_retirement_pot"] - m["projected_retirement_pot"])
        yrs = max(1, int(m["years_to_retirement"]))
        months = yrs * 12
        i = REAL_RETURN / 12
        factor = ((1 + i) ** months - 1) / i
        extra = gap / factor if factor else 0
        add("retirement", "Close the retirement gap",
            f"Projected pot is {m['projected_retirement_pot']:,.0f} against {m['needed_retirement_pot']:,.0f} needed. "
            f"Adding roughly {extra:,.0f} a month to investments closes the gap at a {REAL_RETURN:.0%} real return.")
    if comps["net_worth"]["status"] == "poor":
        add("net_worth", "Grow net worth",
            f"Net worth is {m['net_worth']:,.0f} against an age benchmark of {m['net_worth_benchmark']:,.0f}. "
            "Paying down debt and steady investing are the two levers.")
    if comps["protection"]["points"] < comps["protection"]["max"]:
        missing = []
        if not p["has_health_insurance"]:
            missing.append("health insurance")
        if not p["has_life_insurance"]:
            missing.append("life / income protection")
        add("protection", "Close protection gaps", "Missing: " + " and ".join(missing) + ".")

    items.sort(key=lambda i: i["points_available"], reverse=True)
    risk_note = {
        "low": "With a low risk tolerance, favour diversified bond and cash-like funds for money needed within 5 years.",
        "medium": "With a medium risk tolerance, a diversified mix of low-cost equity and bond funds is a common approach.",
        "high": "With a high risk tolerance, a higher equity share suits long horizons, but keep the emergency fund in cash.",
    }[p["risk_tolerance"]]
    if items:
        summary = (f"Your score is {analysis['score']}/100 ({analysis['grade']}). "
                   f"The biggest opportunity is: {items[0]['title'].lower()}.")
    else:
        summary = f"Your score is {analysis['score']}/100 ({analysis['grade']}). Every area is on target; keep it up."
    return {"summary": summary, "recommendations": items, "risk_note": risk_note}
