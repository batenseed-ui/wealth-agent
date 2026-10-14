"""Standalone financial calculators used by /api/calc/<kind>."""

from __future__ import annotations

import math


def _num(data: dict, name: str, label: str, lo: float, hi: float, errors: dict, default=None):
    raw = data.get(name, default)
    if raw is None or (isinstance(raw, str) and raw.strip() == ""):
        errors[name] = f"{label} is required."
        return None
    if isinstance(raw, bool):
        errors[name] = f"{label} must be a number."
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        errors[name] = f"{label} must be a number."
        return None
    if math.isnan(value) or math.isinf(value) or value < lo or value > hi:
        errors[name] = f"{label} must be between {lo:g} and {hi:g}."
        return None
    return value


def compound(data: dict):
    errors: dict[str, str] = {}
    initial = _num(data, "initial", "Starting amount", 0, 1e11, errors)
    monthly = _num(data, "monthly", "Monthly contribution", 0, 1e9, errors)
    rate = _num(data, "rate", "Annual return (%)", -50, 100, errors)
    years = _num(data, "years", "Years", 1, 80, errors)
    if errors:
        return None, errors
    i = rate / 100 / 12
    balance = initial
    contributed = initial
    schedule = []
    for month in range(1, int(round(years * 12)) + 1):
        balance = balance * (1 + i) + monthly
        contributed += monthly
        if month % 12 == 0:
            schedule.append({"year": month // 12, "balance": round(balance, 2), "contributed": round(contributed, 2)})
    return {
        "final_balance": round(balance, 2),
        "total_contributed": round(contributed, 2),
        "interest_earned": round(balance - contributed, 2),
        "schedule": schedule,
    }, None


def loan(data: dict):
    errors: dict[str, str] = {}
    principal = _num(data, "principal", "Loan amount", 0.01, 1e11, errors)
    rate = _num(data, "rate", "Annual interest rate (%)", 0, 100, errors)
    years = _num(data, "years", "Term (years)", 0.1, 60, errors)
    if errors:
        return None, errors
    n = int(round(years * 12))
    i = rate / 100 / 12
    payment = principal / n if i == 0 else principal * i / (1 - (1 + i) ** -n)
    total = payment * n
    return {
        "monthly_payment": round(payment, 2),
        "total_paid": round(total, 2),
        "total_interest": round(total - principal, 2),
        "months": n,
    }, None


def goal(data: dict):
    errors: dict[str, str] = {}
    target = _num(data, "target", "Goal amount", 0.01, 1e11, errors)
    current = _num(data, "current", "Saved so far", 0, 1e11, errors)
    monthly = _num(data, "monthly", "Monthly saving", 0, 1e9, errors)
    rate = _num(data, "rate", "Annual return (%)", -50, 100, errors)
    if errors:
        return None, errors
    if current >= target:
        return {"months": 0, "years": 0.0, "reached": True, "final_balance": round(current, 2)}, None
    i = rate / 100 / 12
    balance = current
    for month in range(1, 12 * 100 + 1):
        balance = balance * (1 + i) + monthly
        if balance >= target:
            return {"months": month, "years": round(month / 12, 1), "reached": True,
                    "final_balance": round(balance, 2)}, None
    return {"months": None, "years": None, "reached": False, "final_balance": round(balance, 2)}, None


CALCULATORS = {"compound": compound, "loan": loan, "goal": goal}
