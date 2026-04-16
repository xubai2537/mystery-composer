"""Mystery Composer - Main Entry Point (5-stage pipeline).

Usage:
    python main.py                     # Run with default demo scenario
    python main.py "your scenario"     # Run with custom scenario
"""

import sys
import os
import json
from datetime import datetime

# Load .env file if python-dotenv is available
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from mystery_composer.graph import build_graph

# A few sample scenarios that span different sub-genres of short detective fiction.
# Pick one as the default by name; users can also pass their own via CLI.
SAMPLE_SCENARIOS = {
    "locked_room": (
        "雪夜的山间别墅里，独居的剧作家被发现死在反锁的书房中。"
        "唯一的钥匙握在他自己手里，窗外的雪地上没有任何脚印。"
    ),
    "whodunit": (
        "巡回剧团的主演在演出谢幕后于后台暴毙。"
        "案发时所有团员都在台上或观众席内，谁都没有作案时间——"
        "可剧团团长坚称这是一起谋杀。"
    ),
    "whydunit": (
        "小镇邮局收到一封三十年前寄出的信，"
        "信中精确预言了昨天傍晚的一起车祸，"
        "包括司机的姓名和最后一句遗言。"
    ),
    "serial": (
        "与世隔绝的疗养院里，住着七名患有记忆障碍的病人。"
        "从入住的第一晚开始，每晚都有一名病人被谋杀，连续七晚。"
        "但所有监控录像都显示：每一位受害者从未离开过自己的床位。"
    ),
}

DEFAULT_SCENARIO = SAMPLE_SCENARIOS["whodunit"]


def run(scenario: str | None = None, max_retries: int = 3):
    scenario = scenario or DEFAULT_SCENARIO

    print("=" * 60)
    print("  Mystery-Composer (5-Stage Pipeline)")
    print("=" * 60)
    print(f"\n[Input] {scenario}\n")

    graph = build_graph()

    initial_state = {
        "user_input": scenario,
        "invariants": None,
        "blueprint": None,
        "trick": None,
        "truth": {},
        "clues": [],
        "chapters": [],
        "runtime_state": {
            "current_chapter": 0,
            "committed_events": [],
            "casualties_so_far": [],
            "exposed_clue_ids": [],
            "paid_off_foreshadow_ids": [],
            "character_locations": {},
            "unresolved_violations": [],
        },
        "current_plan": None,
        "current_draft": "",
        "audit_result": None,
        "retry_count": 0,
        "max_retries": max_retries,
        "replan_count": 0,
        "total_chapters": 0,  # set by blueprint_designer
        "stable_passed_clue_ids": [],
        "must_fix_count_history": [],
        "final_validation": None,
        "closure_retry_count": 0,
        "clue_manifest": [],
        "event_manifest": [],
        "foreshadow_manifest": [],
        "evidence_chain": [],
        "p1_fail_history": {},
    }

    print("[Stage ①] Locking user input as P0 invariants...\n")

    final_state = {}
    for event in graph.stream(initial_state, {"recursion_limit": 200}):
        for node_name, output in event.items():
            final_state.update(output)

            if node_name == "constraint_extractor":
                print()  # Stage ① already printed inside the node
                print("[Stage ②] Building immutable story blueprint...\n")

            elif node_name == "blueprint_designer":
                print()
                print("[Stage ③] Designing central trick & foreshadow registry...\n")

            elif node_name == "trick_designer":
                print()
                print("[Stage ④] Per-chapter generation loop begins.\n")

            elif node_name == "director":
                plan = output.get("current_plan", {}) or {}
                print(
                    f"[Stage ④ · Director] Chapter {plan.get('chapter_number', '?')} "
                    f"/ Beat {plan.get('beat_index', '?')} 「{plan.get('chapter_title', '')}」"
                )
                print(f"  - POV: {plan.get('perspective_character', '?')}")
                print(f"  - Tone: {plan.get('emotional_tone', '?')}")
                print(f"  - Target clues: {plan.get('target_clues', [])}")
                print(f"  - Target foreshadows: {plan.get('target_foreshadows', [])}\n")

            elif node_name == "auditor":
                audit = output.get("audit_result", {}) or {}
                status = "PASSED" if audit.get("passed") else "FAILED"
                patches = audit.get("patches", []) or []
                p0 = sum(1 for p in patches if p["severity"] == "P0")
                p1 = sum(1 for p in patches if p["severity"] == "P1")
                p2 = sum(1 for p in patches if p["severity"] == "P2")
                print(f"[Stage ④ · Auditor] {status}  (P0={p0} P1={p1} P2={p2})")
                if audit.get("invariant_violations"):
                    print("  P0 violations:")
                    for v in audit["invariant_violations"]:
                        print(f"    ! {v}")
                if not audit.get("passed"):
                    for p in patches:
                        if p.get("must_fix"):
                            print(f"  - [{p['severity']}|{p['anchor']}] {p['problem']}")
                            print(f"      fix: {p['fix_instruction']}")
                    print(f"  Retry count: {output.get('retry_count', 0)}\n")
                else:
                    print()

            elif node_name == "director_replan":
                new_plan = output.get("current_plan") or {}
                print(
                    f"[Stage ④ · Director Replan] retry budget exhausted; "
                    f"plan relaxed (replan #{output.get('replan_count', '?')})"
                )
                if new_plan:
                    print(f"  - new target_clues: {new_plan.get('target_clues', [])}")
                    print(f"  - reason: {new_plan.get('replan_reason', '')}\n")

            elif node_name == "commit":
                chapters = output.get("chapters", [])
                print(f"[Stage ④ · Commit] Chapter {len(chapters)} committed.\n")
                print("-" * 40)

            elif node_name == "final_validator":
                pass  # printed inside the node

            elif node_name == "rewrite_closure":
                print("[Stage ⑤ · Closure Gate] Rewriting closure chapter...\n")

    final_chapters = final_state.get("chapters", [])

    output_dir = "output"
    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    novel_path = os.path.join(output_dir, f"novel_{timestamp}.txt")
    with open(novel_path, "w", encoding="utf-8") as f:
        f.write("# Mystery-Composer Output\n")
        f.write(f"# Scenario: {scenario}\n")
        f.write(f"# Generated: {timestamp}\n\n")
        for chapter in final_chapters:
            f.write(f"\n{'='*40}\n")
            f.write(chapter)
            f.write(f"\n{'='*40}\n")

    truth_path = os.path.join(output_dir, f"truth_{timestamp}.json")
    with open(truth_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "invariants": final_state.get("invariants"),
                "blueprint": final_state.get("blueprint"),
                "trick": final_state.get("trick"),
                "truth": final_state.get("truth"),
                "clues": final_state.get("clues"),
                "final_validation": final_state.get("final_validation"),
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    print(f"\n{'='*60}")
    print(f"  Generation complete! {len(final_chapters)} chapters generated.")
    print(f"  Novel saved to:   {novel_path}")
    print(f"  Design saved to:  {truth_path}")
    print(f"{'='*60}")


if __name__ == "__main__":
    scenario = sys.argv[1] if len(sys.argv) > 1 else None
    run(scenario)
