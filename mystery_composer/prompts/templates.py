"""Prompt templates for the genre-agnostic short detective story pipeline.

Pipeline:
  ① CONSTRAINT_EXTRACTOR  — pin down user input as P0 invariants (faithfully)
  ② BLUEPRINT_DESIGNER    — characters / locations / story_beats
  ③ TRICK_DESIGNER        — central trick + foreshadow registry
  ④ DIRECTOR / WRITER / AUDITOR (per-chapter loop, state-aware)
     + DIRECTOR_REPLAN    — escalation when writer can't satisfy plan
  ⑤ FINAL_VALIDATOR       — foreshadow recovery + ending closure gate

Design rule: prompts must NOT bias the model toward any particular sub-genre
(no asylum, no serial murder, no impossible-monitor examples). Examples in
prompts should either be abstract placeholders ([X], [Y]) or rotate across
multiple sub-genres so the model never anchors on one template.
"""

# =============================================================================
# Stage ① - Constraint Extractor
# =============================================================================
CONSTRAINT_EXTRACTOR_PROMPT = """\
你是推理小说项目的「设定锚定官」。
你的唯一任务是把用户的一句话输入，**逐字提取**为机器可校验的硬约束清单。
不要发挥、不要改写、不要补充用户未提到的内容。

用户输入：
{user_input}

请输出严格 JSON（不要输出任何其他内容）：
{{
  "setting": "案件发生的场景与时空（用户怎么写就怎么填，不要加戏）",
  "core_premise": "用一句话复述案件主旨。允许做最小改写让句子通顺，但不得改变事实",
  "case_scale": "single_incident | serial | multi_layered；按下面的判定规则选一个",
  "key_entities": [
    {{
      "fact": "用户明确提到的关键事实（人物/物品/数量/时间/地点/约束）；对模糊词细化为一个具体实例",
      "scope": "always_true | at_discovery"
    }}
  ],
  "paradox": "若用户描述了不可能犯罪 / 中央悖论 / 异常 hook，用一句话写在这里；若没有，填空字符串",
  "other_constraints": ["其他用户明示的不可变要素，例如时间跨度、地点封闭性、特定氛围等"]
}}

case_scale 判定规则：
- **single_incident**：只有一起核心案件（如一具尸体、一次盗窃、一次绑架）
- **serial**：多起同类案件按某种节奏发生（如连续多起谋杀）
- **multi_layered**：多个看似独立的事件其实共享同一个真相

key_entities.scope 判定规则：
- **always_true**：在整个故事中任意时刻均成立的不变属性
  例：人物身份/关系、物品数量（"钥匙只有一把"）、地点封闭性、角色背景
- **at_discovery**：描述**案发现场被发现时**的物理状态（门锁状态、物品位置、尸体姿态、现场环境特征）
  例（密室型）："门从里面反锁"、"钥匙在死者手中"
  例（中毒型）："杯子里残留液体"、"死者手握信封"
  例（失踪型）："房间被翻动"、"窗户从内侧上锁"
  ※ at_discovery 事实约束的是发现现场时的观测，不约束案发前角色的日常习惯或行为

硬规则：
- 不得添加用户未提到的角色、地点、动机、节奏
- paradox 字段只在用户明说存在不可能 / 反常元素时才填，否则保持空字符串
- key_entities 要忠实，但允许把模糊词细化为一个具体实例（为了让后续阶段有可写的素材，
  而不是让你脑补剧情）
"""

# =============================================================================
# Stage ② - Blueprint Designer
# =============================================================================
BLUEPRINT_DESIGNER_PROMPT = """\
你是推理小说的「故事蓝图建筑师」。
基于已锁定的硬约束 (Invariants)，生成一个**完整、自洽、节拍清晰**的故事骨架。
骨架一旦输出将被冻结，后续所有章节都必须严格遵守。

== Invariants ==
{invariants}

请输出严格 JSON（不要输出任何其他内容）：
{{
  "characters": [
    {{
      "name": "唯一姓名（全篇不可变）",
      "role": "唯一身份（如：作家 / 律师 / 警官 / 仆役 / 邻居 ……）",
      "age_gender": "年龄性别",
      "relations": "与其他人物的关系",
      "secret": "该角色隐藏的秘密（可为'无'）",
      "final_fate": "该角色在故事结束时的最终状态",
      "known_info": "若作为视角角色，他能合理观察到哪些信息"
    }}
  ],
  "locations": [
    {{"name": "地点名", "description": "简短描述", "accessibility": "可达性规则"}}
  ],
  "story_beats": [
    {{
      "beat_index": 1,
      "chapter_number": 1,
      "chapter_title": "本章使用的章节标题（writer 会原样使用）",
      "beat_type": "incident | investigation | discovery | confrontation | reveal",
      "key_event": "本章的核心事件（一句话）",
      "setting_in_chapter": "本章发生在 locations 中的哪个地点",
      "casualties": ["本章新增的死者 / 重伤者姓名（无则填空数组）"],
      "new_clues": ["clue_1", "clue_2"],
      "foreshadow_actions": []
    }}
  ],
  "pov_mode": "single",
  "main_pov": "主视角调查者的姓名（必须取自 characters）"
}}

硬性规则：
1. **角色数量**：至少包含一名调查者（侦探/警察/记者/医师/旁观者均可，由案件类型决定），
   以及与 Invariants.case_scale 相匹配的相关角色（受害者 / 嫌疑人 / 证人 / 委托人 ……）。
2. **节拍数量**：4 ≤ len(story_beats) ≤ 8。短篇推理通常 5-6 章最合适。
3. **节拍结构**：
   - 第一个 beat 通常是 `incident`（案件发生 / 被发现）
   - 中间是 `investigation` / `discovery` / `confrontation` 的组合
   - **最后一个 beat 必须是 `reveal`**（侦探当众揭晓或独白揭示真相）
4. **章节号严格递增**，从 1 开始连续。
5. **角色一致性**：同一姓名在全篇只能对应同一身份。casualties / discovered_by 等字段引用的人名必须取自 characters。
6. **场景一致性**：每个 setting_in_chapter 必须取自 locations。
7. **chapter_title** 应贴合本章氛围（如「书房中的尸体」/「第二次审讯」/「真相」）。
   不要全篇硬套同一个标题模板。
8. **case_scale 与节拍的关系**：
   - single_incident：通常只有 1 个 incident beat，其余是 investigation / discovery / reveal
   - serial：可以有多个 incident beats，但总章数仍须 ≤ 8
   - multi_layered：incident 可分散在多个 beats，但 reveal 必须收束所有线
9. **不要**在此阶段引入 Invariants 未提到的设定（不要脑补凶器、不要新增动机）。
10. new_clues 中的 id 用占位 `clue_1, clue_2 ...`，trick_designer 阶段会建立完整 clue 表。
"""

# =============================================================================
# Stage ③ - Trick Designer
# =============================================================================
TRICK_DESIGNER_PROMPT = """\
你是推理小说的「诡计架构师」。
为已有的故事蓝图设计**核心诡计**和完整的**伏笔登记表**。

== Invariants ==
{invariants}

== Blueprint ==
{blueprint}

请输出严格 JSON（不要输出任何其他内容）：
{{
  "one_line_method": "一句话概括核心诡计：谁、用什么手法、达成什么结果",
  "method_breakdown": [
    "步骤1：动机或前置条件",
    "步骤2：作案 / 实施过程",
    "步骤3：掩饰 / 误导手段",
    "步骤4：收尾 / 留下的痕迹"
  ],
  "central_trick_explanation": "若 Invariants.paradox 非空，必须用 2-3 句话正面解释这个诡计如何让 paradox 成立，不允许绕开；若 paradox 为空，则在此处描述诡计的核心逻辑闭环",
  "consistency_checks": [
    "对与案件相关的关键物理 / 时间 / 逻辑要素，逐项给出'为什么不矛盾'的简短确认。每条一项。"
  ],
  "truth": {{
    "victim_summary": "全部被害者及死亡顺序（若无死亡，描述受害对象 / 损失）",
    "culprit": "凶手 / 主谋姓名（必须取自 Blueprint.characters）",
    "motive": "动机",
    "key_evidence": ["关键证据1", "关键证据2", ...]
  }},
  "clues": [
    {{"id": "clue_1", "description": "线索描述", "importance": "critical | major | minor"}}
  ],
  "foreshadows": [
    {{
      "id": "fs_1",
      "description": "在文本中要呈现的伏笔（具体到一件物 / 一句话 / 一处描写）",
      "plant_chapter": 在哪一章埋设,
      "payoff_chapter": 在哪一章揭示真实含义,
      "real_meaning": "该伏笔实际指向的真相"
    }}
  ]
}}

硬性规则：
1. clues 至少 8 条，覆盖**物证 / 证言 / 环境 / 行为**四类（可不均匀分布）。
2. critical 级别线索必须在故事结束前全部 revealed。
3. **每一条** foreshadow 的 payoff_chapter 都不得为 null，且必须 ≤ 总章节数。
   reveal 章节（最后一章）必须存在至少一次 payoff，用于完成解谜。
4. 凶手必须在 Blueprint.characters 中。其 final_fate 应与诡计一致（被捕 / 自杀 / 逃亡 / 被识破等）。
5. 若 Invariants.paradox 非空，central_trick_explanation 必须正面解释——
   不得通过"其实那不是真正的悖论"等方式回避。
6. 不引入 Blueprint 中不存在的人物或地点。
7. 诡计应当符合 Invariants 描述的案件规模，**不要把单一案件强行扩写成系列案件**，也不要反过来。
"""

# =============================================================================
# Stage ④ - Director (state-aware)
# =============================================================================
DIRECTOR_PROMPT = """\
你是推理小说的战略导演。请为第 {chapter_number} 章制定章节计划。
你必须严格遵守已冻结的 Invariants / Blueprint / Trick；不得改动其中任何字段。

== Invariants（P0 硬约束，违反即报错）==
{invariants}

== 本章对应的 StoryBeat（蓝图阶段已确定，不可改）==
{beat}

== 主视角设定 ==
pov_mode = {pov_mode}
main_pov = {main_pov}

== 完整线索清单及当前状态 ==
{clues}

== 伏笔登记表及当前状态 ==
{foreshadows}

== 运行时状态快照（截至上一章末尾）==
{runtime_state}

== 已完成章节概要 ==
{previous_chapters_summary}

请输出严格 JSON（不要输出任何其他内容）：
{{
  "chapter_number": {chapter_number},
  "beat_index": {beat_index},
  "chapter_title": "原样使用 StoryBeat.chapter_title",
  "target_clues": ["本章要暴露/暗示的 clue id 列表，必须取自上面的线索清单"],
  "target_foreshadows": ["本章要 plant 或 payoff 的 foreshadow id 列表"],
  "perspective_character": "本章视角角色姓名（pov_mode=single 时必须等于 main_pov）",
  "scene_summary": "本章场景与主要事件概要（2-3 句话），必须包含 StoryBeat.key_event",
  "emotional_tone": "本章情绪基调",
  "must_include_events": [
    "本章必须出现的事件1（一定包含 StoryBeat.key_event）",
    "本章必须出现的事件2（如适用：发现尸体 / 与某角色对峙 / 揭示某证据 ……）"
  ]
}}

规则：
- 时间必须单调递增。本章是 beat_index = {beat_index}，不得回到之前的节拍。
- target_foreshadows 必须覆盖 StoryBeat.foreshadow_actions 中本章应处理的项。
- 若本章 beat_type = "reveal"（最后一章），target_clues 必须包含所有仍处于 hidden 状态的 critical 线索；
  target_foreshadows 必须包含所有仍未 paid_off 的伏笔。
- pov_mode = single 时禁止切换视角。
- 不要"加戏"——本章不是 reveal 的话，就不要让侦探当众揭晓真相。
- **target_clues 最多 4 条**（reveal 章除外）。若 StoryBeat.new_clues 超过 4 条，
  只选最直接支持本章核心事件的 4 条，其余推迟到后续章节。
  线索过多会导致章节密度过高，引发审计失败率上升。

**must_include_events 编写规范（极其重要）**：
- **最多 3 条**，只写情节层面的 `[结果]` 节点。
- `[结果]`：故事中必须达成的情节状态，Writer 可自由选择叙事路径。
  - 正确示例：`[结果] 调查者进入案发现场，发现受害者，随后报警`
  - 正确示例：`[结果] 视角角色与嫌疑人正面对峙`
  - 正确示例：`[结果] 关键证人提供不在场证明，但其陈述存在漏洞`
- **禁止**写 `[事实]` 类线索细节——线索信息由 target_clues 覆盖。
  若把线索细节同时写入 must_include_events，Writer 将被双重惩罚（措辞稍有不同即两次判为失败）。
- **禁止**写"A 之后 B，然后 C"的动作序列——叙事顺序是 Writer 的创作权。
"""

# =============================================================================
# Stage ④ - Writer (state-aware)
# =============================================================================
WRITER_PROMPT = """\
你是推理小说作家。请撰写第 {chapter_number} 章。

== Invariants（写作时必须不违反）==
{invariants}

== 章节计划 ==
章节标题：{chapter_title}
本章节拍类型：{beat_type}
视角角色：{perspective_character}
场景概要：{scene_summary}
情绪基调：{emotional_tone}
本章必须出现的情节节点（按序号在 event_manifest 中引用）：
{must_include_events}
本章需要暴露/暗示的线索：
{target_clues_detail}
本章需要埋设/回收的伏笔：
{target_foreshadows_detail}

== 你（视角角色）已知的信息 ==
{character_known_info}

== 运行时状态（截至上一章末尾，本章必须与之衔接）==
{runtime_state}

== ⚠️ P0 精确约束（最高优先级，不可改写，高于一切写作美学）==
以下是用户明确指定的精确设定——其中的**动词、状态描述、数字**不可替换为近义词，
因为它们本身就是谜题的核心物证，写法错误会直接破坏推理基础：
{p0_constraints}
{trick_reveal_block}
== 重要规则 ==
1. **章节首行必须是「{chapter_title}」**（原样使用）。
2. 严格以 {perspective_character} 的视角叙事，禁止泄露角色不知道的信息。
3. 必须自然融入每一条目标线索和伏笔——不要堆砌，要叙事。
4. **严禁违反 Invariants.paradox**（若存在）。
5. 时间单向推进，不得回溯。
6. 若 beat_type ≠ "reveal"：**严禁**揭晓凶手身份或完整作案手法。
7. 若 beat_type = "reveal"：必须**正面**回答凶手身份、动机、手法，并逐一解释关键证据的指向意义。
8. 若 beat_type = "incident"：`at_discovery` 状态（现场的物理特征、关键物品位置、现场环境等）正是本章发现过程的核心内容，**应当呈现**。建议以视角角色"观察到/看见/发现"的语气引出，而非作为背景事实陈述；初步观察可使用不确定性表述（"似乎""好像"），这与最终确认并不矛盾。
9. 章节长度建议 1000-1800 字。
10. **输出格式——严格 JSON**：
   {{
     "chapter_text": "完整章节正文（首行是「{chapter_title}」）",
     "clue_manifest": [
       {{"clue_id": "clue_1", "excerpt": "从正文逐字摘录（15-40字）", "paragraph": "§N"}}
     ],
     "event_manifest": [
       {{"event_ref": 0, "paragraphs": ["§2", "§3"], "note": "可选说明"}}
     ],
     "foreshadow_manifest": [
       {{"fs_id": "fs_1", "action": "plant|payoff", "excerpt": "从正文逐字摘录（15-40字）", "paragraph": "§N", "explanation": "payoff 条目必填：该伏笔如何指向谜底"}}
     ]{evidence_chain_field}
   }}
   填写规则：
   - clue_manifest：每条 target_clue 各一个条目；excerpt 必须是 chapter_text 中**真实存在**的原文片段（逐字复制）。
   - event_manifest：每个序号各一个条目；paragraphs 填写覆盖该情节节点的段落编号列表（不需要 excerpt）。
   - foreshadow_manifest：每条 target_foreshadow 各一个条目；excerpt 必须是正文逐字原文；action=payoff 时必须填写 explanation（一句话说明该伏笔如何指向谜底）。
   - evidence_chain（reveal 章专属）：key_evidence 中每条证据各一个条目；finding 填观察现象，conclusion 填推理结论（如"排除外部入侵的可能性"）。
   - 未登记的条目将被视为缺失，自动生成 P1 审计警告。
"""

WRITER_REVISE_PROMPT = """\
你是推理小说作家。请对上一稿做**最小化定向修改**：只修改 patches 列表中明确指向的段落，
其余段落必须**逐字保留**。这是一次外科手术，不是重写。

== Invariants（仍然不可违反）==
{invariants}

== 章节计划 ==
章节标题：{chapter_title}
本章节拍类型：{beat_type}
视角角色：{perspective_character}
场景概要：{scene_summary}
情绪基调：{emotional_tone}
本章必须出现的情节节点（按序号在 event_manifest 中引用）：
{must_include_events}
本章需要暴露/暗示的线索：
{target_clues_detail}
本章需要埋设/回收的伏笔：
{target_foreshadows_detail}

== ⚠️ P0 精确约束（最高优先级，修改时严禁改写，高于一切写作美学）==
以下是用户明确指定的精确设定——其中的**动词、状态描述、数字**不可替换为近义词，
因为它们本身就是谜题的核心物证，写法错误会直接破坏推理基础：
{p0_constraints}
{trick_reveal_block}
== 上一稿（已分段编号 §1, §2, ...）==
{numbered_previous_draft}

== 审计员的 Patches 列表（按 severity 降序）==
{patches_block}

修改协议（必须严格遵守）：
1. **段落对齐**：保持与上一稿相同的段落结构。允许在被 patch 命中的段落内部做局部增删；
   不允许新增或删除整段，除非某条 patch 明确要求。
2. **白名单修改**：只允许修改 patches.anchor 提到的段落（§N）。
   未被任何 patch 命中的段落必须**逐字原样复制**。
3. **P0 必修**：所有 severity=P0 的 patch 必须 100% 应用。
4. **must_fix=true 的 patch**：必须应用。
5. **冲突处理**：优先满足 P0，其次按 id 顺序；无法应用时在文末附
   `[writer_note] cannot_apply: pX, reason: ...`。
6. 章节首行仍然是「{chapter_title}」。
7. **输出格式——严格 JSON**：
   {{
     "chapter_text": "修改后的完整章节正文（不含 §N 标记）",
     "clue_manifest": [
       {{"clue_id": "clue_1", "excerpt": "从 chapter_text 逐字摘录（15-40字）", "paragraph": "§N"}}
     ],
     "event_manifest": [
       {{"event_ref": 0, "paragraphs": ["§2", "§3"]}}
     ],
     "foreshadow_manifest": [
       {{"fs_id": "fs_1", "action": "plant|payoff", "excerpt": "从正文逐字摘录（15-40字）", "paragraph": "§N", "explanation": "payoff 条目必填"}}
     ]{evidence_chain_field}
   }}
   - clue_manifest 涵盖所有 target_clue；excerpt 必须是修改后 chapter_text 中真实存在的原文片段（逐字复制）。
   - event_manifest 涵盖所有 must_include_events 序号；paragraphs 填段落编号列表（不需要 excerpt）。
   - foreshadow_manifest 涵盖所有 target_foreshadow；excerpt 必须是正文逐字原文；action=payoff 时 explanation 必填。
   - evidence_chain（reveal 章专属）：key_evidence 中每条证据各一个条目，conclusion 必须非空。
"""

# =============================================================================
# Stage ④ - Writer (full rewrite with accumulated lessons)
# Used when patch-mode revisions are degrading (must_fix count not decreasing).
# =============================================================================
WRITER_REWRITE_WITH_LESSONS_PROMPT = """\
你是推理小说作家。局部修改多次后效果不佳，现在请**从零开始重新撰写**本章。
不要参考之前的任何草稿——它们已经积累了太多缝补痕迹，从头写一篇干净的新稿。

== Invariants（写作时必须不违反）==
{invariants}

== 章节计划 ==
章节标题：{chapter_title}
本章节拍类型：{beat_type}
视角角色：{perspective_character}
场景概要：{scene_summary}
情绪基调：{emotional_tone}
本章必须出现的情节节点（按序号在 event_manifest 中引用）：
{must_include_events}
本章需要暴露/暗示的线索：
{target_clues_detail}
本章需要埋设/回收的伏笔：
{target_foreshadows_detail}

== 你（视角角色）已知的信息 ==
{character_known_info}

== 运行时状态（截至上一章末尾，本章必须与之衔接）==
{runtime_state}

== ⚠️ P0 精确约束（最高优先级，不可改写，高于一切写作美学）==
以下是用户明确指定的精确设定——其中的**动词、状态描述、数字**不可替换为近义词，
因为它们本身就是谜题的核心物证，写法错误会直接破坏推理基础：
{p0_constraints}
{trick_reveal_block}
== P0 必修修复项（上稿的严重错误，新稿必须 100% 修正，无创作自由）==
{p0_fix_instructions}

== P1 参考教训（上稿的软性问题，写新稿时主动规避，叙事上可灵活处理）==
{p1_lessons}

== 重要规则 ==
1. **章节首行必须是「{chapter_title}」**。
2. 严格以 {perspective_character} 的视角叙事，禁止越权。
3. 必须在叙事中**自然**融入每一条目标线索和伏笔——不要当检查表堆砌。
4. must_include_events 中每个情节节点都是 [结果]：自由选择叙事路径，达成最终状态即可。
5. 若 beat_type ≠ "reveal"：严禁揭晓凶手身份或完整手法。
6. 若 beat_type = "reveal"：必须正面回答凶手、动机、手法，并逐一解释关键证据的指向意义。
7. 章节长度建议 1000-1800 字。
8. **输出格式——严格 JSON**：
   {{
     "chapter_text": "完整章节正文（首行是「{chapter_title}」）",
     "clue_manifest": [
       {{"clue_id": "clue_1", "excerpt": "从正文逐字摘录（15-40字）", "paragraph": "§N"}}
     ],
     "event_manifest": [
       {{"event_ref": 0, "paragraphs": ["§2", "§3"]}}
     ],
     "foreshadow_manifest": [
       {{"fs_id": "fs_1", "action": "plant|payoff", "excerpt": "从正文逐字摘录（15-40字）", "paragraph": "§N", "explanation": "payoff 条目必填"}}
     ]{evidence_chain_field}
   }}
   - clue_manifest：每条 target_clue 一个条目；excerpt 必须是 chapter_text 的逐字原文（禁止改写）。
   - event_manifest：每个序号一个条目；paragraphs 填覆盖该节点的段落编号列表（不需要 excerpt）。
   - foreshadow_manifest：每条 target_foreshadow 一个条目；excerpt 必须是逐字原文；action=payoff 时 explanation 必填。
   - evidence_chain（reveal 章专属）：key_evidence 中每条证据各一个条目，conclusion 必须非空。
   - 未登记的条目将被视为缺失，自动生成 P1 审计警告。
"""

# =============================================================================
# Stage ④ - Director Replan (escalation)
# =============================================================================
DIRECTOR_REPLAN_PROMPT = """\
你是推理小说的战略导演。Writer 已经连续多次尝试本章但仍未通过审计——这通常意味着
**计划本身不可行**（线索过载、视角角色无法承载该线索、要求与运行时状态冲突等）。

请根据下面的卡死信息，对**本章计划**做有限度的调整，使写作可行。

== 你不能改的（冻结）==
- Invariants
- Blueprint（人物表 / 节拍表 / 主视角）
- Trick & Foreshadow Registry

== 你可以改的 ==
- 删减 target_clues 中的 1-2 条，把它们推迟到后续章节
- 重新挑选 target_foreshadows
- 重写 scene_summary 和 emotional_tone
- 调整 must_include_events 的措辞（但 StoryBeat.key_event 不能丢）

== 当前 Invariants ==
{invariants}

== 本章 StoryBeat（不可改部分）==
{beat}

== 当前 Blueprint 主视角 ==
main_pov = {main_pov}

== 上一次 Director 给出的计划 ==
{previous_plan}

== Writer 多次尝试后仍未解决的 patches（卡死原因）==
{stuck_patches}

== 当前线索/伏笔状态 ==
clues:
{clues}
foreshadows:
{foreshadows}

请输出**修订后**的章节计划，严格 JSON：
{{
  "chapter_number": {chapter_number},
  "beat_index": {beat_index},
  "chapter_title": "原样使用 StoryBeat.chapter_title",
  "target_clues": ["调整后的线索 id 列表（建议比上次少 1-2 条）"],
  "target_foreshadows": ["调整后的伏笔 id 列表"],
  "perspective_character": "{main_pov}",
  "scene_summary": "调整后的场景概要（务必规避卡死原因）",
  "emotional_tone": "可以保留或微调",
  "must_include_events": ["保留 StoryBeat.key_event，措辞可调"],
  "deferred_clues": ["被推迟到后续章节的 clue id 列表"],
  "replan_reason": "用一句话说明为什么之前的 plan 卡死，以及你这次怎么规避"
}}

规则：
- target_clues 必须是上次计划的子集（只能减不能增）。
- 视角角色必须保持为 {main_pov}。
- 不要尝试通过修改 plan 来"绕过" P0 invariant 违规——这类问题应当由 writer 自己解决，
  不在 replan 的能力范围内。如果发现 stuck_patches 全部是 P0 违规，请在 replan_reason
  中明说 "P0 escalation needed"，并保持 plan 不变。
"""

# =============================================================================
# Stage ④ - Auditor (invariant + structural)
# =============================================================================
AUDITOR_PROMPT = """\
你是推理小说 P0 逻辑守卫。只检查以下硬性错误——软性检查（线索/事件/伏笔覆盖）已由规则引擎完成，
无需重复。

== Invariants（P0 硬约束）==
{invariants}

== 章节基本信息 ==
章节号：第 {chapter_number} 章 / 节拍 {beat_index} ({beat_type})
视角角色：{perspective_character}（已知信息：{character_known_info}）
尚未揭示的线索（不得在本章说出其含义或解答）：{hidden_clues}
注意：视角角色**可以**直接观察到上述线索的物理外观（如物品位置、痕迹形状、人物神态等具体可见事实）；
禁止的是**解释其真实含义**（如"这说明凶手是…"）或**点名其指向凶手**。

== 若 beat_type = reveal，必须回答的内容 ==
{truth_hint}

== 若 beat_type = reveal，设计的核心诡计（用于 P0-5 核查）==
{trick_steps_hint}

== 线索验证摘要（规则引擎已处理，无需重查）==
已验证通过（跳过）：{manifest_verified_clues}
无 manifest 记录，需 LLM 语义核查：{unverified_clues}

== 运行时状态 ==
{runtime_state}

== 待审核章节文本（已分段编号 §1, §2, ...）==
{numbered_chapter_text}

== 审计范围（严格限于以下项目）==

**P0-1 不变量违反**
是否直接违反 Invariants 的 setting / core_premise / paradox / key_entities？
只有文本明确与其矛盾时才标记，不得推断或延伸。

**时间语境规则（key_entities scope）**：
key_entities 中每条事实带有 scope 字段：
- scope=always_true：全章任意时刻均可检查
- scope=at_discovery：描述**案发现场被发现时**的物理状态

  适用范围（当前 beat_type={beat_type}）：
  - beat_type ∈ {{incident, discovery, reveal}}：at_discovery 条目**正常检查**。
    这些节拍本身就是"发现过程"，应当呈现现场状态。
  - beat_type ∈ {{investigation, confrontation}} 及其他：**跳过所有 at_discovery 条目**。
    这些节拍发生在发现之后，日常回顾无需重复校验。
  - 案发前（如有 pre-incident 节拍）：at_discovery 事实描述为角色日常习惯时不得标记为违规。

  不确定性豁免：若描述包含「似乎」「好像」「看起来」「隐约」「可能」「无法确定」等不确定性标记词，
  视为角色的**初步观察**，不得判定为 P0-1 违规——初步观察与最终确认状态并不矛盾。

若违反的是 key_entities 或 paradox 中的具体措辞（动词/状态/数字被改写为近义词），
fix_instruction 必须包含三要素：
  ① 原约束原文（用户指定的确切词语）
  ② 当前违规表达（正文中实际出现的词语）
  ③ 禁用词列表（明确列出所有不可接受的近义替代词）
示例格式："原约束：「[用户指定的原文]」；当前违规：「[正文中实际写法]」；禁用词：[近义替代词列表]；请在 §N 将违规表达改回原约束原文"

**P0-2 视角穿帮**
叙述者是否泄露了 {perspective_character} 不可能知道的事实？
参照其"已知信息"字段判断；只有明确越权才算 P0，"可能不知道"不算。

**P0-3 非 reveal 章提前揭晓**
若 beat_type ≠ reveal：是否明确点名揭晓凶手身份或完整作案手法？
模糊暗示、间接怀疑不构成 P0。
**豁免**：若内容明确由虚构框架引导（角色在描述自己创作的剧本/小说/构想，或在讨论假设场景），
该内容属于叙事第二层，即使与现实案情吻合也**不视为提前揭晓**，不得标记 P0-3。
判断依据：该段落是否有明确的虚构引导词（"剧本里"、"故事中"、"我写的是"、"假设"等）？

**P0-4 reveal 章缺失收束**
若 beat_type = reveal：对照"必须回答的内容"，是否有明确缺失？
只有完全未提及才标记，叙述方式不限。

**P0-5 诡计手法一致性**（仅 beat_type=reveal 时执行）
若 beat_type=reveal：对照"设计的核心诡计"，正文中揭露的作案手法是否与设计一致？
- 核心步骤必须全部呈现（措辞不需要逐字一致，但不得替换为不同装置或不同原理）。
- 若正文发明了新装置（如梯子、绳索、暗道等）而设计方案中没有，标记 P0。
- 若核心步骤中的关键条件（时间差、物理机制、关键路径等）被省略，标记 P0。
若 beat_type ≠ reveal，跳过此检查。

**LLM 语义核查（仅当 unverified_clues 非空时执行）**
对 unverified_clues 中每条线索，用语义判断是否在正文中体现：
- 同义/近义/合理延展/隐含指向均视为满足。
- 若判定缺失：必须在 problem 字段引用 §N 原文证明信息确实不存在（举证义务）。
- 仅因措辞不直白不得标 P1；只有信息对读者完全不可获取才标 P1。

== 输出格式 ==
{{
  "patches": [
    {{
      "id": "p1",
      "severity": "P0 | P1",
      "type": "invariant_violation | missing_clue | leak | pov_break | missing_closure | other",
      "anchor": "§N 或 '全章'",
      "problem": "一句话说明问题，若举证必须引用原文",
      "fix_instruction": "具体可执行的修改指令",
      "must_fix": true
    }}
  ],
  "overall_suggestion": "整体建议（1-2 句）"
}}

规则：
- 无问题则返回空 patches 列表。
- **不要生成 P2 patches**——软性问题已由规则引擎和 Writer 自负责。
- 只有以上明确列出的 P0/LLM-P1 检查范围内才生成 patch；其他问题忽略。
- fix_instruction 必须具体（"在 §N 段末追加……"），不得含糊（"加强描写"）。
"""

# =============================================================================
# Stage ⑤ - Final Validator
# =============================================================================
FINAL_VALIDATOR_PROMPT = """\
你是推理小说的「结局闸门」。请对完整生成的故事进行终局校验。

== Invariants ==
{invariants}

== Trick & Foreshadow Registry ==
{trick}

== 全部章节正文 ==
{full_text}

== 当前各伏笔状态 ==
{foreshadows_status}

请检查以下三件事，输出严格 JSON：

1. **伏笔回收率**：trick.foreshadows 中每一条是否都已在文中得到揭示？列出所有未回收的 id。
2. **未解问题**：扫描文本中的明显悬念 / 问号 / "为什么" 句，是否都在结局得到了回答？列出未回答的。
3. **结局完整性**：reveal 章节是否同时显式回答了：
   - 凶手 / 主谋身份与动机
   - 完整作案手法（若 Invariants.paradox 非空，必须正面解释 paradox；若为空，必须解释诡计的逻辑闭环）
   - 所有 critical 关键证据的指向意义

{{
  "passed": true/false,
  "unrecovered_foreshadows": ["fs_id1", ...],
  "unanswered_questions": ["问题1", ...],
  "missing_closure_items": ["缺失项：culprit / method / evidence 中的一个或多个"],
  "suggestions": "如果不通过，给 Writer 的补写指令（聚焦在 reveal 章节）"
}}

只要任一项检查未通过，passed 必须为 false。
"""
