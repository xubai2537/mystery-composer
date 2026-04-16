"""Stage ① - Constraint Extractor.

Faithfully pins user input down as P0 invariants. No genre assumptions.
"""

from mystery_composer.state import GraphState
from mystery_composer.llm import get_llm
from mystery_composer.json_utils import safe_parse_json
from mystery_composer.prompts.templates import CONSTRAINT_EXTRACTOR_PROMPT


VALID_SCALES = {"single_incident", "serial", "multi_layered"}


def constraint_extractor(state: GraphState) -> dict:
    llm = get_llm()
    prompt = CONSTRAINT_EXTRACTOR_PROMPT.format(user_input=state["user_input"])
    response = llm.invoke(prompt)
    data = safe_parse_json(response.content, llm=llm, prompt=prompt)

    case_scale = (data.get("case_scale") or "single_incident").strip()
    if case_scale not in VALID_SCALES:
        case_scale = "single_incident"

    invariants = {
        "setting": data.get("setting", ""),
        "core_premise": data.get("core_premise", ""),
        "case_scale": case_scale,
        "key_entities": data.get("key_entities", []) or [],
        "paradox": data.get("paradox", "") or "",
        "other_constraints": data.get("other_constraints", []) or [],
    }

    print("  [Stage ①] Invariants locked:")
    print(f"    setting       = {invariants['setting']}")
    print(f"    core_premise  = {invariants['core_premise']}")
    print(f"    case_scale    = {invariants['case_scale']}")
    print(f"    key_entities  = {invariants['key_entities']}")
    if invariants["paradox"]:
        print(f"    paradox       = {invariants['paradox']}")
    if invariants["other_constraints"]:
        print(f"    other         = {invariants['other_constraints']}")

    return {"invariants": invariants}
