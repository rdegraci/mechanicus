"""TypeSafe Jev coverage and call checks."""
import json
import os
import urllib.error
import urllib.request

from mechanicus.config import configured_jev_model, log
from mechanicus.paths import module_import


_JEV_YES = 0.7
_JEV_URL = "https://api.typesafe.ai/v1/systemone"
_JEV_SKIP_PRINTED = False


def _note_jev_skipped():
    global _JEV_SKIP_PRINTED
    log.info("jev skipped: no api key")
    if _JEV_SKIP_PRINTED:
        return
    _JEV_SKIP_PRINTED = True
    print("Jev was not called. Set TYPESAFE_API_KEY.")


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
        _note_jev_skipped()
        return None
    missing = []
    for index, part in enumerate(parts):
        probability = _noul(answers, f"part-{index}")
        log.info("jev behavior %.2f %s", probability, part)
        if probability < _JEV_YES:
            missing.append(part)
            print(f"No module performs this behavior ({probability:.2f}): {part}")
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
        _note_jev_skipped()
        return None
    probability = _noul(answers, "feature")
    log.info("jev feature %.2f %s", probability, feature)
    return probability


def weak_call_tests(behavior, tests, check, required):
    """Return uses modules the tests do not require. Return None when the key is empty."""
    questions = {}
    labels = []
    for index, spec_name in enumerate(required):
        imported = module_import(spec_name) or spec_name
        questions[f"call-{index}"] = {
            "type": "noul",
            "instructions": (
                f"Do these tests fail when the implementation never calls {imported}?"
            ),
        }
        labels.append(imported)
    state = f"Behavior:\n{behavior}\n\nTests:\n{tests}\n\nCheck:\n{check}"
    answers = ask_jev(state, questions)
    if answers is None:
        _note_jev_skipped()
        return None
    weak = []
    for index, imported in enumerate(labels):
        probability = _noul(answers, f"call-{index}")
        log.info("jev tests %s %.2f", imported, probability)
        if probability < _JEV_YES:
            weak.append((imported, probability))
    return weak
