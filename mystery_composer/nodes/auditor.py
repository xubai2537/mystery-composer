"""Stage ④ - Auditor (rule-based P1 + P0-only LLM + P1 auto-degrade).

Audit is now split into two fully separate phases:

Phase 1 — Rule engine (no LLM, no false positives):
  • _rule_clue_check():        verifies clue_manifest excerpts in chapter_text
  • _rule_event_check():       verifies event_manifest excerpts (must_include_events)
  • _rule_foreshadow_check():  verifies foreshadow_manifest excerpts

Phase 2 — P0-only LLM:
  • Invariant violations, POV breaks, premature reveals, missing reveal closure.
  • Also handles unverified clues (those absent from manifest) via semantic judgment.

P1 auto-degrade:
  • Each soft-check item tracks its consecutive failure count in p1_fail_history.
  • If an item failed in the PREVIOUS audit (count ≥ 1), this audit's P1 patch for
    it is downgraded to P2 (must_fix=False), letting the chapter commit.
  • Counts reset to 0 on item pass, and the entire dict resets on commit/replan.
"""

import json
from difflib import SequenceMatcher

from mystery_composer.state import GraphState
from mystery_composer.llm import get_llm
from mystery_composer.json_utils import safe_parse_json
from mystery_composer.prompts.templates import AUDITOR_PROMPT


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def number_paragraphs(text: str) -> str:
    """Split text into paragraphs (by blank lines) and prefix each with §N."""
    raw_paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    return "\n\n".join(f"§{i+1} {p}" for i, p in enumerate(raw_paragraphs))


def _normalize_patch(raw: dict, idx: int) -> dict:
    severity = (raw.get("severity") or "P1").upper()
    if severity not in ("P0", "P1", "P2"):
        severity = "P1"
    must_fix = raw.get("must_fix")
    if must_fix is None:
        must_fix = severity in ("P0", "P1")
    return {
        "id": raw.get("id") or f"llm_p{idx+1}",
        "severity": severity,
        "type": raw.get("type", "other"),
        "anchor": raw.get("anchor", "全章"),
        "problem": raw.get("problem", ""),
        "fix_instruction": raw.get("fix_instruction", ""),
        "must_fix": bool(must_fix),
    }


# ---------------------------------------------------------------------------
# Fuzzy excerpt matching
# ---------------------------------------------------------------------------

import re as _re

def _normalize_for_match(text: str) -> str:
    """Strip all punctuation and whitespace for comparison (Chinese + ASCII)."""
    return _re.sub(r'[^\u4e00-\u9fff\u3400-\u4dbf\w]', '', text)


def _excerpt_in_text(excerpt: str, text: str, threshold: float = 0.75) -> bool:
    """Return True if excerpt appears verbatim or near-verbatim in text.

    Matching is done on normalized strings (punctuation/whitespace stripped) to
    tolerate minor formatting differences. Threshold is intentionally looser (0.75)
    since normalization already handles most surface variation.
    """
    if not excerpt or not text:
        return False
    norm_e = _normalize_for_match(excerpt)
    norm_t = _normalize_for_match(text)
    if not norm_e:
        return False
    # Exact match after normalization (handles punctuation/whitespace differences)
    if norm_e in norm_t:
        return True
    n = len(norm_e)
    if n < 6:
        return norm_e in norm_t
    # Sliding window fuzzy match on normalized strings
    step = max(1, n // 8)
    for start in range(0, len(norm_t) - n + 1, step):
        window = norm_t[start:start + n]
        if SequenceMatcher(None, norm_e, window).ratio() >= threshold:
            return True
    return False


def _nearest_match_debug(excerpt: str, text: str) -> str:
    """Return the best-matching window and its similarity score for debug output."""
    norm_e = _normalize_for_match(excerpt)
    norm_t = _normalize_for_match(text)
    n = len(norm_e)
    if not norm_e or len(norm_t) < n:
        return "(text too short)"
    best_ratio, best_window = 0.0, ""
    step = max(1, n // 8)
    for start in range(0, len(norm_t) - n + 1, step):
        window = norm_t[start:start + n]
        ratio = SequenceMatcher(None, norm_e, window).ratio()
        if ratio > best_ratio:
            best_ratio, best_window = ratio, window
    return f"best={best_ratio:.2f}  nearest=「{best_window[:25]}…」"


# ---------------------------------------------------------------------------
# Rule checks (Phase 1)
# ---------------------------------------------------------------------------

def _rule_clue_check(
    clue_manifest: list[dict],
    target_clue_ids: list[str],
    chapter_text: str,
    stable_ids: set[str],
    clue_desc: dict[str, str],
) -> tuple[list[dict], list[str], list[str]]:
    """Verify clue_manifest against chapter_text.

    Returns (patches, passed_ids, unverified_ids).
      passed_ids    — excerpt found; will be added to stable_passed_clue_ids
      unverified_ids — no manifest entry; forwarded to LLM
    """
    by_id = {m.get("clue_id", ""): m for m in (clue_manifest or []) if m.get("clue_id")}
    patches, passed, unverified = [], [], []

    for cid in target_clue_ids:
        if cid in stable_ids:
            continue
        if cid not in by_id:
            unverified.append(cid)
            print(f"  [CLUE] {cid} → no manifest entry → LLM fallback")
            continue
        entry = by_id[cid]
        excerpt = (entry.get("excerpt") or "").strip()
        para = entry.get("paragraph", "")
        if not excerpt:
            unverified.append(cid)
            print(f"  [CLUE] {cid} → empty excerpt → LLM fallback")
            continue
        if _excerpt_in_text(excerpt, chapter_text):
            passed.append(cid)
            print(f"  [CLUE] {cid} → ✓  {para}  「{excerpt[:30]}…」")
        else:
            short = excerpt[:50] + ("…" if len(excerpt) > 50 else "")
            desc = clue_desc.get(cid, "")
            debug = _nearest_match_debug(excerpt, chapter_text)
            print(f"  [CLUE] {cid} → ✗  excerpt not in text")
            print(f"         want: 「{excerpt[:40]}…」")
            print(f"         {debug}")
            patches.append({
                "id": f"mf_{cid}",
                "severity": "P1",
                "type": "missing_clue",
                "anchor": para or "全章",
                "problem": (
                    f"clue_manifest 声明 {cid} ({desc}) 的覆盖原文为「{short}」，"
                    f"但该文字在正文中找不到对应内容。"
                ),
                "fix_instruction": (
                    f"在正文中写入明确体现「{desc}」的句子，"
                    f"并将 clue_manifest 中 {cid} 的 excerpt 更新为新句的逐字摘录。"
                ),
                "must_fix": True,
                "_item_key": cid,
            })

    return patches, passed, unverified


def _rule_event_check(
    event_manifest: list[dict],
    must_include_events: list[str],
) -> tuple[list[dict], list[int]]:
    """Verify event_manifest has an entry for each must_include_events index.

    Events cover multi-paragraph narrative arcs — excerpt matching is unreliable.
    We only verify that the Writer registered each event_ref (presence check).
    The Writer's `paragraphs` field is logged for transparency; no content match.

    Returns (patches, passed_refs).
    """
    by_ref: dict[int, dict] = {}
    for m in (event_manifest or []):
        ref = m.get("event_ref")
        if ref is not None:
            try:
                by_ref[int(ref)] = m
            except (TypeError, ValueError):
                pass

    patches, passed = [], []
    for idx, event_text in enumerate(must_include_events or []):
        if idx not in by_ref:
            print(f"  [EVENT] {idx}: 「{event_text[:35]}」→ not registered → P1")
            patches.append({
                "id": f"ev_{idx}",
                "severity": "P1",
                "type": "missing_event",
                "anchor": "全章",
                "problem": f"must_include_events[{idx}] 「{event_text}」未在 event_manifest 中登记。",
                "fix_instruction": (
                    f"确认正文中该情节节点已达成，并在 event_manifest 中以 "
                    f'event_ref={idx}, paragraphs=["§N",...] 登记覆盖它的段落编号。'
                ),
                "must_fix": True,
                "_item_key": f"ev_{idx}",
            })
        else:
            paras = by_ref[idx].get("paragraphs") or []
            print(f"  [EVENT] {idx}: ✓  registered at {paras}  「{event_text[:35]}」")
            passed.append(idx)

    return patches, passed


def _rule_evidence_chain_check(
    evidence_chain: list[dict],
    key_evidence: list[str],
    beat_type: str,
) -> list[dict]:
    """For reveal chapters, verify every key_evidence item has a conclusion.

    Returns a list of P1 patches for missing or empty entries.
    """
    if beat_type != "reveal" or not key_evidence:
        return []

    chain_by_name = {e.get("evidence", ""): e for e in (evidence_chain or [])}
    patches = []

    for ev in key_evidence:
        # Fuzzy match: evidence name may be slightly paraphrased
        matched_entry = None
        for name, entry in chain_by_name.items():
            if ev in name or name in ev or _excerpt_in_text(ev, name, threshold=0.6):
                matched_entry = entry
                break

        if matched_entry is None:
            patches.append({
                "id": f"ec_{ev[:12].replace(' ', '_')}",
                "severity": "P1",
                "type": "missing_closure",
                "anchor": "全章",
                "problem": f"关键证据「{ev}」未在 evidence_chain 中登记推理结论。",
                "fix_instruction": (
                    f"在 evidence_chain 中为「{ev}」添加条目，"
                    f"填写 finding（观察到的现象）和 conclusion（排除/确认了什么推理结论）。"
                ),
                "must_fix": True,
                "_item_key": f"ec_{ev[:12]}",
            })
            print(f"  [EVIDENCE] 「{ev[:20]}」→ not in evidence_chain → P1")
        elif not (matched_entry.get("conclusion") or "").strip():
            patches.append({
                "id": f"ec_{ev[:12].replace(' ', '_')}_empty",
                "severity": "P1",
                "type": "missing_closure",
                "anchor": "全章",
                "problem": f"evidence_chain 中「{ev}」的 conclusion 为空，缺少推理结论。",
                "fix_instruction": (
                    f"填写「{ev}」的 conclusion 字段，"
                    "说明该证据排除/确认了什么推理可能性（如「排除雪停后外部入侵」）。"
                ),
                "must_fix": True,
                "_item_key": f"ec_{ev[:12]}_empty",
            })
            print(f"  [EVIDENCE] 「{ev[:20]}」→ conclusion empty → P1")
        else:
            print(f"  [EVIDENCE] 「{ev[:20]}」→ ✓  {matched_entry.get('conclusion', '')[:40]}")

    return patches


def _rule_foreshadow_check(
    foreshadow_manifest: list[dict],
    target_fs_ids: list[str],
    chapter_text: str,
    all_foreshadows: list[dict],
) -> tuple[list[dict], list[str]]:
    """Verify foreshadow_manifest against chapter_text.

    Returns (patches, passed_fs_ids).
    """
    by_id = {m.get("fs_id", ""): m for m in (foreshadow_manifest or []) if m.get("fs_id")}
    fs_desc = {fs["id"]: fs.get("description", "") for fs in all_foreshadows}
    patches, passed = [], []

    for fs_id in target_fs_ids:
        if fs_id not in by_id:
            desc = fs_desc.get(fs_id, "")
            print(f"  [FS] {fs_id}: no manifest entry → P1")
            patches.append({
                "id": f"fs_{fs_id}",
                "severity": "P1",
                "type": "missing_foreshadow",
                "anchor": "全章",
                "problem": f"伏笔 {fs_id} ({desc}) 未在 foreshadow_manifest 中登记。",
                "fix_instruction": (
                    f"在正文中写入对 {fs_id} 的埋设/揭示内容，"
                    f"并在 foreshadow_manifest 中登记 fs_id={fs_id} 及逐字 excerpt。"
                ),
                "must_fix": True,
                "_item_key": fs_id,
            })
            continue
        entry = by_id[fs_id]
        excerpt = (entry.get("excerpt") or "").strip()
        para = entry.get("paragraph", "")
        action = entry.get("action", "?")
        if not excerpt:
            print(f"  [FS] {fs_id}: empty excerpt → P1")
            patches.append({
                "id": f"fs_{fs_id}",
                "severity": "P1",
                "type": "missing_foreshadow",
                "anchor": para or "全章",
                "problem": f"foreshadow_manifest 中 {fs_id} 的 excerpt 为空。",
                "fix_instruction": f"为 {fs_id} 填写正文中的逐字 excerpt。",
                "must_fix": True,
                "_item_key": fs_id,
            })
            continue
        if _excerpt_in_text(excerpt, chapter_text):
            passed.append(fs_id)
            print(f"  [FS] {fs_id} [{action}]: ✓  {para}  「{excerpt[:30]}…」")
            # For payoff entries, also require explanation field
            if action == "payoff" and not (entry.get("explanation") or "").strip():
                print(f"  [FS] {fs_id}: payoff missing explanation → P1")
                patches.append({
                    "id": f"fs_{fs_id}_explain",
                    "severity": "P1",
                    "type": "missing_closure",
                    "anchor": para or "全章",
                    "problem": (
                        f"伏笔 {fs_id} 的 payoff 条目缺少 explanation 字段，"
                        f"无法验证该伏笔是否真正被解释并连接到谜底。"
                    ),
                    "fix_instruction": (
                        f"在 foreshadow_manifest 的 {fs_id} payoff 条目中填写 explanation，"
                        f"一句话说明该伏笔如何指向谜底（如「林远山破门后看向窗台，"
                        f"后来在那里发现旧雪，证明窗户曾在雪停前被打开」）。"
                    ),
                    "must_fix": True,
                    "_item_key": f"{fs_id}_explain",
                })
        else:
            short = excerpt[:50] + ("…" if len(excerpt) > 50 else "")
            desc = fs_desc.get(fs_id, "")
            debug = _nearest_match_debug(excerpt, chapter_text)
            print(f"  [FS] {fs_id}: ✗  excerpt not in text")
            print(f"         want: 「{excerpt[:40]}…」")
            print(f"         {debug}")
            patches.append({
                "id": f"fs_{fs_id}",
                "severity": "P1",
                "type": "missing_foreshadow",
                "anchor": para or "全章",
                "problem": (
                    f"foreshadow_manifest 声明 {fs_id} ({desc}) 的覆盖原文为「{short}」，"
                    f"但该文字在正文中找不到。"
                ),
                "fix_instruction": (
                    f"在正文中补写 {fs_id} 的{action}内容，"
                    f"并更新 foreshadow_manifest 中对应的 excerpt。"
                ),
                "must_fix": True,
                "_item_key": fs_id,
            })

    return patches, passed


# ---------------------------------------------------------------------------
# P1 auto-degrade
# ---------------------------------------------------------------------------

def _apply_p1_degrade(
    patches: list[dict],
    p1_fail_history: dict,
) -> tuple[list[dict], dict]:
    """Downgrade P1 patches to P2 if the item failed in a previous audit.

    Also computes the updated p1_fail_history for next audit:
      - failed item  → count + 1
      - passed item  → count reset (key removed)

    Returns (updated_patches, new_p1_fail_history).
    """
    new_history = dict(p1_fail_history)
    failing_keys: set[str] = set()

    for p in patches:
        key = p.pop("_item_key", None)  # extract internal tracking key
        if p["severity"] == "P1" and p.get("must_fix"):
            if key:
                failing_keys.add(key)
                prev_count = p1_fail_history.get(key, 0)
                if prev_count >= 1:
                    # This item already failed last time → degrade
                    p["severity"] = "P2"
                    p["must_fix"] = False
                    p["problem"] = f"[AUTO-DEGRADED P1→P2 after {prev_count+1} attempts] " + p["problem"]
                    print(f"  [DEGRADE] {key} → P2 (was P1 × {prev_count+1} consecutive)")

    # Update history: increment for failures, remove for passes
    for key in failing_keys:
        new_history[key] = new_history.get(key, 0) + 1
    # Keys tracked but not failing this time → reset
    for key in list(new_history.keys()):
        if key not in failing_keys:
            del new_history[key]

    return patches, new_history


# ---------------------------------------------------------------------------
# Auditor node
# ---------------------------------------------------------------------------

def _clue_ids_failing_in_llm(patches: list[dict], candidate_ids: list[str]) -> set[str]:
    """Return subset of candidate_ids mentioned in must_fix LLM patches."""
    failing = set()
    for p in patches:
        if not p.get("must_fix"):
            continue
        haystack = " ".join([p.get("anchor", ""), p.get("problem", ""), p.get("fix_instruction", "")])
        for cid in candidate_ids:
            if cid in haystack:
                failing.add(cid)
    return failing


def auditor(state: GraphState) -> dict:
    llm = get_llm()
    plan = state["current_plan"]
    invariants = state["invariants"]
    trick = state["trick"]

    stable_ids = set(state.get("stable_passed_clue_ids", []) or [])
    p1_fail_history = dict(state.get("p1_fail_history", {}) or {})

    # ── Build per-clue lookup tables ──────────────────────────────────────────
    clue_desc: dict[str, str] = {}
    hidden_clue_lines: list[str] = []
    for clue in state["clues"]:
        clue_desc[clue["id"]] = clue["description"]
        if clue["id"] in plan["target_clues"] and clue["id"] not in stable_ids:
            pass  # target clue — writer handles it
        elif clue["status"] == "hidden":
            # NOTE: POV characters CAN observe physical facts at the scene.
            # "hidden" means the SOLUTION/INTERPRETATION should not be spelled out.
            hidden_clue_lines.append(f"- {clue['id']}: {clue['description']}")

    effective_target_clue_ids = [
        cid for cid in plan["target_clues"] if cid not in stable_ids
    ]

    # ── Phase 1: Rule checks ──────────────────────────────────────────────────
    chapter_text = state["current_draft"]
    clue_manifest = state.get("clue_manifest", []) or []
    event_manifest = state.get("event_manifest", []) or []
    foreshadow_manifest = state.get("foreshadow_manifest", []) or []
    evidence_chain = state.get("evidence_chain", []) or []
    all_foreshadows = trick.get("foreshadows", [])
    target_fs_ids = plan.get("target_foreshadows", [])
    must_include_events = plan.get("must_include_events", [])

    # Beat type lookup (needed for Phase 1 evidence_chain check)
    beat_type = "investigation"
    for b in state["blueprint"].get("story_beats", []):
        if b.get("chapter_number") == plan["chapter_number"]:
            beat_type = b.get("beat_type", "investigation")
            break

    print(f"\n  [Auditor] Phase 1 — Rule checks")
    if stable_ids:
        print(f"  [Auditor] Stable (skipping): {sorted(stable_ids)}")

    clue_patches, clue_passed, unverified_clue_ids = _rule_clue_check(
        clue_manifest, effective_target_clue_ids, chapter_text, stable_ids, clue_desc,
    )
    event_patches, event_passed_refs = _rule_event_check(
        event_manifest, must_include_events,
    )
    fs_patches, fs_passed = _rule_foreshadow_check(
        foreshadow_manifest, target_fs_ids, chapter_text, all_foreshadows,
    )
    key_evidence = (plan.get("trick_detail") or {}).get("key_evidence", [])
    evidence_chain_patches = _rule_evidence_chain_check(evidence_chain, key_evidence, beat_type)

    all_rule_patches = clue_patches + event_patches + fs_patches + evidence_chain_patches

    # ── Phase 2: P0-only LLM ─────────────────────────────────────────────────
    print(f"\n  [Auditor] Phase 2 — P0-only LLM")

    # character_known_info
    character_known_info = "（无特定已知信息）"
    for char in state["blueprint"].get("characters", []):
        if char["name"] == plan["perspective_character"]:
            character_known_info = char.get("known_info", character_known_info)
            break

    # truth_hint + trick_steps_hint (for reveal checks)
    if beat_type == "reveal":
        t = trick.get("truth", {})
        truth_hint = (
            f"凶手/主谋：{t.get('culprit', '?')}  "
            f"动机：{t.get('motive', '?')}  "
            f"手法：{trick.get('one_line_method', '?')}  "
            f"关键证据：{t.get('key_evidence', [])}"
        )
        steps = trick.get("steps", [])
        trick_steps_hint = (
            f"一句话手法：{trick.get('one_line_method', '?')}\n"
            + ("步骤：\n" + "\n".join(f"  {i+1}. {s}" for i, s in enumerate(steps))
               if steps else "步骤：（无）")
        )
    else:
        truth_hint = "（非 reveal 章，此项不适用）"
        trick_steps_hint = "（非 reveal 章，跳过 P0-5）"

    # manifest_verified_clues string
    all_verified_ids = sorted(
        set(clue_passed) | (stable_ids & set(plan["target_clues"]))
    )
    manifest_verified_text = (
        "\n".join(f"- {cid}: {clue_desc.get(cid, '')}" for cid in all_verified_ids)
        or "（无）"
    )
    unverified_text = (
        "\n".join(f"- {cid}: {clue_desc.get(cid, '')}" for cid in unverified_clue_ids)
        or "（无——所有目标线索均已由 manifest 核实）"
    )

    numbered_chapter_text = number_paragraphs(chapter_text)

    prompt = AUDITOR_PROMPT.format(
        invariants=json.dumps(invariants, ensure_ascii=False, indent=2),
        chapter_number=plan["chapter_number"],
        beat_index=plan.get("beat_index", plan["chapter_number"]),
        beat_type=beat_type,
        perspective_character=plan["perspective_character"],
        character_known_info=character_known_info,
        hidden_clues="\n".join(hidden_clue_lines) if hidden_clue_lines else "（无）",
        truth_hint=truth_hint,
        trick_steps_hint=trick_steps_hint,
        manifest_verified_clues=manifest_verified_text,
        unverified_clues=unverified_text,
        runtime_state=json.dumps(state["runtime_state"], ensure_ascii=False, indent=2),
        numbered_chapter_text=numbered_chapter_text,
    )

    response = llm.invoke(prompt)
    result = safe_parse_json(response.content, llm=llm, prompt=prompt)
    raw_llm_patches = result.get("patches", []) or []
    llm_patches = [_normalize_patch(p, i) for i, p in enumerate(raw_llm_patches)]

    # Tag LLM unverified-clue patches with _item_key for degrade tracking
    for p in llm_patches:
        if p["severity"] == "P1" and p["type"] == "missing_clue":
            for cid in unverified_clue_ids:
                if cid in p.get("problem", "") or cid in p.get("fix_instruction", ""):
                    p["_item_key"] = cid
                    break

    # ── Merge and apply P1 degrade ────────────────────────────────────────────
    all_patches = all_rule_patches + llm_patches
    all_patches, new_p1_fail_history = _apply_p1_degrade(all_patches, p1_fail_history)

    # ── Update stable_passed_clue_ids ─────────────────────────────────────────
    # LLM-verified unverified clues (no must_fix LLM patch mentioning them)
    llm_failing_ids = _clue_ids_failing_in_llm(
        [p for p in llm_patches if p.get("must_fix")], unverified_clue_ids
    )
    newly_stable_llm = [cid for cid in unverified_clue_ids if cid not in llm_failing_ids]
    newly_stable = sorted(set(clue_passed) | set(newly_stable_llm))
    updated_stable = sorted(stable_ids | set(newly_stable))
    if newly_stable:
        print(f"  [Auditor] Newly stable: {newly_stable}")

    # ── Audit result ──────────────────────────────────────────────────────────
    must_fix_unresolved = [p for p in all_patches if p.get("must_fix")]
    must_fix_count = len(must_fix_unresolved)
    history = list(state.get("must_fix_count_history", []) or [])
    history.append(must_fix_count)

    passed = must_fix_count == 0
    print(
        f"\n  [Auditor] Rule patches: {len(all_rule_patches)} | LLM patches: {len(llm_patches)} "
        f"| must_fix: {must_fix_count} | history: {history}"
    )

    issues = [
        f"[{p['severity']}|{p['anchor']}] {p['problem']}"
        for p in all_patches
    ]
    p0_summaries = [
        f"[{p['anchor']}] {p['problem']}" for p in all_patches if p["severity"] == "P0"
    ]

    return {
        "audit_result": {
            "passed": passed,
            "patches": all_patches,
            "issues": issues,
            "invariant_violations": p0_summaries,
            "suggestions": result.get("overall_suggestion", ""),
        },
        "retry_count": state["retry_count"] + 1,
        "stable_passed_clue_ids": updated_stable,
        "must_fix_count_history": history,
        "p1_fail_history": new_p1_fail_history,
    }
