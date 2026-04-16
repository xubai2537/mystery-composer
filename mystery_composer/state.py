"""Mystery Composer - State Definition (5-stage, genre-agnostic pipeline)."""

from __future__ import annotations

from typing import TypedDict, Literal


class Clue(TypedDict):
    id: str
    description: str
    status: Literal["hidden", "hinted", "revealed"]
    chapter_revealed: int | None


class Foreshadow(TypedDict):
    """A planted clue that MUST be paid off before the story ends."""
    id: str
    description: str
    plant_chapter: int
    payoff_chapter: int
    real_meaning: str
    status: Literal["pending", "planted", "paid_off"]


class Invariants(TypedDict):
    """P0-level hard constraints faithfully extracted from user input.

    Genre-agnostic. None of these fields force a serial-murder structure;
    a single locked room, a heist, a why-dunit and a serial case all map
    cleanly into this schema.
    """
    setting: str                  # where & when the story takes place
    core_premise: str             # one-line case summary (replaces victim_count + rhythm)
    case_scale: Literal["single_incident", "serial", "multi_layered"]
    key_entities: list[str]       # concrete people / objects / facts the user mentioned
    paradox: str                  # central impossibility / hook; "" if none
    other_constraints: list[str]


class CharacterCard(TypedDict):
    name: str
    role: str
    age_gender: str
    relations: str
    secret: str
    final_fate: str
    known_info: str               # what this POV character knows


class StoryBeat(TypedDict):
    """A single narrative beat / chapter scene. Generic across genres.

    A beat could be a murder, a discovery, an interrogation, a reconstruction,
    or the final reveal — `beat_type` makes the role explicit.
    """
    beat_index: int
    chapter_number: int
    chapter_title: str            # writer uses this verbatim as the chapter heading
    beat_type: Literal[
        "incident", "investigation", "discovery", "confrontation", "reveal"
    ]
    key_event: str                # one-sentence focal event of the chapter
    setting_in_chapter: str       # which location (must come from Blueprint.locations)
    casualties: list[str]         # any character deaths/injuries this beat (often empty)
    new_clues: list[str]
    foreshadow_actions: list[str]


class Blueprint(TypedDict):
    """Stage ② - immutable story skeleton."""
    characters: list[CharacterCard]
    locations: list[dict]         # [{name, description, accessibility}]
    story_beats: list[StoryBeat]  # 4-8 beats; last beat MUST be of type "reveal"
    pov_mode: Literal["single", "alternating"]
    main_pov: str                 # primary investigator character


class Trick(TypedDict):
    """Stage ③ - the central trick + foreshadow registry."""
    one_line_method: str
    method_breakdown: list[str]
    central_trick_explanation: str  # if Invariants.paradox is set, must explain it head-on
    consistency_checks: list[str]   # generic physical/logical self-consistency checklist
    foreshadows: list[Foreshadow]


class RuntimeState(TypedDict):
    """Stage ④ - running snapshot updated after each committed chapter."""
    current_chapter: int
    committed_events: list[str]         # one entry per committed chapter (key_event)
    casualties_so_far: list[str]        # accumulated casualties (may stay empty)
    exposed_clue_ids: list[str]
    paid_off_foreshadow_ids: list[str]
    character_locations: dict           # name -> last known location
    unresolved_violations: list[dict]   # P0 violations force-committed; {chapter, anchor, problem}


class ChapterPlan(TypedDict):
    chapter_number: int
    beat_index: int
    chapter_title: str
    target_clues: list[str]
    target_foreshadows: list[str]
    perspective_character: str
    scene_summary: str
    emotional_tone: str
    must_include_events: list[str]
    trick_detail: dict  # reveal chapters only: culprit/motive/steps/key_evidence


class Patch(TypedDict):
    """A single, actionable correction the auditor wants the writer to apply."""
    id: str
    severity: Literal["P0", "P1", "P2"]
    type: str
    anchor: str
    problem: str
    fix_instruction: str
    must_fix: bool


class AuditResult(TypedDict):
    passed: bool
    patches: list[Patch]
    issues: list[str]
    invariant_violations: list[str]
    suggestions: str


class FinalValidation(TypedDict):
    passed: bool
    unrecovered_foreshadows: list[str]
    unanswered_questions: list[str]
    missing_closure_items: list[str]
    suggestions: str


class GraphState(TypedDict):
    user_input: str

    invariants: Invariants | None
    blueprint: Blueprint | None
    trick: Trick | None

    truth: dict
    clues: list[Clue]

    chapters: list[str]
    runtime_state: RuntimeState
    current_plan: ChapterPlan | None
    current_draft: str
    audit_result: AuditResult | None
    retry_count: int
    max_retries: int
    replan_count: int
    total_chapters: int

    # Per-chapter retry quality tracking (reset on every commit / replan)
    stable_passed_clue_ids: list[str]   # confirmed-present clues; auditor skips re-checking
    must_fix_count_history: list[int]   # must_fix count per audit attempt; detects degradation

    final_validation: FinalValidation | None
    closure_retry_count: int

    # Writer self-annotation for the current chapter draft (all reset to [] / {} on commit/replan).
    clue_manifest: list[dict]       # [{clue_id, excerpt, paragraph}]
    event_manifest: list[dict]      # [{event_ref(int), paragraphs, note}]
    foreshadow_manifest: list[dict] # [{fs_id, action, excerpt, paragraph, explanation?}]
    evidence_chain: list[dict]      # reveal only: [{evidence, finding, conclusion}]

    # Per-item P1 failure counter. Key = clue_id / "ev_N" / fs_id.
    # If count >= 1 at audit time, the P1 patch is auto-degraded to P2.
    # Reset on commit / replan.
    p1_fail_history: dict
