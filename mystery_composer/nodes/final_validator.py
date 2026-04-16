"""Stage ⑤ - Final Validator (closure gate).

Checks foreshadow recovery, unanswered questions, and ending completeness.
If it fails, the graph routes back to rewrite the closure chapter.
"""

import json

from mystery_composer.state import GraphState
from mystery_composer.llm import get_llm
from mystery_composer.json_utils import safe_parse_json
from mystery_composer.prompts.templates import FINAL_VALIDATOR_PROMPT


MAX_CLOSURE_RETRIES = 2


def final_validator(state: GraphState) -> dict:
    llm = get_llm()
    invariants = state["invariants"]
    trick = state["trick"]
    chapters = state["chapters"]
    full_text = "\n\n".join(chapters)

    foreshadows_status = [
        {"id": fs["id"], "status": fs["status"], "payoff_chapter": fs["payoff_chapter"]}
        for fs in trick.get("foreshadows", [])
    ]

    prompt = FINAL_VALIDATOR_PROMPT.format(
        invariants=json.dumps(invariants, ensure_ascii=False, indent=2),
        trick=json.dumps(trick, ensure_ascii=False, indent=2),
        full_text=full_text,
        foreshadows_status=json.dumps(foreshadows_status, ensure_ascii=False, indent=2),
    )

    response = llm.invoke(prompt)
    result = safe_parse_json(response.content, llm=llm, prompt=prompt)

    validation = {
        "passed": bool(result.get("passed", False)),
        "unrecovered_foreshadows": result.get("unrecovered_foreshadows", []) or [],
        "unanswered_questions": result.get("unanswered_questions", []) or [],
        "missing_closure_items": result.get("missing_closure_items", []) or [],
        "suggestions": result.get("suggestions", ""),
    }

    print("\n[Stage ⑤] Final validation:")
    print(f"  passed                 = {validation['passed']}")
    print(f"  unrecovered_foreshadows= {validation['unrecovered_foreshadows']}")
    print(f"  unanswered_questions   = {validation['unanswered_questions']}")
    print(f"  missing_closure_items  = {validation['missing_closure_items']}")

    return {"final_validation": validation}


def rewrite_closure(state: GraphState) -> dict:
    """If final validator fails, pop the last (closure) chapter and rebuild a forced
    closure plan that explicitly fixes the missing items. Routes back to writer.
    """
    chapters = list(state["chapters"])
    if not chapters:
        return {"closure_retry_count": state.get("closure_retry_count", 0) + 1}

    # Drop the last chapter so the loop will regenerate it
    dropped = chapters.pop()
    validation = state["final_validation"]

    # Build forced plan: include every unrecovered foreshadow + every missing closure item.
    # Look up the reveal beat to preserve its title and beat_index.
    trick = state["trick"]
    reveal_chapter = len(chapters) + 1
    reveal_beat = None
    for b in state["blueprint"].get("story_beats", []):
        if b.get("chapter_number") == reveal_chapter:
            reveal_beat = b
            break

    paradox_text = invariants_paradox(state)
    method_instruction = (
        f"完整说明作案手法（必须正面解释 paradox：{paradox_text}）"
        if paradox_text
        else "完整说明作案手法的逻辑闭环"
    )

    plan = {
        "chapter_number": reveal_chapter,
        "beat_index": reveal_beat.get("beat_index", reveal_chapter) if reveal_beat else reveal_chapter,
        "chapter_title": reveal_beat.get("chapter_title", "真相") if reveal_beat else "真相",
        "target_clues": [c["id"] for c in state["clues"] if c["status"] == "hidden"],
        "target_foreshadows": validation["unrecovered_foreshadows"] or [
            fs["id"] for fs in trick.get("foreshadows", []) if fs["status"] != "paid_off"
        ],
        "perspective_character": state["blueprint"].get("main_pov", ""),
        "scene_summary": "调查者当众或独白揭示完整真相，依次回答凶手身份、动机、手法，并回收所有伏笔。",
        "emotional_tone": "揭晓、收束、余韵",
        "must_include_events": [
            "明确指出凶手 / 主谋姓名与动机",
            method_instruction,
            "逐条解释关键证据的指向意义",
        ] + [f"补全：{q}" for q in validation["unanswered_questions"][:5]],
    }

    # Synthetic audit feedback in patch format, so the patch-mode writer can apply it.
    fake_patches = []
    pid = 0
    for fs_id in validation["unrecovered_foreshadows"]:
        pid += 1
        # Look up the foreshadow's real meaning to give writer something concrete
        meaning = ""
        for fs in trick.get("foreshadows", []):
            if fs["id"] == fs_id:
                meaning = fs.get("real_meaning", "")
                break
        fake_patches.append({
            "id": f"closure_p{pid}",
            "severity": "P1",
            "type": "missing_foreshadow",
            "anchor": "全章",
            "problem": f"伏笔 {fs_id} 未在解谜段落得到回收",
            "fix_instruction": (
                f"在解谜段落中明确解释 {fs_id} 的真实含义：{meaning}。"
                f"由侦探 {state['blueprint'].get('main_pov', '')} 当众说出。"
            ),
            "must_fix": True,
        })
    method_fix = (
        f"在解谜段落中正面解释作案手法，必须直接回答 paradox：{paradox_text}"
        if paradox_text
        else "在解谜段落中完整解释作案手法的逻辑闭环。"
    )
    for item in validation["missing_closure_items"]:
        pid += 1
        normalized = item.lower()
        is_critical = any(k in normalized for k in ("culprit", "murderer", "method"))
        fix_map = {
            "culprit": "在解谜段落中明确指出凶手 / 主谋姓名与作案动机。",
            "murderer": "在解谜段落中明确指出凶手 / 主谋姓名与作案动机。",
            "method": method_fix,
            "evidence": "在解谜段落中逐条解释关键证据的指向意义。",
        }
        fix = next((v for k, v in fix_map.items() if k in normalized), f"补全 {item}")
        fake_patches.append({
            "id": f"closure_p{pid}",
            "severity": "P0" if is_critical else "P1",
            "type": "missing_event",
            "anchor": "全章",
            "problem": f"结局缺失项: {item}",
            "fix_instruction": fix,
            "must_fix": True,
        })
    for q in validation["unanswered_questions"][:5]:
        pid += 1
        fake_patches.append({
            "id": f"closure_p{pid}",
            "severity": "P1",
            "type": "missing_event",
            "anchor": "全章",
            "problem": f"未答问题: {q}",
            "fix_instruction": f"在解谜段落或结尾追加一段，正面回答：{q}",
            "must_fix": True,
        })

    fake_audit = {
        "passed": False,
        "patches": fake_patches,
        "issues": [f"[{p['severity']}|{p['anchor']}] {p['problem']}" for p in fake_patches],
        "invariant_violations": [],
        "suggestions": validation.get("suggestions", "请在解谜段落补全所有上述要求。"),
    }

    print(f"  [Closure Gate] Rewriting closure chapter (retry {state.get('closure_retry_count', 0) + 1})")

    return {
        "chapters": chapters,
        "current_plan": plan,
        "current_draft": dropped,  # writer will treat as revision base
        "audit_result": fake_audit,
        "retry_count": 1,
        "closure_retry_count": state.get("closure_retry_count", 0) + 1,
    }


def invariants_paradox(state: GraphState) -> str:
    return state.get("invariants", {}).get("paradox", "")
