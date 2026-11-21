"""TypeSafe Jev coverage and call checks."""
import json
import os
import urllib.error
import urllib.request

from mechanicus.config import configured_jev_model, log
from mechanicus.console import blank
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
    blank()
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
    from mechanicus.activity import activity_spinner

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
        with activity_spinner("Asking Jev"):
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


_DESCRIPTION_BREADTH = (
    (
        "outcomes",
        "Does this description name the primary user outcomes end-to-end?",
    ),
    (
        "core_flows",
        "Does this description cover the core create/read/update/delete "
        "(or domain-equivalent) flows the product needs?",
    ),
    (
        "failures",
        "Does this description mention important failure, empty-input, "
        "or permission behavior worth shipping?",
    ),
    (
        "persistence",
        "If the product keeps data, does this description say how it is "
        "stored or persisted? Answer yes when persistence is not needed.",
    ),
    (
        "boundaries",
        "If the product imports, exports, or integrates with other systems, "
        "does this description name those boundaries? Answer yes when none apply.",
    ),
    (
        "explicit",
        "Are the essential product capabilities stated explicitly, "
        "not only implied?",
    ),
)


def description_breadth(description):
    """Return gap labels for a thin application description.

    Returns:
      - [] when the description looks broad enough
      - a non-empty list of rubric labels when thin
      - None when Jev is skipped (no key) or the call fails soft
    Records ok/skipped/failed with gate=description.
    """
    text = str(description or "").strip()
    if not text:
        note_jev_result(
            "failed",
            "empty",
            "Application description is empty.",
            gate="description",
        )
        return list(label for label, _q in _DESCRIPTION_BREADTH)
    questions = {}
    for label, prompt in _DESCRIPTION_BREADTH:
        questions[label] = {"type": "noul", "instructions": prompt}
    try:
        answers = ask_jev(f"Application description:\n{text}", questions)
    except Exception as error:
        log.exception("jev description breadth failed")
        print(f"Error: {error}")
        _note_jev_failed("error", str(error), gate="description")
        return None
    if answers is None:
        _note_jev_skipped(gate="description")
        return None
    gaps = []
    for label, _prompt in _DESCRIPTION_BREADTH:
        probability = _noul(answers, label)
        log.info("jev description %s %.2f", label, probability)
        if probability < _JEV_YES:
            gaps.append(label)
            print(f"Description gap ({probability:.2f}): {label}")
    detail = f"gaps={','.join(gaps)}" if gaps else ""
    if gaps:
        note_jev_result("ok", "thin", detail, gate="description")
    else:
        _note_jev_ok(gate="description", detail=detail)
    return gaps


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
