"""Stage ④ - Director (story_beat aware) + Director Replan (escalation)."""

import json

from mystery_composer.state import GraphState
from mystery_composer.llm import get_llm
from mystery_composer.json_utils import safe_parse_json
from mystery_composer.prompts.templates import DIRECTOR_PROMPT, DIRECTOR_REPLAN_PROMPT


def _find_beat(blueprint: dict, chapter_number: int) -> dict | None:
    for b in blueprint.get("story_beats", []):
        if b.get("chapter_number") == chapter_number:
            return b
    return None


def director(state: GraphState) -> dict:
    llm = get_llm()
    chapter_number = len(state["chapters"]) + 1

    blueprint = state["blueprint"]
    invariants = state["invariants"]
    total_chapters = state["total_chapters"]

    beat = _find_beat(blueprint, chapter_number)
    if beat is None:
        # This should not happen because total_chapters == len(story_beats),
        # but defend against off-by-one anyway.
        beat = {
            "beat_index": chapter_number,
            "chapter_number": chapter_number,
            "chapter_title": f"第{chapter_number}章",
            "beat_type": "reveal" if chapter_number == total_chapters else "investigation",
            "key_event": "",
            "setting_in_chapter": "",
            "casualties": [],
            "new_clues": [],
            "foreshadow_actions": [],
        }

    # Previous chapters summary
    if state["chapters"]:
        summaries = []
        for i, ch in enumerate(state["chapters"], 1):
            summaries.append(f"第{i}章: {ch[:200]}...")
        previous = "\n".join(summaries)
    else:
        previous = "（尚无已完成章节）"

    prompt = DIRECTOR_PROMPT.format(
        chapter_number=chapter_number,
        beat_index=beat["beat_index"],
        invariants=json.dumps(invariants, ensure_ascii=False, indent=2),
        beat=json.dumps(beat, ensure_ascii=False, indent=2),
        pov_mode=blueprint.get("pov_mode", "single"),
        main_pov=blueprint.get("main_pov", ""),
        clues=json.dumps(state["clues"], ensure_ascii=False, indent=2),
        foreshadows=json.dumps(state["trick"].get("foreshadows", []), ensure_ascii=False, indent=2),
        runtime_state=json.dumps(state["runtime_state"], ensure_ascii=False, indent=2),
        previous_chapters_summary=previous,
    )

    response = llm.invoke(prompt)
    plan = safe_parse_json(response.content, llm=llm, prompt=prompt)

    # Hard-enforce single POV
    if blueprint.get("pov_mode", "single") == "single":
        plan["perspective_character"] = blueprint.get("main_pov", plan.get("perspective_character", ""))

    # Ensure required fields
    plan["chapter_number"] = chapter_number
    plan["beat_index"] = beat["beat_index"]
    plan["chapter_title"] = beat.get("chapter_title", f"第{chapter_number}章")
    plan.setdefault("target_clues", [])
    plan.setdefault("target_foreshadows", [])
    plan.setdefault("must_include_events", [])
    if not plan["must_include_events"] and beat.get("key_event"):
        plan["must_include_events"] = [beat["key_event"]]

    is_reveal = beat.get("beat_type") == "reveal"
    if is_reveal:
        for c in state["clues"]:
            if c["status"] == "hidden" and c["id"] not in plan["target_clues"]:
                plan["target_clues"].append(c["id"])
        for fs in state["trick"].get("foreshadows", []):
            if fs["status"] != "paid_off" and fs["id"] not in plan["target_foreshadows"]:
                plan["target_foreshadows"].append(fs["id"])

        # Inject full trick design so Writer cannot deviate from the designed method.
        trick = state["trick"]
        truth = trick.get("truth", {})
        trick_steps = trick.get("steps", [])
        plan["trick_detail"] = {
            "culprit": truth.get("culprit", ""),
            "motive": truth.get("motive", ""),
            "one_line_method": trick.get("one_line_method", ""),
            "steps": trick_steps,
            "central_trick_explanation": trick.get("central_trick_explanation", ""),
            "key_evidence": truth.get("key_evidence", []),
        }
        # Also inject each trick step as a must_include_event so the Writer
        # cannot silently skip any step.
        existing_events = list(plan.get("must_include_events", []))
        step_events = [f"[诡计步骤{i+1}] {s}" for i, s in enumerate(trick_steps)]
        plan["must_include_events"] = existing_events + step_events
    else:
        plan.setdefault("trick_detail", {})

    return {
        "current_plan": plan,
        "retry_count": 0,
        "replan_count": 0,
        "audit_result": None,
    }


def director_replan(state: GraphState) -> dict:
    """Escalation: Writer is stuck. Relax the plan within safe bounds."""
    llm = get_llm()
    plan = state["current_plan"]
    blueprint = state["blueprint"]
    invariants = state["invariants"]
    audit = state.get("audit_result") or {}
    stuck_patches = audit.get("patches", []) or []

    beat = _find_beat(blueprint, plan["chapter_number"]) or {
        "beat_index": plan.get("beat_index", plan["chapter_number"]),
        "chapter_number": plan["chapter_number"],
        "chapter_title": plan.get("chapter_title", f"第{plan['chapter_number']}章"),
        "beat_type": "investigation",
        "key_event": "",
    }

    must_fix_patches = [p for p in stuck_patches if p.get("must_fix")]
    all_p0 = bool(must_fix_patches) and all(p["severity"] == "P0" for p in must_fix_patches)
    if all_p0:
        print("  [Director Replan] All stuck patches are P0; replan cannot help. Skipping.")
        return {
            "replan_count": state.get("replan_count", 0) + 1,
        }

    prompt = DIRECTOR_REPLAN_PROMPT.format(
        invariants=json.dumps(invariants, ensure_ascii=False, indent=2),
        beat=json.dumps(beat, ensure_ascii=False, indent=2),
        main_pov=blueprint.get("main_pov", ""),
        previous_plan=json.dumps(plan, ensure_ascii=False, indent=2),
        stuck_patches=json.dumps(stuck_patches, ensure_ascii=False, indent=2),
        clues=json.dumps(state["clues"], ensure_ascii=False, indent=2),
        foreshadows=json.dumps(state["trick"].get("foreshadows", []), ensure_ascii=False, indent=2),
        chapter_number=plan["chapter_number"],
        beat_index=plan.get("beat_index", plan["chapter_number"]),
    )

    response = llm.invoke(prompt)
    new_plan = safe_parse_json(response.content, llm=llm, prompt=prompt)

    # Safety: target_clues must be subset of old; preserve frozen fields
    old_targets = set(plan.get("target_clues", []))
    proposed_targets = set(new_plan.get("target_clues", []))
    if not proposed_targets.issubset(old_targets):
        print("  [Director Replan] WARNING: replan tried to add new clues; clamping to old set.")
        proposed_targets &= old_targets
    new_plan["target_clues"] = list(proposed_targets)
    new_plan["perspective_character"] = blueprint.get("main_pov", plan["perspective_character"])
    new_plan["chapter_number"] = plan["chapter_number"]
    new_plan["beat_index"] = plan.get("beat_index", plan["chapter_number"])
    new_plan["chapter_title"] = plan.get("chapter_title", new_plan.get("chapter_title", ""))
    new_plan.setdefault("target_foreshadows", plan.get("target_foreshadows", []))
    new_plan.setdefault("must_include_events", plan.get("must_include_events", []))

    deferred = new_plan.get("deferred_clues", [])
    reason = new_plan.get("replan_reason", "")
    print(f"  [Director Replan] reason: {reason}")
    print(f"  [Director Replan] target_clues: {sorted(old_targets)} → {sorted(proposed_targets)}")
    if deferred:
        print(f"  [Director Replan] deferred to later chapters: {deferred}")

    return {
        "current_plan": new_plan,
        "retry_count": 0,
        "replan_count": state.get("replan_count", 0) + 1,
        "current_draft": "",
        "audit_result": None,
        # Fresh retry loop after replan — clear quality tracking
        "stable_passed_clue_ids": [],
        "must_fix_count_history": [],
        "clue_manifest": [],
        "event_manifest": [],
        "foreshadow_manifest": [],
        "evidence_chain": [],
        "p1_fail_history": {},
    }
