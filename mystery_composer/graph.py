"""LangGraph workflow for the 5-stage Mystery Composer pipeline.

Pipeline:
  ① constraint_extractor → ② blueprint_designer → ③ trick_designer
  → ④ director → writer → auditor → commit (loop per chapter)
  → ⑤ final_validator → (pass) END  /  (fail) rewrite_closure → writer ...
"""

from langgraph.graph import StateGraph, END

from mystery_composer.state import GraphState
from mystery_composer.nodes.constraint_extractor import constraint_extractor
from mystery_composer.nodes.blueprint_designer import blueprint_designer
from mystery_composer.nodes.trick_designer import trick_designer
from mystery_composer.nodes.director import director, director_replan
from mystery_composer.nodes.writer import writer
from mystery_composer.nodes.auditor import auditor

# Maximum number of director-replans allowed per chapter before we accept the
# best-effort draft. Replan is expensive (LLM call), so keep this small.
MAX_REPLANS_PER_CHAPTER = 1
from mystery_composer.nodes.final_validator import (
    final_validator,
    rewrite_closure,
    MAX_CLOSURE_RETRIES,
)


def commit_chapter(state: GraphState) -> dict:
    """Commit approved draft and update runtime state + clue/foreshadow status."""
    plan = state["current_plan"]
    chapter_number = plan["chapter_number"]
    draft = state["current_draft"]

    # Update clue statuses
    updated_clues = []
    for clue in state["clues"]:
        if clue["id"] in plan["target_clues"] and clue["status"] == "hidden":
            updated_clues.append({
                **clue,
                "status": "revealed",
                "chapter_revealed": chapter_number,
            })
        else:
            updated_clues.append(clue)

    # Update foreshadows
    trick = dict(state["trick"])
    updated_fs = []
    for fs in trick.get("foreshadows", []):
        new_fs = dict(fs)
        if fs["id"] in plan.get("target_foreshadows", []):
            if fs["plant_chapter"] == chapter_number and fs["status"] == "pending":
                new_fs["status"] = "planted"
            if fs["payoff_chapter"] == chapter_number:
                new_fs["status"] = "paid_off"
        updated_fs.append(new_fs)
    trick["foreshadows"] = updated_fs

    # Update runtime state
    rs = dict(state["runtime_state"])
    rs["current_chapter"] = chapter_number
    # find this chapter's beat in the blueprint
    beat = None
    for b in state["blueprint"].get("story_beats", []):
        if b.get("chapter_number") == chapter_number:
            beat = b
            break
    if beat:
        # Append the beat's key_event to committed_events (one entry per chapter)
        key_event = beat.get("key_event") or plan.get("scene_summary", "")
        rs["committed_events"] = list(rs.get("committed_events", [])) + [
            f"第{chapter_number}章「{beat.get('chapter_title', '')}」: {key_event}"
        ]
        # Accumulate casualties (may be empty for non-incident beats)
        for victim in beat.get("casualties", []) or []:
            if victim and victim not in rs.get("casualties_so_far", []):
                rs["casualties_so_far"] = list(rs.get("casualties_so_far", [])) + [victim]
    rs["exposed_clue_ids"] = sorted({
        *rs.get("exposed_clue_ids", []),
        *[c["id"] for c in updated_clues if c["status"] == "revealed"],
    })
    rs["paid_off_foreshadow_ids"] = [fs["id"] for fs in updated_fs if fs["status"] == "paid_off"]

    # Record any unresolved P0 violations so downstream chapters are aware.
    # These arise when the circuit breaker force-commits a chapter despite failures.
    audit = state.get("audit_result") or {}
    unresolved_p0 = [
        {
            "chapter": chapter_number,
            "anchor": p["anchor"],
            "problem": p["problem"],
        }
        for p in (audit.get("patches", []) or [])
        if p.get("severity") == "P0" and p.get("must_fix")
    ]
    if unresolved_p0:
        existing = rs.get("unresolved_violations", []) or []
        rs["unresolved_violations"] = existing + unresolved_p0
        print(
            f"  [Commit] ⚠ {len(unresolved_p0)} unresolved P0 violation(s) "
            f"recorded in runtime_state.unresolved_violations"
        )
    else:
        # Clean up violations resolved by this chapter's commit
        rs.setdefault("unresolved_violations", [])

    chapters = list(state["chapters"]) + [draft]

    return {
        "chapters": chapters,
        "clues": updated_clues,
        "trick": trick,
        "runtime_state": rs,
        "current_plan": None,
        "current_draft": "",
        "audit_result": None,
        "retry_count": 0,
        # Reset per-chapter quality tracking for the next chapter
        "stable_passed_clue_ids": [],
        "must_fix_count_history": [],
        "clue_manifest": [],
        "event_manifest": [],
        "foreshadow_manifest": [],
        "evidence_chain": [],
        "p1_fail_history": {},
    }


# --- Routing ---

def route_after_audit(state: GraphState) -> str:
    """Tiered commit gate.

    Priority order:
    1. No must-fix patches              → commit
    2. Circuit breaker: must_fix count  → commit (with warnings)
       stuck ≥ 3 consecutive audits
    3. Still has retries                → writer (patch-mode revise)
    4. Retries exhausted + P1 + replan  → director_replan
       budget available
    5. Last resort                      → degraded commit (with warnings)
    """
    audit = state.get("audit_result") or {}
    patches = audit.get("patches", []) or []
    must_fix = [p for p in patches if p.get("must_fix")]

    if not must_fix:
        return "commit"

    p0_unfixed = [p for p in must_fix if p["severity"] == "P0"]
    p1_unfixed = [p for p in must_fix if p["severity"] == "P1"]

    # --- Circuit breaker ---
    # If must_fix count has not decreased for 3 consecutive audit attempts,
    # the writer-auditor pair is stuck in a loop. Force-commit with warnings.
    history = state.get("must_fix_count_history", []) or []
    if len(history) >= 3 and len(set(history[-3:])) == 1 and history[-1] > 0:
        print(
            f"  [Circuit Breaker] must_fix count stuck at {history[-1]} × 3 consecutive audits "
            f"→ force-committing to break loop."
        )
        for p in must_fix:
            print(f"    ! [{p['severity']}|{p['anchor']}] {p['problem']}")
        return "commit"

    # Still have retry budget — try patch-mode revision
    if state["retry_count"] < state["max_retries"]:
        return "retry"

    # Retries exhausted. Can we replan? Replan only helps for P1 (plan
    # infeasibility). Pure P0 violations need a writer-level fix, not a plan
    # change, so skip replan in that case.
    can_replan = (
        state.get("replan_count", 0) < MAX_REPLANS_PER_CHAPTER
        and len(p1_unfixed) > 0
    )
    if can_replan:
        print(
            f"  [Route] Retries exhausted with {len(p1_unfixed)} P1 + "
            f"{len(p0_unfixed)} P0 unfixed → escalating to director_replan."
        )
        return "replan"

    # Last resort
    if p0_unfixed:
        print(
            f"  [WARNING] Force-committing chapter with {len(p0_unfixed)} "
            f"unresolved P0 violation(s):"
        )
        for p in p0_unfixed:
            print(f"    ! [{p['anchor']}] {p['problem']}")
    else:
        print(
            f"  [WARNING] Committing with {len(p1_unfixed)} unresolved P1 issue(s)."
        )
    return "commit"


def route_after_commit(state: GraphState) -> str:
    if len(state["chapters"]) >= state["total_chapters"]:
        return "validate"
    return "next_chapter"


def route_after_final(state: GraphState) -> str:
    val = state.get("final_validation") or {}
    if val.get("passed"):
        return "end"
    if state.get("closure_retry_count", 0) >= MAX_CLOSURE_RETRIES:
        print("  [WARNING] Max closure retries reached, accepting current ending.")
        return "end"
    return "rewrite_closure"


def build_graph() -> StateGraph:
    workflow = StateGraph(GraphState)

    # Stage ① ② ③
    workflow.add_node("constraint_extractor", constraint_extractor)
    workflow.add_node("blueprint_designer", blueprint_designer)
    workflow.add_node("trick_designer", trick_designer)

    # Stage ④
    workflow.add_node("director", director)
    workflow.add_node("director_replan", director_replan)
    workflow.add_node("writer", writer)
    workflow.add_node("auditor", auditor)
    workflow.add_node("commit", commit_chapter)

    # Stage ⑤
    workflow.add_node("final_validator", final_validator)
    workflow.add_node("rewrite_closure", rewrite_closure)

    workflow.set_entry_point("constraint_extractor")

    workflow.add_edge("constraint_extractor", "blueprint_designer")
    workflow.add_edge("blueprint_designer", "trick_designer")
    workflow.add_edge("trick_designer", "director")
    workflow.add_edge("director", "writer")
    workflow.add_edge("writer", "auditor")

    workflow.add_conditional_edges(
        "auditor",
        route_after_audit,
        {
            "commit": "commit",
            "retry": "writer",
            "replan": "director_replan",
        },
    )
    # After replan, restart the writer with the relaxed plan (fresh draft)
    workflow.add_edge("director_replan", "writer")
    workflow.add_conditional_edges(
        "commit",
        route_after_commit,
        {"next_chapter": "director", "validate": "final_validator"},
    )
    workflow.add_conditional_edges(
        "final_validator",
        route_after_final,
        {"end": END, "rewrite_closure": "rewrite_closure"},
    )
    # rewrite_closure → writer (treated as revision) → auditor → commit → validator
    workflow.add_edge("rewrite_closure", "writer")

    return workflow.compile()
