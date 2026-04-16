# Mystery-Composer

基于 LangGraph 的推理小说自动生成系统。输入一句话场景描述，输出结构完整、逻辑自洽、线索可追溯的短篇推理小说。

**输入：** 一句话  
**输出：** 4–8 章推理小说正文 + 记录全部设计决策的 JSON 真相文件

---

## 演示

```
输入: 巡回剧团的主演在演出谢幕后于后台暴毙。
      案发时所有团员都在台上或观众席内，谁都没有作案时间——
      可剧团团长坚称这是一起谋杀。

[Stage ①] 锁定用户输入为 P0 不变量...
[Stage ②] 构建冻结故事骨架...
[Stage ③] 设计核心诡计与伏笔系统...
[Stage ④] 逐章生成循环开始。
  [Director] 第 1 章 / Beat 1 「幕落之时」
  [Writer]   fresh 模式
  [Auditor]  PASSED  (P0=0 P1=0 P2=0)
  ...
[Stage ⑤] 终局验证...

生成完成！共 6 章。
小说已保存至：output/novel_20240416_103045.txt
设计文件已保存至：output/truth_20240416_103045.json
```

---

## 核心特性

- **类型无关** — 密室、凶手是谁、为何发生、连环案、盗窃、失踪等任意子类型均支持
- **约束保全** — 用户场景锁定为 P0 不变量，任何阶段均不得违背或重新诠释
- **Writer 自注释** — Writer 声明线索、事件、伏笔在正文中的具体位置（manifest），审计器无需重读全文即可验证
- **两阶段审计** — 规则引擎（零误判）+ LLM 语义核查（仅处理规则无法覆盖的问题）
- **时间语境感知** — `at_discovery` / `always_true` 双 scope，防止案发前描述被误判为违反案发状态
- **终止性保证** — P1 自动降级 + Circuit Breaker 双重机制，流程必然终止
- **设计-生成一致** — reveal 章强制注入 `trick_designer` 设计的步骤；P0-5 核查手法不偏离设计
- **OpenAI 兼容** — 支持任何实现 OpenAI chat completions 接口的服务

---

## 快速开始

### 1. 克隆并安装依赖

```bash
git clone https://github.com/xubai2537/mystery-composer.git
cd mystery-composer
pip install -r requirements.txt
```

### 2. 配置 API Key

在项目根目录创建 `.env` 文件：

```env
OPENAI_API_KEY=your_key_here
OPENAI_API_BASE=https://api.openai.com/v1   # 可选，用于兼容接口
OPENAI_MODEL=gpt-4o-mini                    # 可选，默认 gpt-4o-mini
```

### 3. 运行

```bash
# 使用默认场景（whodunit）
python main.py

# 传入自定义场景
python main.py "博物馆闭馆后，镇馆之宝消失，所有监控均无异常"
```

输出写入 `output/` 目录：
- `output/novel_<时间戳>.txt` — 完整小说正文
- `output/truth_<时间戳>.json` — 不变量、故事骨架、诡计设计、线索表、验证结果

---

## 内置示例场景

| 键 | 类型 | 描述 |
|----|------|------|
| `locked_room` | 密室 | 雪夜山间别墅，剧作家死于反锁书房，唯一钥匙握在死者手中，窗外雪地无脚印 |
| `whodunit` | 凶手是谁 | 巡回剧团主演后台暴毙，案发时全员均有不在场证明 |
| `whydunit` | 为何发生 | 三十年前寄出的信精确预言了昨天的车祸，包括司机姓名和遗言 |
| `serial` | 连环案 | 疗养院七名记忆障碍病人，连续七晚各一起谋杀，监控显示无人离开床位 |

修改 [main.py](main.py) 中的 `DEFAULT_SCENARIO` 可切换默认场景。

---

## 架构

系统是一个 **LangGraph 状态机** — 有向图中节点为 LLM 调用或纯函数，边为条件路由函数。

```
constraint_extractor (①)
        ↓
blueprint_designer (②)
        ↓
trick_designer (③)
        ↓
    director (④) ◄────────────────────┐
        ↓                             │
     writer (④)                 director_replan
        ↓                             ↑
    auditor (④)                       │
        ↓                             │
  route_after_audit ──retry──────► writer
        │            ──replan─────────┘
        │            ──commit──► commit_chapter
        ↓                             ↓
                             route_after_commit
                                      │
                         ┌────────────┴────────────┐
                    next_chapter             final_validator (⑤)
                         │                         ↓
                      director             route_after_final
                   （下一章循环）      通过 → 输出 | 失败 → closure_writer
```

### 五阶段 Pipeline

| 阶段 | 节点 | 职责 |
|------|------|------|
| ① | `constraint_extractor` | 将用户输入解析为机器可校验的 P0 不变量（全程冻结） |
| ② | `blueprint_designer` | 构建角色表、地点表和 StoryBeat 序列（4–8 章） |
| ③ | `trick_designer` | 设计核心诡计、线索登记表（≥8 条）和伏笔埋设/回收计划 |
| ④ | `director` → `writer` → `auditor` 循环 | 逐章生成，支持最多 `max_retries` 次重试、replan 和 force-commit 兜底 |
| ⑤ | `final_validator` → `closure_writer` | 验证伏笔回收率、叙事闭环和 reveal 完整性 |

### 关键设计

**`key_entities` scope 系统** — 每条用户约束携带时间标注：
- `always_true`：全篇任意时刻均成立（人物属性、物品数量、地点特征）
- `at_discovery`：仅约束现场被发现时的物理状态，不约束案发前的描述

这解决了"案发前章节描述被误判为违反案发状态"的常见问题。

**Writer 自注释（manifest）** — Writer 在生成正文的同时，以结构化 JSON 声明每条线索/事件/伏笔在文中的精确位置（含逐字摘录）。审计器通过模糊字符串匹配验证声明，无需 LLM 重读全文。

**两阶段审计**：
- Phase 1：确定性规则引擎 — 线索 manifest 验证、事件存在性检查、伏笔回收验证、证据链检查
- Phase 2：LLM 语义核查 — 不变量违反（P0-1）、视角穿帮（P0-2）、提前揭晓（P0-3）、结局缺失（P0-4）、诡计不一致（P0-5）

**终止性保证**：
- P1 自动降级：同一条 P1 连续失败 ≥2 次，自动降为 P2
- Circuit Breaker：`must_fix_count` 连续 3 轮相同且 > 0，强制跳出死循环

完整技术细节见 [ARCHITECTURE.md](ARCHITECTURE.md)。

---

## 项目结构

```
mystery-composer/
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

## 环境要求

- Python 3.10+
- OpenAI API Key（或任意兼容接口）
- [requirements.txt](requirements.txt) 中的依赖：
  ```
  langgraph>=0.2.0
  langchain-openai>=0.1.0
  python-dotenv>=1.0.0
  ```

---

## 配置说明

| 环境变量 | 默认值 | 说明 |
|---------|-------|------|
| `OPENAI_API_KEY` | *(必填)* | API 密钥 |
| `OPENAI_API_BASE` | OpenAI 默认 | 任意 OpenAI 兼容接口的 Base URL |
| `OPENAI_MODEL` | `gpt-4o-mini` | 所有 LLM 调用使用的模型 |

---

## License

MIT
