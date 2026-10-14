"""Wealth Advisory Agent - Flask backend.

Endpoints
  GET  /                  the web app
  GET  /api/ping          health check
  GET  /api/methodology   the full, transparent scoring method
  POST /api/analyze       validate inputs, return score + metrics ("Analyze My Finances")
  POST /api/advice        score + AI advice, with rule-based fallback ("Get AI Wealth Advice")
  POST /api/calc/<kind>   compound | loan | goal calculators
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from flask import Flask, jsonify, render_template, request

import calculators
import scoring
from envfile import load_env_file

# Load keys from a .env file next to this script BEFORE the settings below read the environment.
load_env_file(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_MODEL = os.environ.get("WEALTH_AGENT_MODEL", "claude-sonnet-5-5")
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
AI_TIMEOUT_SECONDS = 30

SYSTEM_PROMPT = (
    "You are a careful personal-finance coach. You get a user's numbers and a deterministic financial "
    "health score. Give clear, specific, prioritised guidance in plain language (max ~350 words): a short "
    "summary, then the 3 most valuable actions with concrete amounts. Explain trade-offs, do not promise "
    "returns, and do not recommend specific securities. End with one sentence saying this is educational "
    "and not personalised financial advice."
)


class AIUnavailable(Exception):
    """Raised when the AI provider cannot be used; the caller falls back to rules."""


def call_claude(profile: dict, analysis: dict) -> str:
    """Ask Claude for narrative advice. Raises AIUnavailable on any problem."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise AIUnavailable("No AI key is configured on the server (set GEMINI_API_KEY or ANTHROPIC_API_KEY).")
    user_content = json.dumps({"profile": profile, "score": analysis}, default=str)
    body = json.dumps({
        "model": ANTHROPIC_MODEL,
        "max_tokens": 900,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": user_content}],
    }).encode()
    req = urllib.request.Request(
        ANTHROPIC_URL,
        data=body,
        headers={"content-type": "application/json", "x-api-key": api_key, "anthropic-version": "2023-06-01"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=AI_TIMEOUT_SECONDS) as resp:
            payload = json.load(resp)
        text = "".join(b.get("text", "") for b in payload.get("content", []) if b.get("type") == "text").strip()
    except urllib.error.HTTPError as exc:
        raise AIUnavailable(f"The AI service returned HTTP {exc.code}.") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise AIUnavailable("Could not reach the AI service.") from exc
    except (ValueError, AttributeError) as exc:
        raise AIUnavailable("The AI service returned an unreadable response.") from exc
    if not text:
        raise AIUnavailable("The AI service returned an empty answer.")
    return text


def call_gemini(profile: dict, analysis: dict) -> str:
    """Ask Google Gemini for narrative advice. Raises AIUnavailable on any problem."""
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise AIUnavailable("No GEMINI_API_KEY is configured on the server.")
    user_content = json.dumps({"profile": profile, "score": analysis}, default=str)
    body = json.dumps({
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": user_content}]}],
        "generationConfig": {"maxOutputTokens": 1200},
    }).encode()
    req = urllib.request.Request(
        GEMINI_URL.format(model=GEMINI_MODEL),
        data=body,
        headers={"content-type": "application/json", "x-goog-api-key": api_key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=AI_TIMEOUT_SECONDS) as resp:
            payload = json.load(resp)
        parts = payload["candidates"][0]["content"]["parts"]
        text = "".join(part.get("text", "") for part in parts).strip()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise AIUnavailable(
                f"The AI service returned HTTP 404: Gemini model '{GEMINI_MODEL}' was not found or is retired. "
                "Set GEMINI_MODEL to a current model name (see README)."
            ) from exc
        raise AIUnavailable(f"The AI service returned HTTP {exc.code}.") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise AIUnavailable("Could not reach the AI service.") from exc
    except (ValueError, AttributeError, KeyError, IndexError, TypeError) as exc:
        raise AIUnavailable("The AI service returned an unreadable or blocked response.") from exc
    if not text:
        raise AIUnavailable("The AI service returned an empty answer.")
    return text


def pick_provider() -> str:
    """AI_PROVIDER=gemini|anthropic wins; otherwise Gemini if only a Gemini key is set."""
    choice = os.environ.get("AI_PROVIDER", "").strip().lower()
    if choice in ("gemini", "anthropic"):
        return choice
    if os.environ.get("GEMINI_API_KEY", "").strip() and not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return "gemini"
    return "anthropic"


def call_ai(profile: dict, analysis: dict):
    """Return (source_label, text). Raises AIUnavailable."""
    if pick_provider() == "gemini":
        return "gemini", call_gemini(profile, analysis)
    return "claude", call_claude(profile, analysis)


def _json_body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return None, (jsonify(ok=False, error="Request body must be a JSON object."), 400)
    return data, None


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/ping")
def ping():
    return jsonify(ok=True, service="wealth-advisory-agent")


@app.get("/api/methodology")
def methodology():
    return jsonify(ok=True, methodology=scoring.METHODOLOGY)


@app.post("/api/analyze")
def analyze():
    data, err = _json_body()
    if err:
        return err
    profile, errors = scoring.validate_profile(data)
    if errors:
        return jsonify(ok=False, error="Please fix the highlighted fields.", field_errors=errors), 400
    analysis = scoring.score_profile(profile)
    return jsonify(ok=True, analysis=analysis, guidance=scoring.rule_based_advice(profile, analysis))


@app.post("/api/advice")
def advice():
    data, err = _json_body()
    if err:
        return err
    profile, errors = scoring.validate_profile(data)
    if errors:
        return jsonify(ok=False, error="Please fix the highlighted fields.", field_errors=errors), 400
    analysis = scoring.score_profile(profile)
    guidance = scoring.rule_based_advice(profile, analysis)
    try:
        source, text = call_ai(profile, analysis)
        notice = None
    except AIUnavailable as exc:
        text = None
        source = "rule-based"
        notice = f"{exc} Showing rule-based guidance computed from your numbers instead."
    return jsonify(ok=True, source=source, notice=notice, advice_text=text, analysis=analysis, guidance=guidance)


@app.post("/api/calc/<kind>")
def calc(kind: str):
    fn = calculators.CALCULATORS.get(kind)
    if fn is None:
        return jsonify(ok=False, error=f"Unknown calculator '{kind}'."), 404
    data, err = _json_body()
    if err:
        return err
    result, errors = fn(data)
    if errors:
        return jsonify(ok=False, error="Please fix the highlighted fields.", field_errors=errors), 400
    return jsonify(ok=True, result=result)


@app.errorhandler(404)
@app.errorhandler(405)
@app.errorhandler(413)
def http_error(exc):
    if request.path.startswith("/api/"):
        return jsonify(ok=False, error=exc.description if hasattr(exc, "description") else "Request failed."), exc.code
    return exc


@app.errorhandler(500)
def server_error(_exc):
    if request.path.startswith("/api/"):
        return jsonify(ok=False, error="Unexpected server error. Please try again."), 500
    return "Internal server error", 500


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5000")), debug=False)
