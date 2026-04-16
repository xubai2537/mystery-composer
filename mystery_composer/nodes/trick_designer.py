"""Stage ③ - Trick Designer (genre-agnostic)."""

import json

from mystery_composer.state import GraphState
from mystery_composer.llm import get_llm
from mystery_composer.json_utils import safe_parse_json
from mystery_composer.prompts.templates import TRICK_DESIGNER_PROMPT


def trick_designer(state: GraphState) -> dict:
    llm = get_llm()
    invariants = state["invariants"]
    blueprint = state["blueprint"]

    prompt = TRICK_DESIGNER_PROMPT.format(
        invariants=json.dumps(invariants, ensure_ascii=False, indent=2),
        blueprint=json.dumps(blueprint, ensure_ascii=False, indent=2),
    )
    response = llm.invoke(prompt)
    data = safe_parse_json(response.content, llm=llm, prompt=prompt)

    foreshadows = []
    for fs in data.get("foreshadows", []) or []:
        foreshadows.append({
            "id": fs["id"],
            "description": fs["description"],
            "plant_chapter": fs["plant_chapter"],
            "payoff_chapter": fs["payoff_chapter"],
            "real_meaning": fs.get("real_meaning", ""),
            "status": "pending",
        })

    trick = {
        "one_line_method": data.get("one_line_method", ""),
        "method_breakdown": data.get("method_breakdown", []) or [],
        "central_trick_explanation": data.get("central_trick_explanation", ""),
        "consistency_checks": data.get("consistency_checks", []) or [],
        "foreshadows": foreshadows,
    }

    clues = []
    for c in data.get("clues", []) or []:
        clues.append({
            "id": c["id"],
            "description": c["description"],
            "status": "hidden",
            "chapter_revealed": None,
        })

    truth = data.get("truth", {}) or {}
    truth["characters"] = blueprint["characters"]
    truth["one_line_method"] = trick["one_line_method"]
    truth["central_trick_explanation"] = trick["central_trick_explanation"]

    runtime_state = {
        "current_chapter": 0,
        "committed_events": [],
        "casualties_so_far": [],
        "exposed_clue_ids": [],
        "paid_off_foreshadow_ids": [],
        "character_locations": {},
    }

    print("  [Stage ③] Trick & foreshadow registry built:")
    print(f"    method        : {trick['one_line_method']}")
    if invariants.get("paradox"):
        print(f"    paradox lock  : {trick['central_trick_explanation'][:60]}...")
    print(f"    clues         : {len(clues)}")
    print(f"    foreshadows   : {len(foreshadows)}")

    return {
        "trick": trick,
        "truth": truth,
        "clues": clues,
        "runtime_state": runtime_state,
    }
