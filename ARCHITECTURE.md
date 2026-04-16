# Mystery-Composer 技术架构文档

> 一个基于 LangGraph 的自动化推理小说生成系统。输入一句话场景描述，输出结构完整、逻辑自洽、线索可追溯的短篇推理小说。

---

## 项目结构

```
ai_detective_story/
├── main.py                          # 入口：示例场景 + run() 函数
└── mystery_composer/
    ├── state.py                     # 全局状态定义（TypedDict）
    ├── graph.py                     # LangGraph 图构建 + 路由逻辑
    ├── llm.py                       # LLM 初始化（OpenAI 兼容接口）
    ├── json_utils.py                # safe_parse_json（含 LLM 自修复）
    ├── prompts/
    │   └── templates.py             # 所有 LLM prompt 模板（8 个）
    └── nodes/
        ├── constraint_extractor.py  # Stage ①
        ├── blueprint_designer.py    # Stage ②
        ├── trick_designer.py        # Stage ③
        ├── director.py              # Stage ④ Director + Replan
        ├── writer.py                # Stage ④ Writer（三模式）
        ├── auditor.py               # Stage ④ Auditor（两阶段）
        └── final_validator.py       # Stage ⑤
```

---

## 运行方式

```bash
# 使用默认场景
python main.py

# 传入自定义场景
python main.py "博物馆闭馆后，镇馆之宝消失，所有监控均无异常"

# 环境变量
OPENAI_API_KEY=...
OPENAI_API_BASE=...   # 可选，指向兼容接口
OPENAI_MODEL=...      # 默认 gpt-4o-mini
```

内置示例场景（`main.py` 中 `SAMPLE_SCENARIOS`）：

| 键 | 类型 | 描述 |
|----|------|------|
| `locked_room` | 密室 | 雪夜山间别墅，剧作家死于反锁书房 |
| `whodunit` | 凶手是谁 | 巡回剧团主演后台暴毙，全员有不在场证明 |
| `whydunit` | 为何发生 | 三十年前的信预言了昨天的车祸 |
| `serial` | 连环案 | 疗养院七晚七起谋杀，监控显示无人离床 |

---

## 核心架构：LangGraph 状态机

整个系统是一个**有向图**，节点是 LLM 调用或纯函数，边是条件路由函数。

```
constraint_extractor (①)
        ↓
blueprint_designer (②)
        ↓
trick_designer (③)
        ↓
    director (④) ◄─────────────────────┐
        ↓                              │
     writer (④)                  director_replan
        ↓                              ↑
    auditor (④)                        │
        ↓                              │
  route_after_audit ──retry──────────► writer
        │            ──replan──────────┘
        │            ──commit──► commit_chapter
        ↓                              ↓
                              route_after_commit
                                       │
                          ┌────────────┴────────────┐
                     next_chapter              final_validator (⑤)
                          │                         ↓
                       director              route_after_final
                     （下一章循环）          pass → 输出 | fail → closure_writer
```

Stage ④ 是一个**内嵌循环**：每章最多重试 `max_retries` 次，失败后升级到 replan，再失败则 force-commit。

---

## 五阶段 Pipeline 详解

### Stage ① — Constraint Extractor（硬约束锁定）

**职责**：将用户一句话输入转化为机器可校验的 JSON，禁止后续阶段改写用户意图。

**关键设计 — `key_entities` 的 scope 字段**：

每条用户约束带有时间标注，解决"案发前描述被误判为违反案发状态"的问题：

```json
"key_entities": [
  {"fact": "死者是独居者", "scope": "always_true"},
  {"fact": "书房门从内部反锁", "scope": "at_discovery"},
  {"fact": "唯一的钥匙在死者手中", "scope": "at_discovery"}
]
```

- `always_true`：全篇任意时刻均成立（人物属性、物品数量、地点特征）
- `at_discovery`：仅约束现场被发现时的物理状态；不约束案发前角色的日常习惯

**输出**：`invariants`（写入全局状态后冻结，全篇不可修改）

---

### Stage ② — Blueprint Designer（故事骨架）

**职责**：在 Invariants 基础上生成冻结的故事结构，后续节点只能读取，不得修改。

**输出**：角色表 + 地点表 + StoryBeat 序列

```json
"story_beats": [
  {
    "beat_index": 1, "chapter_number": 1,
    "chapter_title": "序章标题",
    "beat_type": "incident",        // incident|investigation|discovery|confrontation|reveal
    "key_event": "核心事件（一句话）",
    "casualties": [], "new_clues": ["clue_1", "clue_2"]
  }
]
```

**约束**：4–8 章；首章通常为 `incident`；末章必须为 `reveal`；`pov_mode` 默认为 `single`（全篇单一视角）。

---

### Stage ③ — Trick Designer（诡计与伏笔系统）

**职责**：设计核心诡计的完整逻辑闭环，以及全部伏笔的埋设/回收计划。

**输出**：

```json
{
  "one_line_method": "一句话概括手法",
  "steps": ["步骤1", "步骤2", "步骤3", "步骤4"],
  "central_trick_explanation": "如何正面解释 paradox",
  "truth": {
    "culprit": "凶手（取自 Blueprint.characters）",
    "motive": "...",
    "key_evidence": ["证据1", "证据2"]
  },
  "clues": [{"id": "clue_1", "description": "...", "importance": "critical|major|minor"}],
  "foreshadows": [
    {"id": "fs_1", "plant_chapter": 2, "payoff_chapter": 6, "real_meaning": "..."}
  ]
}
```

**约束**：≥8 条线索覆盖物证/证言/环境/行为四类；所有 `critical` 线索必须在结局前 revealed；每条伏笔必须有明确 payoff 章节；若 `paradox` 非空，`central_trick_explanation` 必须正面回答。

---

### Stage ④ — Director-Writer-Auditor 循环（核心）

#### Director — 章节规划

每章开始运行，将 StoryBeat 转化为具体写作指令：

```json
{
  "target_clues": ["clue_2", "clue_3"],   // 最多4条（reveal章除外）
  "target_foreshadows": ["fs_1"],
  "must_include_events": [                 // 最多3条，只写[结果]节点
    "[结果] 调查者进入案发现场，发现受害者，随后报警"
  ],
  "trick_detail": { ... }                  // reveal章专属：完整诡计设计
}
```

**reveal 章特殊处理**：
- `trick.steps` 逐条注入 `must_include_events`（强制 Writer 按设计手法写）
- 完整 `trick_detail` 写入计划，包含凶手/动机/步骤/关键证据
- 所有仍处于 `hidden` 的 `critical` 线索自动加入 `target_clues`

**Director Replan**：当 Writer 反复卡死时，缩减 `target_clues`（只能减不能增），调整场景描述，重置 retry 计数。纯 P0 违规不触发 replan（plan 调整解决不了措辞问题）。

---

#### Writer — 三模式写作

根据审计历史自动选择模式：

| 模式 | 触发条件 | 特点 |
|------|---------|------|
| `fresh` | 首次撰写 | 全新章节 |
| `patch` | 审计失败且 must_fix 数在减少 | 定点修改，未命中段落逐字保留 |
| `rewrite_with_lessons` | P0 违规 或 must_fix 数未减少 | 从零重写，携带 P0 强制指令 + P1 教训 |

**输入约束层次**（优先级从高到低）：

1. `p0_constraints`：用户指定的精确措辞，动词/数字不可替换为近义词
2. `trick_reveal_block`（reveal 章）：完整诡计方案，标注"不可更改"
3. `p0_fix_instructions`（rewrite 模式）：上稿 P0 错误的逐条强制修复指令
4. `p1_lessons`（rewrite 模式）：P1 教训，可灵活处理
5. 章节计划（线索、伏笔、must_include_events）

**Writer 输出 — 自注释 JSON**：

Writer 在生成正文的同时，声明自己在哪里完成了哪些要求（manifest），供审计器校验：

```json
{
  "chapter_text": "完整章节正文（首行=章节标题）",
  "clue_manifest": [
    {"clue_id": "clue_2", "excerpt": "从正文逐字摘录15-40字", "paragraph": "§3"}
  ],
  "event_manifest": [
    {"event_ref": 0, "paragraphs": ["§2", "§3"], "note": "可选说明"}
  ],
  "foreshadow_manifest": [
    {"fs_id": "fs_1", "action": "plant", "excerpt": "...", "paragraph": "§4",
     "explanation": "payoff时必填：该伏笔如何指向谜底"}
  ],
  "evidence_chain": [                                    // reveal章专属
    {"evidence": "证据名称", "finding": "观察现象", "conclusion": "推理结论"}
  ]
}
```

---

#### Auditor — 两阶段审计

**Phase 1 — 规则引擎（无 LLM，零误判率）**：

```
_rule_clue_check(clue_manifest, target_clues, chapter_text)
  ├─ 有 manifest + excerpt 命中正文 → passed（加入 stable_passed_clue_ids）
  ├─ 有 manifest + excerpt 未命中 → P1 patch（含调试信息 best=0.XX nearest=「...」）
  └─ 无 manifest → 转交 Phase 2 LLM 语义核查

_rule_event_check(event_manifest, must_include_events)
  └─ 仅检查 event_ref 是否存在（存在性检查，不做内容匹配）

_rule_foreshadow_check(foreshadow_manifest, target_foreshadows, chapter_text)
  ├─ excerpt 命中正文 → passed
  ├─ payoff 条目额外验证 explanation 字段非空
  └─ 失败 → P1 patch

_rule_evidence_chain_check(evidence_chain, key_evidence, beat_type)
  └─ reveal 章专属：验证每条 key_evidence 有对应 conclusion（非空）
```

**核心算法 — 归一化模糊匹配**：

```python
def _normalize_for_match(text):
    # 去除所有标点和空格，保留汉字和字母数字
    return re.sub(r'[^\u4e00-\u9fff\u3400-\u4dbf\w]', '', text)

def _excerpt_in_text(excerpt, text, threshold=0.75):
    norm_e = _normalize_for_match(excerpt)
    norm_t = _normalize_for_match(text)
    if norm_e in norm_t: return True          # 精确匹配（去标点后）
    n = len(norm_e)
    step = max(1, n // 8)                     # 步长=窗口长度/8
    for start in range(0, len(norm_t)-n+1, step):
        window = norm_t[start:start+n]
        if SequenceMatcher(None, norm_e, window).ratio() >= threshold:
            return True
    return False
```

**Phase 2 — P0-only LLM**（仅处理规则引擎无法覆盖的语义问题）：

| 检查项 | 触发条件 | 说明 |
|--------|---------|------|
| P0-1 | 所有章节 | 不变量违反（含 scope 时间语境规则） |
| P0-2 | 所有章节 | 视角穿帮（叙述者越权泄露信息） |
| P0-3 | 非 reveal 章 | 提前揭晓凶手（含戏中戏豁免） |
| P0-4 | reveal 章 | 缺失收束（凶手/动机/手法未回答） |
| P0-5 | reveal 章 | 诡计手法与 trick.steps 不一致 |
| LLM-P1 | unverified clues 非空 | 无 manifest 记录的线索的语义核查 |

**时间语境规则（P0-1 的核心）**：

- `beat_type ∈ {incident, discovery, reveal}` → 检查 `at_discovery` 条目（这些节拍就是发现过程）
- `beat_type ∈ {investigation, confrontation}` → 跳过 `at_discovery` 条目（已建立的事实，无需重复校验）
- 不确定性豁免：含"似乎/好像/看起来/隐约/可能"等词 → 视为初步观察，不标 P0-1

**P0-3 戏中戏豁免**：若内容由明确虚构框架引导（"剧本里""故事中""假设"等），视为叙事第二层，不标 P0-3。

---

#### 路由逻辑 — 5 级决策树

```python
def route_after_audit(state):
    if must_fix == 0:                               return "commit"    # ✓ 通过
    if history[-3:] 全相同 and > 0:                 return "commit"    # Circuit Breaker
    if retry_count < max_retries:                   return "retry"     # 继续重试
    if p1_unfixed > 0 and replan_count < MAX:       return "replan"    # 升级 replan
    return "commit"                                                    # 最终兜底
```

**关键机制**：

- **Circuit Breaker**：`must_fix_count_history` 记录每次审计结果，连续 3 次相同且 > 0 → 强制跳出死循环
- **P1 自动降级**：同一条 P1 连续失败 ≥ 2 次 → 自动降为 P2（`must_fix=False`），追踪键存于 `p1_fail_history`
- **Stable 保护**：`stable_passed_clue_ids` 记录已验证通过的线索，跨次不重复审计
- **Force-commit 透明化**：强制提交时将未解决的 P0 violation 写入 `runtime_state.unresolved_violations`，下游章节可感知

---

### Stage ⑤ — Final Validator（终局验证）

**三项检查**：
1. **伏笔回收率**：`trick.foreshadows` 中每条是否都 paid_off
2. **悬念闭环**：正文中明显的问题/悬念是否在结局得到回答
3. **reveal 完整性**：凶手身份/动机/手法/关键证据是否均有正面表述

若不通过：触发 `closure_writer` 节点补写 reveal 章，最多一次机会。

---

## 全局状态设计

`GraphState`（`state.py`）是贯穿整个图的单一可变对象：

```python
class GraphState(TypedDict):
    # ── 冻结区（Stage①②③ 填写，之后只读）──────────────────
    user_input: str
    invariants: dict             # P0 硬约束
    blueprint: dict              # 故事骨架（角色+地点+StoryBeat）
    trick: dict                  # 诡计+伏笔登记表
    clues: list[Clue]            # 线索状态表 hidden→hinted→revealed
    truth: dict                  # 真相汇总（blueprint.characters 的镜像）
    total_chapters: int

    # ── 运行时快照（每章 commit 后更新）─────────────────────
    chapters: list[str]          # 已完成章节正文列表
    runtime_state: RuntimeState
        # current_chapter, committed_events, casualties_so_far
        # exposed_clue_ids, paid_off_foreshadow_ids
        # unresolved_violations    ← force-commit 时记录未解决的 P0

    # ── 章节内循环追踪（每章 commit/replan 时重置）──────────
    current_plan: ChapterPlan | None
    current_draft: str
    audit_result: AuditResult | None
    retry_count: int
    replan_count: int
    max_retries: int

    # Writer 自注释（每章重置）
    clue_manifest: list[dict]
    event_manifest: list[dict]
    foreshadow_manifest: list[dict]
    evidence_chain: list[dict]   # reveal 章专属

    # 审计辅助（每章重置）
    stable_passed_clue_ids: list # 已验证线索（跨次不重复审计）
    must_fix_count_history: list # Circuit Breaker 依据
    p1_fail_history: dict        # P1 自动降级计数器
```

---

## 设计原则

| 原则 | 实现方式 |
|------|---------|
| **类型无关** | 所有 prompt 使用抽象占位符，无场景特定词汇；支持密室/连环/失踪/盗窃等任意子类型 |
| **声明式验证** | Writer 自注释输出（manifest），审计器验证声明，不依赖 LLM 重读全文 |
| **分层审计** | 规则引擎（快速、零误判）+ LLM（仅查语义）；各司其职，互不干扰 |
| **时间语境感知** | `scope=at_discovery` 区分"发现时状态"与"全篇常量"，防止预发现描述被误判 |
| **稳定保护** | `stable_passed_clue_ids` 防止已通过线索反复重审；降低每轮 LLM 调用量 |
| **死循环防护** | P1 自动降级 + Circuit Breaker 双重机制，保证流程必然终止 |
| **设计-生成一致** | reveal 章强制注入 `trick.steps`，P0-5 核查手法不偏离 trick designer 设计 |
| **质量降级透明** | force-commit 时将未解决违规写入 `unresolved_violations`，下游可感知遗留问题 |

---

## 依赖

```
langgraph          # 状态机图框架
langchain-openai   # LLM 接口（兼容任何 OpenAI 协议的服务）
python-dotenv      # 环境变量加载（可选）
difflib            # 标准库，用于模糊匹配（SequenceMatcher）
```
