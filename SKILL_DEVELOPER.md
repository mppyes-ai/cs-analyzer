# CS-Analyzer 开发者文档 v2.6.4

> 本文档供开发者在修改系统代码、修复Bug时使用。
> 最后更新：2026-04-05
> 
> **v2.6.4 更新**: 消息服务稳定性增强 - PID监控 + 崩溃恢复 + 必达消息
> **v2.6.3 更新**: Worker可靠性修复 - 启动前依赖检查 + 启动失败即时通知

---

## 1. 你的身份与工作原则

**角色**：谨慎的系统维护者，不是乐于提供方案的通用助手

**目标**：在最小改动范围内安全地修复问题

**原则**：不确定时明确说"我需要先确认"，而不是给出可能错误的答案

---

## 2. 修改代码前必须做的事

1. **确认已阅读本文档**：每次对话开始时，检查本文档版本
2. **声明影响范围**：任何修改方案提出前，先列出"可能受影响的模块"
3. **检查禁区**：对照已知陷阱列表，验证方案是否触犯禁区
4. **使用diff格式**：代码修改始终以diff格式输出（+新增/-删除），不重写整个文件

---

## 3. 项目身份

**项目名称**：CS-Analyzer（客服质检分析系统）
**核心功能**：自动分析客服聊天记录，通过意图分类 + 情绪分析 + LLM 评分，输出四维质检报告（专业性/标准化/政策执行/转化能力）。
**技术栈**：Python 3.9+ · SQLite（主库 + 队列库） · LanceDB（向量检索） · Moonshot/Kimi API（评分） · Ollama + qwen2.5:7b（意图/情绪） · SentenceTransformer（向量嵌入）
**推荐入口**：`cs_analyzer_batch.py`（v2.4 批处理入口，支持异步批量模式）

---

## 4. 目录结构与模块列表

```
cs-analyzer/
├── cs_analyzer_batch.py    # [入口] 批处理命令行入口，加载.env，调用BatchAnalyzer
├── batch_analyzer.py       # [控制器] 前台/后台模式控制，幂等检查，Worker管理
├── monitor_agent.py        # [监控] 后台子进程，轮询进度，写消息到文件
├── message_poller.py       # [消息] 轮询读取文件，调用openclaw CLI发送飞书消息
├── worker.py               # [核心] 四模式Worker（serial/parallel/grouped/async-batch），消费队列
├── smart_scoring_v2.py     # [评分] SmartScoringEngine，支持单通/批量评分
├── intent_classifier_v3.py # [意图] RobustIntentClassifier，五层漏斗分类
├── sentiment_analyzer.py   # [情绪] SentimentAnalyzer，调用qwen2.5:7b或关键词降级
├── ollama_client.py        # [客户端] 健壮Ollama HTTP客户端，连接池+退避重试
├── keywords_extended.py    # [关键词] 扩展关键词库，口语化映射，商品链接识别
├── hybrid_retriever.py     # [检索] HybridRuleRetriever，RRF融合全文+向量检索
├── knowledge_base_v2.py    # [规则库] SQLite规则表初始化和查询工具
├── db_utils.py             # [数据库] 分析结果读写，矫正记录，会话管理
├── task_queue.py           # [队列] SQLite任务队列，任务生命周期管理
├── merge_sessions.py       # [合并] 会话合并和转接链构建逻辑
├── log_parser.py           # [解析] 聊天记录解析，HTML清理，角色识别，会话切割
├── analyze_log.py          # [旧入口] 已知Bug(H-1)，生产环境不建议使用
├── embedding_utils.py      # [工具] Embedding模型单例管理器
├── config.py               # [配置] 全局配置集中定义
└── transfer_analyzer.py    # [转接] 转接会话分析和质量评估
```

## 5. 数据库结构

### 5.1 主库：cs_analyzer_new.db

**analysis_tasks**（任务队列，路径：data/task_queue.db）
```sql
task_id       TEXT PRIMARY KEY   -- UUID
session_id    TEXT               -- 业务 ID，用于幂等检查
session_data  TEXT               -- JSON序列化的完整会话数据
status        TEXT               -- pending | processing | completed | failed | cancelled
result        TEXT               -- JSON序列化的评分结果
error         TEXT               -- 错误信息
retry_count   INTEGER DEFAULT 0  -- 重试次数，最多3次
created_at    DATETIME
started_at    DATETIME
completed_at  DATETIME
```

**sessions**（分析结果主表）
```sql
session_id        TEXT PRIMARY KEY
user_id           TEXT
staff_name        TEXT
messages          TEXT    -- JSON数组
summary           TEXT
session_count     INTEGER DEFAULT 1
start_time        TEXT
end_time          TEXT
professionalism_score INTEGER    -- 1-5分
standardization_score INTEGER      -- 1-5分
policy_execution_score INTEGER     -- 1-5分
conversion_score INTEGER           -- 1-5分
total_score       INTEGER          -- 4-20分
strengths         TEXT    -- JSON数组
issues            TEXT    -- JSON数组
suggestions       TEXT    -- JSON数组
analysis_json     TEXT    -- 完整分析JSON
is_transfer       BOOLEAN DEFAULT 0
transfer_from     TEXT
transfer_to       TEXT
transfer_reason   TEXT
related_sessions  TEXT    -- JSON数组
created_at        DATETIME
```

**rules**（评分规则）
```sql
rule_id           TEXT PRIMARY KEY
rule_type         TEXT DEFAULT 'scoring'
scene_category    TEXT    -- 售前阶段/售中阶段/售后阶段/客诉处理
scene_sub_category TEXT
trigger_keywords  TEXT    -- JSON数组
trigger_intent    TEXT
rule_dimension    TEXT    -- professionalism/standardization/policy_execution/conversion
rule_criteria     TEXT
rule_score_guide  TEXT    -- JSON：评分指导
status            TEXT DEFAULT 'pending'  -- pending/approved/rejected
```

### 5.2 队列库：data/task_queue.db（独立SQLite文件）

与主库分离，仅存 analysis_tasks 表。

---

## 6. 关键变量与命名约定

### 6.1 session_id 生成规则
```python
# 由 log_parser.py 生成
session_id = f'session_{idx:04d}_{hashlib.md5(first_content.encode()).hexdigest()[:8]}'
```

### 6.2 status 枚举值（analysis_tasks）
- `pending`：已提交，等待处理
- `processing`：Worker 已取出，正在处理
- `completed`：处理完成
- `failed`：处理失败（retry_count < 3 会被重试）
- `cancelled`：已取消（合并时被取消）

### 6.3 Worker 模式枚举（v2.4更新）
- `--serial`：单线程串行（最稳定）
- `--parallel`：ThreadPoolExecutor并行
- `--grouped`：按user_id分组，组内串行、组间并行
- `--async-batch`：【v2.4新增】组间并行+组内异步+批量评分（推荐）

### 6.4 评分维度名称
```python
DIMENSIONS = {
    "professionalism": "专业性",      # 1-5分
    "standardization": "标准化",      # 1-5分
    "policy_execution": "政策执行",   # 1-5分
    "conversion": "转化能力"          # 1-5分
}
# 总分 = 4-20分，风险分级：🔴≤8 🟡9-12 🟢≥13
```

### 6.5 PID 文件路径（v2.3 Worker锁机制，H-5修复）
```python
PID_FILE = '/tmp/cs_analyzer_worker.pid'
# ✅ H-5已修复：使用PID文件+进程存在性双重检测替代Socket检测
```

### 6.6 核心配置键名（.env）（v2.6.2更新）
```bash
WORKER_MODE=async-batch          # serial | parallel | grouped | async-batch
WORKER_MAX_GROUPS=6
# 【v2.6.2】WORKER_BATCH_SIZE 已废弃，改用 WORKER_MAX_BATCH_SIZE
WORKER_MAX_BATCH_SIZE=150        # 单次处理上限（智能感知队列数量）
BATCH_SCORE_SIZE=30              # 基础批量评分大小
KIMI_MAX_CONCURRENT=90           # Kimi API并发限制
KIMI_MAX_TOKENS=32000            # 【v2.6.2】提升以支持20通/批
MERGE_WINDOW_MINUTES=30
MOONSHOT_API_KEY=sk-xxxx

# 【v2.6.2】Token估算配置
TOKENS_PER_CHAR=0.67
OUTPUT_TOKENS_PER_SESSION=600
SYSTEM_PROMPT_TOKENS=900
MAX_TOKENS_PER_BATCH=200000
ADAPTIVE_BATCH_MIN=10
ADAPTIVE_BATCH_MAX=50  # 注意：实际限制为20（代码硬编码）

---

---

## 7. 异步批量模式架构

### 7.1 v2.6.2 智能批量架构（方案B）

**解决的问题：**
- **上游瓶颈**：`fetch_and_group_tasks(batch_size=20)` 每次只取20个，自适应算法无用武之地
- **API返回截断**：50通/批时API只返回20个结果，其余为空
- **Worker残留**：旧Worker进程未清理导致新代码不生效

**架构设计：**
```
┌─────────────────────────────────────────────────────────────┐
│           async-batch 架构 v2.6.2（智能批量）               │
├─────────────────────────────────────────────────────────────┤
│  【v2.6.2】智能批量流程：                                    │
│  1. fetch_and_group_tasks()                                  │
│     ├─ count pending总数                                     │
│     ├─ 如果 ≤150：全部取出                                   │
│     └─ 如果 >150：取前150个                                  │
│                                                             │
│  2. calculate_adaptive_batch_size()                          │
│     ├─ 估算Token                                             │
│     ├─ 计算最优批量（上限20）                                │
│     └─ 返回 min(计算值, 20, 剩余任务数)                      │
│                                                             │
│  3. _batch_score_with_limit_v2()                             │
│     ├─ 按20通/批分组                                         │
│     ├─ 多批并行（asyncio.gather）                            │
│     └─ 每批独立竞争Kimi信号量                                │
│                                                             │
│  示例: 50通会话                                              │
│    ├─ 全部取出（50通 < 150上限）                            │
│    ├─ 自适应计算：50通 → 分3批（20+20+10）                  │
│    ├─ 3批并行评分                                           │
│    └─ API调用：3次（vs 之前8次）                            │
└─────────────────────────────────────────────────────────────┘
```

**关键代码位置（v2.6.2）：**

**worker.py**
```python
def fetch_and_group_tasks(max_batch_size: int = 150) -> Dict[str, List[Dict]]:
    """【v2.6.2】智能获取：先count总数，再决定取多少"""
    cursor.execute("SELECT COUNT(*) FROM analysis_tasks WHERE status = 'pending'")
    total_pending = cursor.fetchone()[0]
    limit = total_pending if total_pending <= max_batch_size else max_batch_size
    # ...

def calculate_adaptive_batch_size(sessions: List[Dict], base_size: int = 30) -> int:
    """【v2.6.2】限制最大20通/批，避免API返回截断"""
    # ...计算逻辑...
    return min(potential_size, 20, len(sessions))  # 硬编码限制20
```

### 7.2 v2.5 跨用户场景分组（旧架构，已整合）
解决大规模分析时的性能瓶颈：
- **组内串行问题**：单个Worker等待API响应时CPU空转
- **API调用次数多**：每通会话独立HTTP请求
- **并发不可控**：可能触发Kimi API限流
- **【v2.5新增】重复预分析**：同场景会话被重复分类

### 7.2 架构设计（v2.5跨用户场景分组）
```
┌─────────────────────────────────────────────────────────────┐
│              async-batch 架构 v2.5（跨用户优化）            │
├─────────────────────────────────────────────────────────────┤
│  【v2.5优化】跨用户场景分组流程：                           │
│  1. 收集所有任务（跨用户）                                  │
│  2. _fast_scene_classify() 快速场景分类（关键词规则）       │
│  3. 按场景分组（售前/售中/售后/客诉）                       │
│  4. 同场景内批量评分（5通/批）                              │
│                                                             │
│  示例: 22通会话                                             │
│    ├─ 售前组(15通) → 3批 × 1次API调用 = 3次                │
│    ├─ 售中组(4通)  → 1批 × 1次API调用 = 1次                │
│    └─ 售后组(3通)  → 1批 × 1次API调用 = 1次                │
│                                                             │
│  总计: 5次API调用 vs 22次 = 节省77%                        │
└─────────────────────────────────────────────────────────────┘
```

### 7.3 核心代码位置（v2.5更新）

**worker.py**
```python
async def run_async_batch_worker(...):
    """【v2.5】异步批量Worker主循环（跨用户场景分组）"""
    # 1. 获取所有任务（跨用户）
    # 2. 【v2.5】_fast_scene_classify() 快速场景分类
    # 3. 【v2.5】按场景分组（不再按user_id分组）
    # 4. 同场景批量评分

async def _fast_scene_classify(messages: List[Dict]) -> str:
    """【v2.5】快速场景分类（关键词规则，不调用Ollama）"""
    # 毫秒级分类，避免LLM调用开销

async def _batch_score_with_limit_v2(tasks, batch_size):
    """【v2.5】带限流的批量评分（传入预分析结果）"""
    # 构建预分析结果，跳过内部重复分析
    # 调用score_sessions_batch_async(sessions, pre_analyses)
```

**smart_scoring_v2.py**
```python
async def score_sessions_batch_async(
    self, 
    sessions: List[Dict],
    pre_analyses: List[Dict] = None  # 【v2.5】可选预分析结果
) -> List[Dict]:
    """【v2.5】异步批量评分（支持传入预分析结果）"""
    if pre_analyses is None:
        # 只有在未提供时才进行预分析
        pre_analyses = await self._pre_analyze_sessions(sessions)
    # 按场景分组后批量评分
```

### 7.4 关键配置
```python
# worker.py 全局信号量
kimi_semaphore = asyncio.Semaphore(KIMI_MAX_CONCURRENT)

# 【v2.5】快速场景分类关键词
PRE_SALE_KEYWORDS = ['咨询', '推荐', '多少钱']
POST_SALE_KEYWORDS = ['订单', '发货', '物流']
AFTER_SALE_KEYWORDS = ['安装', '维修', '故障']
```

---

## 8. 完整数据流

### 8.1 后台批处理链路（v2.4 async-batch）
```
cs_analyzer_batch.py → BatchAnalyzer
  → log_parser.parse_log_file()
  → BatchAnalyzer.reset_stale_tasks()
  → BatchAnalyzer.is_already_analyzed()
  → task_queue.submit_task()
  → BatchAnalyzer.check_worker_running()
  → BatchAnalyzer.start_worker()  # 【v2.4】支持async-batch模式
    → worker.py --async-batch
      → run_async_batch_worker()
        → fetch_and_group_tasks()
        → asyncio.gather(process_group_async(...))
          → _batch_score_with_limit()
            → scorer.score_sessions_batch_async()
  → BatchAnalyzer.spawn_monitor_agent()
  → BatchAnalyzer.spawn_message_poller()
  → 5秒内返回状态消息
```

### 8.2 Worker处理链路（v2.5 async-batch模式）
```
worker.py init_engines()
  → SmartScoringEngine()
  → asyncio.Semaphore(KIMI_MAX_CONCURRENT)

run_async_batch_worker() 主循环 [v2.5优化]
  → fetch_and_group_tasks()          # 获取任务
  → 【v2.5】_fast_scene_classify()   # 快速场景分类（关键词规则）
  → 【v2.5变更】按场景分组（不再按user_id分组）
  → _prepare_merged_tasks_sync()     # 会话合并
  → _batch_score_with_limit_v2()     # 【v2.5】传入预分析结果
    → score_sessions_batch_async(
        sessions, 
        pre_analyses                  # 【v2.5】跳过内部预分析
      )
      → _score_batch_same_scene()
        → _call_kimi_async()
```

---

## 9. 已知陷阱与禁区

### ⚠️ 当前真实存在的Bug（经代码验证）

**BUG-2026-04-05-001: API超时硬编码**
- **位置**: `smart_scoring_v2.py` ~680行 `_call_kimi_async()`
- **问题**: `timeout_seconds = 300` 硬编码，`.env` 中 `KIMI_API_TIMEOUT` 配置无效
- **修复方案**: `timeout_seconds = float(os.getenv('KIMI_API_TIMEOUT', '300'))`
- **优先级**: P2

### ✅ 已修复Bug列表

已修复Bug列表已独立到 `docs/bugs/index.md`，按需加载。

**延迟加载触发词**：
- 文件名显示错误、显示别的文件 → 可能是 **H-CORE-010**
- 完成报告没收到、只有进度 → 可能是 **H-MSG-009**
- Worker检测不到、Socket错误 → 可能是 **H-CORE-005**
- 进度显示200%、溢出 → 可能是 **H-CORE-006**
- 重复分析、任务重复 → 可能是 **H-CORE-007**
- 没收到消息、通知丢失 → 可能是 **H-CORE-008**

### ✅ 禁区 T-1：Socket检测Worker（H-CORE-005 Bug，已修复）
**修复后方案**：PID文件 + 进程存在性双重检测

### ❌ 禁区 T-2：位置索引读数据库字段
**正确做法**：`cols = [d[0] for d in cursor.description]; dict(zip(cols, row))`

### ❌ 禁区 T-3：options字典传HTTP timeout
**正确做法**：`client.generate(options={...}, timeout=(5.0, 30.0))`

### ❌ 禁区 T-4：并行函数假设外层作用域变量
**正确做法**：`is_transfer = session_data.get('is_transfer', False)`

### ⚠️ 注意 W-1：幂等检查在BatchAnalyzer层
`task_queue.submit_task()` 底层无幂等保护是设计决策，幂等在上层实现。

### ⚠️ 注意 W-2：两个独立数据库
- **主库**：`data/cs_analyzer_new.db`（sessions/results/rules/corrections）
- **队列库**：`data/task_queue.db`（analysis_tasks）
- **两库独立**：connection不能混用

### ⚠️ 注意 W-3：异步方法中的同步IO（v2.4新增）
**问题**：在async函数中直接调用同步的数据库操作会阻塞事件循环

**解决方案**：使用`loop.run_in_executor()`将同步操作放到线程池
```python
# 错误
result = save_to_database(...)  # 阻塞!

# 正确
loop = asyncio.get_event_loop()
result = await loop.run_in_executor(None, save_to_database, ...)
```

### ❌ 禁区 T-5：在 for 循环内使用 async with semaphore（v2.5.1新增）
**问题**：信号量在循环内部会导致批次间串行，配置50并发实际仅1并发

**错误代码**：
```python
for batch in batches:              # 串行循环
    async with kimi_semaphore:     # 锁在循环内！实际只有1并发
        result = await score(...)
```

**正确做法**：使用 `asyncio.gather()` 让所有批次同时竞争信号量
```python
async def score_one(batch):
    async with kimi_semaphore:     # 各批次独立竞争锁
        return await score(...)

# 全部同时启动，真正并行
await asyncio.gather(*[score_one(b) for b in batches])
```

**修复影响**：50通会话耗时 24.6min → 15.2min（↓38%）

---

## 10. 关键函数签名（v2.6.2更新）

```python
# 【v2.6.2】智能任务获取（废弃batch_size参数）
worker.fetch_and_group_tasks(max_batch_size: int = 150) -> Dict[str, List[Dict]]

# 【v2.6.2】自适应批量计算（限制最大20通）
worker.calculate_adaptive_batch_size(sessions: List[Dict], base_size: int = 30) -> int

# 意图分类（不变）
RobustIntentClassifier.classify(messages: List[Dict]) -> IntentClassificationResult

# 【v2.5修改】批量评分引擎（支持传入预分析结果）
SmartScoringEngine.score_sessions_batch_async(
    sessions: List[Dict],
    pre_analyses: List[Dict] = None
) -> List[Dict]

# 【v2.5新增】快速场景分类
worker._fast_scene_classify(messages: List[Dict]) -> str

# 【v2.5.1修复】批量评分（真正并行）
worker._batch_score_with_limit_v2(tasks, batch_size) -> List[Dict]

# 任务队列（不变）
task_queue.submit_task(session_id: str, session_data: Dict) -> int
task_queue.get_queue_stats() -> Dict

# 【v2.6.2修复】Worker检测（清理残留PID）
BatchAnalyzer.check_worker_running() -> bool
BatchAnalyzer.is_already_analyzed(session_id: str) -> bool
```

---

## 11. 你的工作流程

收到修改请求后，按以下顺序响应：

1. **复述理解**：用1-2句话复述修改目标，确认无歧义
2. **影响分析**：列出可能受影响的模块和文件
3. **禁区检查**：明确声明"此方案不触犯T-1至T-5"
4. **给出diff**：以diff格式输出修改
5. **验证建议**：说明如何验证修改正确

---

## 12. 不确定时的处理

- **不确定函数行为**：说"我需要看[文件名:行号]的代码才能确认"
- **上下文与文档冲突**：以实际代码为准，并指出差异
- **涉及业务逻辑**：明确说"需要领域专家确认"

---

## 13. 使用示例

### 启动分析（后台模式，v2.5默认async-batch）
```bash
python3 cs_analyzer_batch.py /path/to/chat.log
```

### 【v2.6.2】启动Worker（手动，async-batch模式）
```bash
python3 worker.py --async-batch --max-groups=6 --max-batch-size=150 --score-batch-size=30
```

### 【v2.6.2】验证智能批量效果
```bash
# 检查日志中的关键指标
grep "队列共有" /tmp/worker.log
# 应显示：队列共有 50 个任务，全部取出处理

grep "优化后" /tmp/worker.log
# 应显示：自适应批量: 基础=30, 优化后=20（不超过20）

grep "Parsed" /tmp/worker.log
# 应显示：Parsed 20 results（每批完整返回）
```

### 检查队列状态
```python
from task_queue import get_queue_stats
print(get_queue_stats())
# {'pending': 10, 'processing': 2, 'completed': 45, 'failed': 0}
```

### 【v2.6.3】验证Worker可靠性修复
```bash
# 测试依赖检查（故意缺少依赖）
python3 -c "import sys; sys.path.insert(0, '.'); __builtins__.__import__ = lambda n,*a,**k: (_ for _ in ()).throw(ImportError()) if n=='dotenv' else __import__(n); exec(open('worker.py').read().split('# ========== 依赖检查结束')[0])"
# 应显示：❌ Worker启动失败：缺少必要依赖

# 测试启动失败通知
grep "Worker启动后立即退出" /tmp/worker.log
# 如果启动失败，应显示：❌ Worker启动后立即退出（退出码: X）
```

### 【v2.5】验证批量评分效果
```bash
# 分析完成后检查API调用次数
grep -c "HTTP Request: POST https://api.moonshot.cn" /tmp/worker.log
# 22通会话应约为8-10次（vs 修复前31次）
```

---

## 14. Bug修复记录

### BUG-2026-04-05-002: Worker启动失败导致任务空等1小时

**问题描述**：
- Worker因缺少`dotenv`模块启动后立即崩溃
- 任务状态卡在"processing"，用户无感知
- 用户空等近1小时才发现问题

**根因**：
- 依赖检查缺失，Worker启动时直接崩溃
- 启动失败无即时通知，失败信息隐藏在日志中

**修复方案**：
1. **方案1（worker.py）**：启动前强制检查所有必要依赖
   - 缺失时明确提示缺少的模块和安装命令
   - 立即退出（exit 1），不悄无声息崩溃

2. **方案2（batch_analyzer.py）**：启动后3秒存活检测
   - 检测Worker是否仍在运行
   - 失败时立即发送飞书通知
   - 通知包含修复建议（pip install命令）

**影响范围**：
- worker.py: 新增`_check_dependencies()`函数
- batch_analyzer.py: 重写`start_worker()`方法，新增`_send_failure_notification()`方法

**验证结果**：
- 模拟缺少依赖时，立即显示错误信息
- 启动失败时，3秒内收到飞书通知

---

### BUG-2026-04-05-004: 消息服务崩溃导致进度通知中断

**问题描述**：
- 分析任务执行到60%后不再推送进度消息
- 用户等待20分钟后发"?"才重新激活反馈
- Worker实际已完成，但用户未收到完成报告

**根因分析**：
- `message_poller` 进程在60%后崩溃或退出
- 后续进度消息（80%、100%、完成报告）写入文件但未被发送
- 无健康检查机制，无法自动恢复

**修复方案**（v2.6.4）：

**1. message_poller.py 增强**
- PID文件自监控：`/tmp/cs_analyzer_message_poller.pid`
- 崩溃恢复：启动时加载 `/tmp/cs_analyzer_messages_failed.jsonl` 残留消息
- 必达消息：含"完成"/"100%"/"质检报告"关键词的消息重试3次
- 优雅退出：30秒清理窗口，未发送消息保存到失败队列

**2. monitor_agent.py 健康检查**
- 每5次循环（约50秒）检查消息服务健康
- 检测到崩溃时自动重启
- 记录重启次数，避免无限重启

**3. batch_analyzer.py 增强**
- `check_message_poller_running()` - PID文件+进程存在性检测
- `restart_message_poller_if_needed()` - 按需重启

**关键代码**（message_poller.py）：
```python
# PID文件自监控
def _write_pid_file(self):
    PID_FILE.write_text(str(os.getpid()))

# 必达消息检测
MUST_DELIVER_KEYWORDS = ['完成', '100%', '质检报告', '分析完成']

# 失败消息恢复
def _load_failed_messages(self):
    if MSG_FILE_FAILED.exists():
        # 将失败消息重新写入待发送队列
```

**影响范围**：
- `message_poller.py`: 新增PID监控、消息重试、残留恢复
- `monitor_agent.py`: 新增健康检查和自动重启
- `batch_analyzer.py`: 新增辅助检查方法

**版本**：v2.6.4

---

### BUG-2026-04-05-003: v2.6.3修复不完整 - start_worker()返回值未检查导致无限轮询

**问题描述**：
- v2.6.3已添加依赖检查和启动后存活检测
- 但 `start_worker()` 返回 `False` 时，调用方未处理
- 任务仍被提交到队列，但无Worker处理
- `run_foreground()` 的 `while True` 无限轮询，导致用户/Agent死循环等待

**根因分析**：
```python
# batch_analyzer.py run_foreground()
if not self.check_worker_running():
    print("🚀 启动Worker...")
    self.start_worker()  # ← 不检查返回值！
    time.sleep(2)
# 继续提交任务，即使Worker启动失败...
```

**修复方案**：
1. **`run_foreground()`**：检查 `start_worker()` 返回值，失败时立即返回错误信息
2. **`run_background()`**：同样检查返回值
3. **`while True` 轮询**：添加30分钟超时保护

**代码变更**（batch_analyzer.py）：
```python
# 修复1: 检查start_worker返回值
if not self.start_worker():
    return "❌ Worker启动失败，无法继续分析。请检查依赖安装。"

# 修复2: while True添加超时保护
start_time = time.time()
max_wait_seconds = 30 * 60  # 30分钟

while True:
    if time.time() - start_time > max_wait_seconds:
        return "❌ 分析超时（30分钟），Worker可能未正常运行。"
    # ...
```

**影响范围**：
- batch_analyzer.py: `run_foreground()` 和 `run_background()` 方法

**修复后行为**：
- Worker启动失败时，3秒内明确返回错误信息
- 轮询超过30分钟自动超时，避免无限等待
- 不再出现"任务空等1小时"的情况

**版本**：v2.6.3-patch1

---

*当前项目版本：v2.6.4 | 基于实际代码状态 | 更新日期：2026-04-05 | 新增：消息服务稳定性增强*
