"""Stage ② - Blueprint Designer (story_beats based, genre-agnostic)."""

import json

from mystery_composer.state import GraphState
from mystery_composer.llm import get_llm
from mystery_composer.json_utils import safe_parse_json
from mystery_composer.prompts.templates import BLUEPRINT_DESIGNER_PROMPT


MIN_BEATS = 4
MAX_BEATS = 8


def blueprint_designer(state: GraphState) -> dict:
    llm = get_llm()
    invariants = state["invariants"]
    prompt = BLUEPRINT_DESIGNER_PROMPT.format(
        invariants=json.dumps(invariants, ensure_ascii=False, indent=2),
    )
    response = llm.invoke(prompt)
    data = safe_parse_json(response.content, llm=llm, prompt=prompt)

    blueprint = {
        "characters": data.get("characters", []) or [],
        "locations": data.get("locations", []) or [],
        "story_beats": data.get("story_beats", []) or [],
        "pov_mode": data.get("pov_mode", "single"),
        "main_pov": data.get("main_pov", ""),
    }

    # --- Normalize / clamp story_beats ---
    beats = blueprint["story_beats"]
    # Hard clamp to [MIN_BEATS, MAX_BEATS]
    if len(beats) > MAX_BEATS:
        beats = beats[:MAX_BEATS]
    # Reassign chapter_number / beat_index to be sequential and consistent
    for i, b in enumerate(beats, start=1):
        b["beat_index"] = i
        b["chapter_number"] = i
        b.setdefault("chapter_title", f"第{i}章")
        b.setdefault("beat_type", "investigation")
        b.setdefault("casualties", [])
        b.setdefault("new_clues", [])
        b.setdefault("foreshadow_actions", [])
    blueprint["story_beats"] = beats

    # --- Validation ---
    issues = []
    if len(beats) < MIN_BEATS:
        issues.append(f"story_beats 数量不足 ({len(beats)} < {MIN_BEATS})")
    if beats and beats[-1].get("beat_type") != "reveal":
        issues.append(
            f"最后一个 beat 类型必须是 reveal，当前是 {beats[-1].get('beat_type')}"
        )

    char_names = {c["name"] for c in blueprint["characters"]}
    if blueprint["main_pov"] and blueprint["main_pov"] not in char_names:
        issues.append(f"main_pov={blueprint['main_pov']} 不在人物表中")
    location_names = {loc["name"] for loc in blueprint["locations"]}
    for b in beats:
        for victim in b.get("casualties", []):
            if victim and victim not in char_names:
                issues.append(
                    f"beat {b['beat_index']} 的 casualty={victim} 不在人物表中"
                )
        loc = b.get("setting_in_chapter", "")
        if loc and location_names and loc not in location_names:
            issues.append(
                f"beat {b['beat_index']} 的 setting_in_chapter={loc} 不在地点表中"
            )

    if issues:
        print("  [Stage ②] Blueprint VALIDATION WARNINGS:")
        for i in issues:
            print(f"    - {i}")
    else:
        print("  [Stage ②] Blueprint validated OK.")

    total_chapters = len(beats)
    print(f"    characters    = {len(blueprint['characters'])}")
    print(f"    locations     = {len(blueprint['locations'])}")
    print(f"    story_beats   = {total_chapters} (last beat: "
          f"{beats[-1].get('beat_type') if beats else 'N/A'})")
    print(f"    main_pov      = {blueprint['main_pov']} ({blueprint['pov_mode']})")

    return {
        "blueprint": blueprint,
        "total_chapters": total_chapters,
    }
