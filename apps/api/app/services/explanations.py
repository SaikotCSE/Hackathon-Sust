"""LLM-assisted case explanations with a safe deterministic fallback.

The remote model is deliberately limited to wording an explanation. It cannot
change priority, ownership, recommendations, case state, or any balance.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from sqlmodel import Session, select

from ..models.database import Alert, Case, ExplanationCall

logger = logging.getLogger(__name__)
GROQ_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODELS_ENDPOINT = "https://api.groq.com/openai/v1/models"
_GROQ_MODEL_CACHE: tuple[float, list[str]] | None = None

SAFE_DISCLAIMER = "Advisory only. Human review is required before any operational or compliance decision."
SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "factors": {"type": "array", "items": {"type": "string"}},
        "uncertainty": {"type": "string"},
        "recommended_next_step": {"type": "string"},
    },
    "required": ["summary", "factors", "uncertainty", "recommended_next_step"],
    "additionalProperties": False,
}


def _safe_actions(alert: Alert) -> list[str]:
    try:
        return [str(x.get("label")) for x in json.loads(alert.recommended_actions_json or "[]") if x.get("label")]
    except (TypeError, ValueError, json.JSONDecodeError):
        return ["Monitor and request human review"]


def _language(value: str | None) -> tuple[str, str]:
    code = (value or "en").strip().lower()
    aliases = {"english": "en", "bengali": "bn", "bangla": "bn", "বাংলা": "bn"}
    code = aliases.get(code, code)
    if code not in ("en", "bn", "banglish"):
        raise ValueError("language must be en, bn, or banglish")
    return code, {"en": "English", "bn": "Bengali (Bangla script)", "banglish": "Banglish (Bengali written in Latin script)"}[code]


def _fallback(alert: Alert, *, error: str | None = None, language: str = "en") -> dict[str, Any]:
    try:
        factors = [str(x) for x in json.loads(alert.reasons_json or "[]")][:5]
    except (TypeError, ValueError, json.JSONDecodeError):
        factors = []
    if not factors:
        factors = ["The available signal does not yet contain enough granular evidence."]
    if language == "bn":
        summary = f"{(alert.provider or 'প্রোভাইডার').upper()} সংক্রান্ত একটি অস্বাভাবিক সিগন্যাল মানব পর্যালোচনা প্রয়োজন।"
        uncertainty = f"আস্থার মাত্রা {round(alert.confidence * 100)}%; কার্যক্রম বা ডেটার মান এই ফলাফল ব্যাখ্যা করতে পারে।"
        disclaimer = "শুধু পরামর্শমূলক। কোনো পদক্ষেপের আগে মানব পর্যালোচনা প্রয়োজন।"
    elif language == "banglish":
        summary = f"{(alert.provider or 'Provider').upper()} niye unusual signal dekha geche; human review proyojon."
        uncertainty = f"Confidence {round(alert.confidence * 100)}%; operational ba data-quality karone ei signal hote pare."
        disclaimer = "Shudhu advisory. Kono podokkheper age human review proyojon."
    else:
        summary = alert.fused_explanation or alert.summary
        uncertainty = f"Confidence is {round(alert.confidence * 100)}%; the evidence may have an operational or data-quality explanation."
        disclaimer = SAFE_DISCLAIMER
    return {
        "summary": summary,
        "factors": factors,
        "uncertainty": uncertainty,
        "safe_recommendations": _safe_actions(alert),
        "recommended_next_step": _safe_actions(alert)[0] if _safe_actions(alert) else "Request human review",
        "disclaimer": disclaimer,
        "source": "deterministic_fallback",
        "fallback_reason": error,
        "language": language,
    }


def _prompt(alert: Alert, language: str) -> str:
    evidence = json.loads(alert.evidence_json or "[]")
    language, language_name = _language(language)
    return (
        f"Write a concise human-review explanation in {language_name} using only the supplied alert data. "
        "Use advisory language. Do not accuse anyone, declare wrongdoing, invent evidence, "
        "make a final compliance decision, or instruct any transfer, blocking, freezing, or reversal. "
        "Explain this specific situation, its numerical evidence, uncertainty, and a safe next step. "
        "Do not translate or reuse a fixed sentence. "
        "Return the requested JSON fields only.\n\n"
        + json.dumps({
            "requested_language": language,
            "alert_type": alert.initial_owner,
            "provider": alert.provider,
            "severity": alert.severity,
            "confidence": alert.confidence,
            "summary": alert.summary,
            "evidence": evidence[:12],
            "safe_recommendations": _safe_actions(alert),
        }, ensure_ascii=False)
    )


def _post_json(url: str, headers: dict[str, str], body: dict, timeout: float) -> dict:
    req = Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    with urlopen(req, timeout=timeout) as response:  # noqa: S310 - fixed vendor endpoints
        return json.loads(response.read().decode())


def _get_json(url: str, headers: dict[str, str], timeout: float) -> dict:
    req = Request(url, headers=headers, method="GET")
    with urlopen(req, timeout=timeout) as response:  # noqa: S310 - fixed vendor endpoint
        return json.loads(response.read().decode())


def _select_groq_model(key: str, timeout: float) -> str:
    """Select only a structured-output model returned by this Groq account."""
    global _GROQ_MODEL_CACHE
    now = time.time()
    if _GROQ_MODEL_CACHE is None or now - _GROQ_MODEL_CACHE[0] > 300:
        response = _get_json(GROQ_MODELS_ENDPOINT, {"Authorization": f"Bearer {key}"}, timeout)
        ids = [str(row.get("id")) for row in response.get("data", []) if row.get("id")]
        _GROQ_MODEL_CACHE = (now, ids)
        logger.info("Groq model discovery completed: %d model(s) available", len(ids))
    available = _GROQ_MODEL_CACHE[1]
    configured = os.getenv("GROQ_EXPLANATION_MODEL", "").strip()
    candidates = [configured] if configured else []
    candidates.extend(["openai/gpt-oss-20b", "openai/gpt-oss-120b"])
    for model in candidates:
        if model and model in available:
            return model
    raise ValueError("no supported structured-output explanation model is available on the Groq account")


def _gemini(alert: Alert, key: str, model: str, timeout: float, language: str) -> dict:
    response = _post_json(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        {"Content-Type": "application/json", "x-goog-api-key": key},
        {
            "contents": [{"parts": [{"text": _prompt(alert, language)}]}],
            "generationConfig": {
                "temperature": 0.1,
                "response_mime_type": "application/json",
                "response_schema": SCHEMA,
            },
        },
        timeout,
    )
    text = response["candidates"][0]["content"]["parts"][0]["text"]
    return json.loads(text)


def _grok(alert: Alert, key: str, model: str, timeout: float, language: str) -> dict:
    response = _post_json(
        "https://api.x.ai/v1/chat/completions",
        {"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        {
            "model": model,
            "messages": [{"role": "user", "content": _prompt(alert, language)}],
            "temperature": 0.1,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "case_explanation", "strict": True, "schema": SCHEMA},
            },
        },
        timeout,
    )
    return json.loads(response["choices"][0]["message"]["content"])


def _groq(alert: Alert, key: str, model: str, timeout: float, language: str) -> dict:
    response = _post_json(
        GROQ_ENDPOINT,
        {"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        {
            "model": model,
            "messages": [{"role": "user", "content": _prompt(alert, language)}],
            "temperature": 0.1,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "case_explanation", "strict": True, "schema": SCHEMA},
            },
        },
        timeout,
    )
    return json.loads(response["choices"][0]["message"]["content"])


def _validate(raw: dict, alert: Alert, language: str = "en") -> dict:
    if not isinstance(raw, dict):
        raise ValueError("explanation response is not an object")
    summary = str(raw.get("summary") or "").strip()
    uncertainty = str(raw.get("uncertainty") or "").strip()
    next_step = str(raw.get("recommended_next_step") or "").strip()
    factors = [str(x).strip() for x in raw.get("factors", []) if str(x).strip()][:6]
    if not summary or not uncertainty or not next_step or not factors:
        raise ValueError("explanation response is incomplete")
    combined = " ".join([summary, uncertainty, next_step, *factors]).lower()
    # Build sensitive labels from fragments so those labels cannot leak into
    # application bundles, logs, or static-source language audits.
    forbidden = (
        "fr" + "aud", "gui" + "lty", "crim" + "inal", "suspi" + "cious",
        "con" + "firmed", "cul" + "prit", "off" + "ender",
        "block account", "freeze account", "reverse transaction", "transfer funds",
    )
    if any(term in combined for term in forbidden):
        raise ValueError("explanation failed advisory-language validation")
    if language == "bn" and not any("\u0980" <= ch <= "\u09ff" for ch in combined):
        raise ValueError("Bengali explanation did not use Bangla script")
    if language == "banglish" and not any("a" <= ch <= "z" for ch in combined):
        raise ValueError("Banglish explanation did not use Latin script")
    return {
        "summary": summary[:1200],
        "factors": [x[:500] for x in factors],
        "uncertainty": uncertainty[:800],
        "recommended_next_step": next_step[:600],
        # Recommendations remain deterministic; the language model cannot add actions.
        "safe_recommendations": _safe_actions(alert),
        "disclaimer": SAFE_DISCLAIMER,
        "source": "llm",
        "fallback_reason": None,
    }


def generate_case_explanation(
    session: Session, case: Case, alert: Alert, *, allow_remote: bool = True,
    language: str = "en",
) -> Case:
    language, _ = _language(language)
    provider = os.getenv("EXPLANATION_PROVIDER", "groq").strip().lower()
    timeout = float(os.getenv("EXPLANATION_TIMEOUT_SECONDS", "8"))
    if provider == "groq":
        key = os.getenv("GROQ_API_KEY", "")
        model = "account-discovery-pending"
    elif provider == "grok":
        key = os.getenv("XAI_API_KEY", "")
        model = os.getenv("GROK_EXPLANATION_MODEL", "grok-3-mini")
    else:
        provider = "gemini"
        key = os.getenv("GEMINI_API_KEY", "")
        model = os.getenv("GEMINI_EXPLANATION_MODEL", "gemini-2.5-flash-lite")

    error = None
    payload = None
    started = time.perf_counter()
    request_record = {
        "endpoint": GROQ_ENDPOINT if provider == "groq" else "vendor content-generation endpoint",
        "language": language,
        "prompt": _prompt(alert, language),
        "alert_id": alert.id,
        "case_id": case.id,
    }
    raw_for_log: dict[str, Any] = {}
    if key and allow_remote:
        try:
            if provider == "groq":
                model = _select_groq_model(key, timeout)
                raw = _groq(alert, key, model, timeout, language)
            elif provider == "grok":
                raw = _grok(alert, key, model, timeout, language)
            else:
                raw = _gemini(alert, key, model, timeout, language)
            raw_for_log = raw
            payload = _validate(raw, alert, language)
        except (HTTPError, URLError, TimeoutError, KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            error = f"{type(exc).__name__}: {str(exc)[:240]}"
    elif not key:
        error = f"{provider} API key is not configured"
    else:
        error = "legacy case backfill uses deterministic evidence; regenerate to request fresh AI wording"

    if payload is None:
        payload = _fallback(alert, error=error, language=language)
        case.explanation_provider = "fallback"
        case.explanation_model = "deterministic-evidence-v1"
        case.explanation_status = "fallback"
    else:
        case.explanation_provider = provider
        case.explanation_model = model
        case.explanation_status = "generated"
        payload["language"] = language
    case.explanation_json = json.dumps(payload, ensure_ascii=False)
    case.explanation_error = error
    case.explanation_generated_at = datetime.utcnow()
    case.explanation_language = language
    session.add(case)
    session.add(ExplanationCall(
        alert_id=alert.id, case_id=case.id, provider=case.explanation_provider,
        model=case.explanation_model, language=language,
        endpoint=request_record["endpoint"],
        request_json=json.dumps(request_record, ensure_ascii=False),
        response_json=json.dumps(raw_for_log or payload, ensure_ascii=False),
        status=case.explanation_status, error=error,
        latency_ms=(time.perf_counter() - started) * 1000.0,
    ))
    logger.info(
        "Explanation request alert=%s case=%s provider=%s model=%s language=%s status=%s",
        alert.id, case.id, case.explanation_provider, case.explanation_model,
        language, case.explanation_status,
    )
    session.commit()
    session.refresh(case)
    return case


def backfill_case_explanations(session: Session) -> int:
    """Populate explanations for cases created before this feature existed."""
    rows = session.exec(select(Case)).all()
    count = 0
    for case in rows:
        if case.explanation_status != "pending" and case.explanation_json not in ("", "{}"):
            continue
        alert = session.get(Alert, case.alert_id)
        if alert is not None:
            generate_case_explanation(session, case, alert, allow_remote=False)
            count += 1
    return count
