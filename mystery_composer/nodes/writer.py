"""Stage ④ - Writer (state-aware, invariant-aware, patch-mode revise).

Output format: JSON with {chapter_text, clue_manifest, event_manifest, foreshadow_manifest}.
  chapter_text       — the narrative prose (plain text, used as current_draft)
  clue_manifest      — [{clue_id, excerpt, paragraph}]  clue coverage
  event_manifest     — [{event_ref(int), excerpt, paragraph}]  must_include_events coverage
  foreshadow_manifest— [{fs_id, action, excerpt, paragraph}]  foreshadow coverage

All three manifests are verified rule-based by the auditor (excerpt fuzzy-match).
If JSON parsing fails, raw output is treated as plain prose with all manifests empty.
"""

import json
import re
import time

from mystery_composer.state import GraphState
from mystery_composer.llm import get_llm
from mystery_composer.nodes.auditor import number_paragraphs
from mystery_composer.prompts.templates import (
    WRITER_PROMPT,
    WRITER_REVISE_PROMPT,
    WRITER_REWRITE_WITH_LESSONS_PROMPT,
)

_STREAM_MAX_RETRIES = 3
_STREAM_RETRY_DELAY = 5   # seconds between retries


def _stream_with_retry(llm, prompt: str, label: str) -> str:
    """Stream LLM output with automatic retry on network errors.

    Retry policy:
    - Up to _STREAM_MAX_RETRIES attempts with exponential back-off.
    - If streaming keeps failing, falls back to a non-streaming invoke().
    - Any partial output already received is discarded on failure and the
      full generation is restarted (we cannot splice partial chunks).
    """
    for attempt in range(1, _STREAM_MAX_RETRIES + 1):
        try:
            chunks = []
            for chunk in llm.stream(prompt):
                token = chunk.content
                if token:
                    print(token, end="", flush=True)
                    chunks.append(token)
            return "".join(chunks).strip()
        except Exception as e:
            err_name = type(e).__name__
            if attempt < _STREAM_MAX_RETRIES:
                wait = _STREAM_RETRY_DELAY * attempt
                print(
                    f"\n  [Writer] 流式传输中断 ({err_name}), "
                    f"第 {attempt} 次重试，等待 {wait}s..."
                )
                time.sleep(wait)
            else:
                print(
                    f"\n  [Writer] 流式传输连续失败 {_STREAM_MAX_RETRIES} 次 ({err_name})，"
                    f"降级为非流式调用..."
                )

    # Fallback: non-streaming invoke (no real-time output)
    print(f"  [Writer] 非流式生成「{label}」中...", end="", flush=True)
    response = llm.invoke(prompt)
    print(" 完成")
    return (response.content or "").strip()


def _format_patches_block(patches: list[dict]) -> str:
    """Render patches as a writer-readable block, sorted by severity (P0 first)."""
    if not patches:
        return "（无）"
    order = {"P0": 0, "P1": 1, "P2": 2}
    sorted_patches = sorted(patches, key=lambda p: order.get(p.get("severity", "P2"), 3))
    lines = []
    for p in sorted_patches:
        lines.append(
            f"[{p['id']} | {p['severity']} | must_fix={p['must_fix']} | type={p['type']}]\n"
            f"  anchor: {p['anchor']}\n"
            f"  problem: {p['problem']}\n"
            f"  fix:     {p['fix_instruction']}"
        )
    return "\n\n".join(lines)


def _format_target_clues(state: GraphState) -> str:
    plan = state["current_plan"]
    details = []
    for clue in state["clues"]:
        if clue["id"] in plan["target_clues"]:
            details.append(f"- {clue['id']}: {clue['description']}")
    return "\n".join(details) if details else "（无）"


def _format_target_foreshadows(state: GraphState) -> str:
    plan = state["current_plan"]
    chapter = plan["chapter_number"]
    lines = []
    for fs in state["trick"].get("foreshadows", []):
        if fs["id"] in plan.get("target_foreshadows", []):
            if fs["plant_chapter"] == chapter:
                action = "PLANT（埋设）"
            elif fs["payoff_chapter"] == chapter:
                action = "PAYOFF（揭示其真实含义）"
            else:
                action = "处理"
            lines.append(f"- [{action}] {fs['id']}: {fs['description']}（真实含义：{fs['real_meaning']}）")
    return "\n".join(lines) if lines else "（无）"


def _build_p0_constraints(invariants: dict) -> str:
    """Format invariants key_entities + paradox as an explicit P0 constraint block.

    Handles both old format (list of strings) and new format (list of {fact, scope}).
    at_discovery entries are labelled so the Writer knows they describe discovery-time state.
    """
    lines = []
    for item in invariants.get("key_entities", []):
        if isinstance(item, dict):
            fact = item.get("fact", "")
            scope = item.get("scope", "always_true")
            label = "（发现时状态，仅约束现场描写）" if scope == "at_discovery" else ""
            lines.append(f"- {fact}{label}")
        else:
            lines.append(f"- {item}")  # backward compat: old plain-string format
    paradox = (invariants.get("paradox") or "").strip()
    if paradox:
        lines.append(f"- [核心悖论] {paradox}")
    return "\n".join(lines) if lines else "（暂无 P0 精确约束）"


def _build_p0_fix_instructions(patches: list[dict]) -> str:
    """Render P0 must-fix patches as strict fix instructions (no creative freedom)."""
    p0 = [p for p in patches if p.get("severity") == "P0" and p.get("must_fix")]
    if not p0:
        return "（无）"
    return "\n".join(
        f"[{p['id']} | anchor={p['anchor']}]\n  问题：{p['problem']}\n  修复：{p['fix_instruction']}"
        for p in p0
    )


def _format_trick_reveal_block(plan: dict) -> str:
    """Build the reveal-chapter mandatory trick section.

    Returns an empty string for non-reveal chapters.
    The section header is intentionally eye-catching so the LLM cannot miss it.
    """
    td = plan.get("trick_detail") or {}
    if not td or not td.get("one_line_method"):
        return ""
    steps = td.get("steps") or []
    steps_text = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(steps)) or "  （无分步）"
    evidence = td.get("key_evidence") or []
    evidence_text = "\n".join(f"  - {e}" for e in evidence) or "  （无）"
    return (
        "\n== ⚠️ 核心诡计（reveal 章专属 · must_fix 级 · 不可更改）==\n"
        "以下是 trick_designer 设计的完整作案方案。揭露段落必须严格按照这个方案呈现，\n"
        "不得更改作案步骤，不得发明额外装置或替换核心手法：\n\n"
        f"凶手：{td.get('culprit', '？')}\n"
        f"动机：{td.get('motive', '？')}\n"
        f"一句话手法：{td.get('one_line_method', '？')}\n"
        f"完整步骤：\n{steps_text}\n"
        f"核心悖论解释：{td.get('central_trick_explanation', '（无）')}\n"
        f"关键证据（必须逐一解释其指向意义）：\n{evidence_text}\n"
    )


def _format_evidence_chain_field(beat_type: str, key_evidence: list[str]) -> str:
    """Return the evidence_chain JSON field spec for reveal chapters.

    Empty string for non-reveal so the format string needs no conditional logic.
    """
    if beat_type != "reveal":
        return ""
    if not key_evidence:
        return ',\n   "evidence_chain": []'
    examples = ",\n     ".join(
        f'{{"evidence": "{e}", "finding": "观察到的现象", "conclusion": "该证据排除/确认了什么"}}'
        for e in key_evidence
    )
    return f',\n   "evidence_chain": [\n     {examples}\n   ]'


def _build_p1_lessons(patches: list[dict]) -> str:
    """Render P1 patches as soft lessons (flexible handling allowed)."""
    p1 = [p for p in patches if p.get("severity") == "P1"]
    if not p1:
        return "（无）"
    return "\n".join(f"- [{p['anchor']}] {p['problem']}" for p in p1)


def _format_must_include(plan) -> str:
    """Render must_include_events as a numbered list (0-based) for event_manifest reference."""
    events = plan.get("must_include_events", [])
    if not events:
        return "（无）"
    return "\n".join(f"{i}. {e}" for i, e in enumerate(events))


def _parse_writer_output(raw: str) -> tuple[str, list[dict], list[dict], list[dict], list[dict]]:
    """Extract chapter_text and all manifests from writer JSON output.

    Returns (chapter_text, clue_manifest, event_manifest, foreshadow_manifest, evidence_chain).
    Falls back to (raw, [], [], [], []) on parse failure.
    """
    raw = raw.strip()
    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)```", raw, re.DOTALL)
    candidate = fence_match.group(1).strip() if fence_match else raw

    def _safe_list(obj: dict, key: str) -> list[dict]:
        val = obj.get(key) or []
        return val if isinstance(val, list) else []

    def _try_parse(s: str) -> tuple[str, list[dict], list[dict], list[dict], list[dict]] | None:
        try:
            obj = json.loads(s)
        except json.JSONDecodeError:
            return None
        if not isinstance(obj, dict) or "chapter_text" not in obj:
            return None
        text = (obj.get("chapter_text") or "").strip()
        return (
            text,
            _safe_list(obj, "clue_manifest"),
            _safe_list(obj, "event_manifest"),
            _safe_list(obj, "foreshadow_manifest"),
            _safe_list(obj, "evidence_chain"),
        )

    result = _try_parse(candidate)
    if result:
        return result

    start = candidate.find("{")
    end = candidate.rfind("}")
    if start != -1 and end > start:
        result = _try_parse(candidate[start:end + 1])
        if result:
            return result

    print(
        "  [Writer] WARNING: output is not valid JSON — treating as plain prose, all manifests=[]\n"
        "  (All target items fall back to LLM-based audit checks.)"
    )
    return raw, [], [], [], []


def writer(state: GraphState) -> dict:
    llm = get_llm()
    plan = state["current_plan"]
    truth = state["truth"]
    invariants = state["invariants"]

    # POV character known info
    character_known_info = "（无特定已知信息）"
    for char in truth.get("characters", []):
        if char["name"] == plan["perspective_character"]:
            character_known_info = char.get("known_info", character_known_info)
            break

    # Look up the beat type for this chapter (must come first)
    beat_type = "investigation"
    for b in state["blueprint"].get("story_beats", []):
        if b.get("chapter_number") == plan["chapter_number"]:
            beat_type = b.get("beat_type", "investigation")
            break

    target_clues_detail = _format_target_clues(state)
    target_foreshadows_detail = _format_target_foreshadows(state)
    must_include_events = _format_must_include(plan)
    p0_constraints = _build_p0_constraints(invariants)
    trick_reveal_block = _format_trick_reveal_block(plan)
    key_evidence_list = (plan.get("trick_detail") or {}).get("key_evidence", [])
    evidence_chain_field = _format_evidence_chain_field(beat_type, key_evidence_list)

    audit = state.get("audit_result")
    has_draft = bool(state.get("current_draft"))
    audit_failed = bool(audit and not audit["passed"])

    # Degradation detection: if must_fix count did not decrease across last two
    # audits, patch-mode is stuck — fall back to a clean full rewrite.
    history = state.get("must_fix_count_history", []) or []
    is_degraded = (
        audit_failed
        and has_draft
        and len(history) >= 2
        and history[-1] >= history[-2]
    )

    # P0 violations require structural rewrite, not surgical patch.
    patches = (audit.get("patches", []) or []) if audit else []
    has_p0 = any(p.get("severity") == "P0" for p in patches)

    if not audit_failed or not has_draft:
        write_mode = "fresh"
    elif has_p0 or is_degraded:
        write_mode = "rewrite_with_lessons"
    else:
        write_mode = "patch"

    common_kwargs = dict(
        chapter_number=plan["chapter_number"],
        chapter_title=plan.get("chapter_title", f"第{plan['chapter_number']}章"),
        beat_type=beat_type,
        perspective_character=plan["perspective_character"],
        scene_summary=plan["scene_summary"],
        emotional_tone=plan["emotional_tone"],
        target_clues_detail=target_clues_detail,
        target_foreshadows_detail=target_foreshadows_detail,
        must_include_events=must_include_events,
        invariants=json.dumps(invariants, ensure_ascii=False, indent=2),
        p0_constraints=p0_constraints,
        trick_reveal_block=trick_reveal_block,
        evidence_chain_field=evidence_chain_field,
    )

    if write_mode == "patch":
        patches = audit.get("patches", []) or []
        patches_block = _format_patches_block(patches)
        numbered_previous_draft = number_paragraphs(state["current_draft"])
        p0_count = sum(1 for p in patches if p["severity"] == "P0")
        p1_count = sum(1 for p in patches if p["severity"] == "P1")
        prompt = WRITER_REVISE_PROMPT.format(
            **common_kwargs,
            numbered_previous_draft=numbered_previous_draft,
            patches_block=patches_block,
        )
        mode_label = (
            f"[PATCH] 修改第 {plan['chapter_number']} 章 "
            f"(第{state['retry_count']}次, P0={p0_count} P1={p1_count})"
        )

    elif write_mode == "rewrite_with_lessons":
        all_patches = audit.get("patches", []) or []
        p0_fix_instructions = _build_p0_fix_instructions(all_patches)
        p1_lessons = _build_p1_lessons(all_patches)
        prompt = WRITER_REWRITE_WITH_LESSONS_PROMPT.format(
            **common_kwargs,
            character_known_info=character_known_info,
            runtime_state=json.dumps(state["runtime_state"], ensure_ascii=False, indent=2),
            p0_fix_instructions=p0_fix_instructions,
            p1_lessons=p1_lessons,
        )
        mode_label = (
            f"[REWRITE] 重写第 {plan['chapter_number']} 章「{plan.get('chapter_title', '')}」"
            f" (局部修改已退化，从零重写)"
        )

    else:  # fresh
        prompt = WRITER_PROMPT.format(
            **common_kwargs,
            character_known_info=character_known_info,
            runtime_state=json.dumps(state["runtime_state"], ensure_ascii=False, indent=2),
        )
        mode_label = (
            f"撰写第 {plan['chapter_number']} 章「{plan.get('chapter_title', '')}」"
            f" [{beat_type}]"
        )

    print(f"\n{'─'*50}")
    print(f"  ✍ 正在{mode_label}...")
    print(f"{'─'*50}\n")

    raw_output = _stream_with_retry(llm, prompt, mode_label)
    chapter_text, clue_manifest, event_manifest, foreshadow_manifest, evidence_chain = (
        _parse_writer_output(raw_output)
    )

    print(f"\n\n{'─'*50}")
    print(f"  ✍ {mode_label}完成 ({len(chapter_text)} 字)")
    target_clues = plan.get("target_clues", [])
    target_events = plan.get("must_include_events", [])
    target_fs = plan.get("target_foreshadows", [])
    print(f"  [MANIFEST] clues  : {[m.get('clue_id') for m in clue_manifest]}"
          f" / expected {target_clues}")
    print(f"  [MANIFEST] events : {[m.get('event_ref') for m in event_manifest]}"
          f" / expected 0..{len(target_events)-1}" if target_events else "")
    print(f"  [MANIFEST] fs     : {[m.get('fs_id') for m in foreshadow_manifest]}"
          f" / expected {target_fs}")
    if beat_type == "reveal":
        print(f"  [MANIFEST] evidence_chain: {[e.get('evidence') for e in evidence_chain]}"
              f" / expected {key_evidence_list}")
    print(f"{'─'*50}\n")

    return {
        "current_draft": chapter_text,
        "clue_manifest": clue_manifest,
        "event_manifest": event_manifest,
        "foreshadow_manifest": foreshadow_manifest,
        "evidence_chain": evidence_chain,
    }
