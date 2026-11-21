"""TypeSafe Jev coverage and call checks."""
import json
import os
import urllib.error
import urllib.request

from mechanicus.config import configured_jev_model, log
from mechanicus.paths import module_import


_JEV_YES = 0.7
_JEV_URL = "https://api.typesafe.ai/v1/systemone"
_LAST_JEV = {
    "status": "none",
    "reason": "",
    "detail": "",
    "gate": "",
}


def last_jev_result():
    """Return a copy of the last in-process Jev outcome."""
    return dict(_LAST_JEV)


def clear_last_jev():
    """Reset the in-process Jev outcome to none."""
    _LAST_JEV["status"] = "none"
    _LAST_JEV["reason"] = ""
    _LAST_JEV["detail"] = ""
    _LAST_JEV["gate"] = ""


def note_jev_result(status, reason, detail="", gate=""):
    """Record and print a structured Jev outcome (ok, skipped, or failed)."""
    _LAST_JEV["status"] = status
    _LAST_JEV["reason"] = reason
    _LAST_JEV["detail"] = detail or ""
    _LAST_JEV["gate"] = gate or ""
    log.info(
        "jev result status=%s reason=%s gate=%s detail=%s",
        status,
        reason,
        gate or "",
        (detail or "")[:200],
    )
    line = f"Jev: {status} reason={reason}"
    if gate:
        line += f" gate={gate}"
    print(line)
    if detail and status == "failed":
        print(f"detail: {detail}")
    return last_jev_result()


def _note_jev_skipped(gate=""):
    detail = "TYPESAFE_API_KEY is empty."
    return note_jev_result("skipped", "no_key", detail, gate)


def _note_jev_failed(reason, detail="", gate=""):
    return note_jev_result("failed", reason, detail, gate)


def _note_jev_ok(gate="", detail=""):
    return note_jev_result("ok", "ok", detail, gate)


def _noul(answers, key):
    answer = answers.get(key)
    if not isinstance(answer, dict):
        return 0.0
    try:
        return float(answer.get("noul"))
    except (TypeError, ValueError):
        return 0.0


def ask_jev(state, questions):
    """Ask Jev yes-or-no questions. Return None when the API key is empty."""
    key = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if not key:
        return None
    if not questions:
        return {}
    model = configured_jev_model()
    log.info("jev request model=%s questions=%s", model, len(questions))
    payload = json.dumps({
        "model": model,
        "state": state,
        "questions": questions,
    }).encode("utf-8")
    request = urllib.request.Request(
        _JEV_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"Jev request failed ({error.code}). {detail}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"Jev request failed. {error.reason}") from error
    log.info("jev response model=%s", body.get("model"))
    answers = body.get("answers")
    if not isinstance(answers, dict):
        raise RuntimeError("Jev response has no answers.")
    return answers


def _plan_state(description, modules, heading="Application description"):
    blocks = [f"{heading}:\n{str(description).strip()}"]
    for item in modules:
        lines = [f"uses: {use}" for use in item["uses"]]
        block = f"--- {item['name']} ---\n"
        if lines:
            block += "\n".join(lines) + "\n\n"
        block += item["behavior"]
        blocks.append(block)
    return "\n\n".join(blocks)


def missing_behaviors(description, modules, parts):
    """Return behaviors no module performs. Return None when the API key is empty."""
    questions = {}
    for index, part in enumerate(parts):
        questions[f"part-{index}"] = {
            "type": "noul",
            "instructions": f"Does at least one module perform this behavior: {part}?",
        }
    answers = ask_jev(_plan_state(description, modules), questions)
    if answers is None:
        _note_jev_skipped(gate="plan")
        return None
    missing = []
    for index, part in enumerate(parts):
        probability = _noul(answers, f"part-{index}")
        log.info("jev behavior %.2f %s", probability, part)
        if probability < _JEV_YES:
            missing.append(part)
            print(f"No module performs this behavior ({probability:.2f}): {part}")
    detail = f"missing={len(missing)}" if missing else ""
    _note_jev_ok(gate="plan", detail=detail)
    return missing


def feature_score(feature, modules):
    """Return the probability that a module performs the feature.

    Return None when the API key is empty.
    """
    answers = ask_jev(
        _plan_state(feature, modules, "Feature"),
        {
            "feature": {
                "type": "noul",
                "instructions": f"Does at least one module perform this feature: {feature}?",
            },
        },
    )
    if answers is None:
        _note_jev_skipped(gate="feature")
        return None
    probability = _noul(answers, "feature")
    log.info("jev feature %.2f %s", probability, feature)
    _note_jev_ok(gate="feature", detail=f"score={probability:.2f}")
    return probability


def _dependency_label(spec_name, swift=False):
    """Human label for a uses: dependency in Jev questions."""
    if swift:
        from mechanicus.swift_support import swift_type_name

        return swift_type_name(spec_name) or str(spec_name)
    return module_import(spec_name) or str(spec_name)


def weak_call_tests(behavior, tests, check, required, swift=False):
    """Return uses deps the tests do not require. Return None when the key is empty.

    For Swift packages, questions name adopted types (swift_type_name), not
    Python import paths. Same gate threshold and ok/skipped/failed recording.
    """
    questions = {}
    labels = []
    for index, spec_name in enumerate(required):
        label = _dependency_label(spec_name, swift=swift)
        kind = "type" if swift else "module"
        questions[f"call-{index}"] = {
            "type": "noul",
            "instructions": (
                f"Do these tests fail when the implementation never uses "
                f"the required {kind} {label}?"
            ),
        }
        labels.append(label)
    state = f"Behavior:\n{behavior}\n\nTests:\n{tests}\n\nCheck:\n{check}"
    answers = ask_jev(state, questions)
    if answers is None:
        _note_jev_skipped(gate="tests")
        return None
    weak = []
    for index, label in enumerate(labels):
        probability = _noul(answers, f"call-{index}")
        log.info("jev tests %s %.2f", label, probability)
        if probability < _JEV_YES:
            weak.append((label, probability))
    detail = ""
    if weak:
        detail = "weak=" + ",".join(name for name, _probability in weak)
    _note_jev_ok(gate="tests", detail=detail)
    return weak
