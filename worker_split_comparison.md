## Opus 4.6 拆分方案 vs 我的方案对比

### Opus 4.6 方案

```
worker.py          → 主入口+编排（~15KB）
worker_config.py   → 共享配置+全局状态（~3KB）
worker_db.py       → 数据库写入层（~12KB）
worker_merge.py    → 会话合并+去重（~10KB）
worker_batch.py    → 批量评分核心（~12KB）
worker_retry.py    → 重试逻辑（~8KB）
```

### 我的方案

```
worker.py          → 入口+主循环（~200行）
db_writer.py       → 异步数据库写入（~120行）
session_merger.py  → 会话合并/去重（~250行）
batch_scorer.py    → 评分+重试核心（~400行）
task_fetcher.py    → 任务获取/分组（~150行）
utils/             → 锁、Token估算、信号处理
```

---

## 关键差异

| 维度 | Opus 4.6 | 我的方案 | 评估 |
|------|---------|---------|------|
| **全局状态管理** | `worker_config.py` 集中管理 | 分散在各模块 | ✅ Opus更优 |
| **重试逻辑** | 独立 `worker_retry.py` | 放在 `batch_scorer.py` | ⚠️ 各有道理 |
| **任务获取** | 留在 `worker.py` | 独立 `task_fetcher.py` | ✅ 我的更彻底 |
| **锁/信号处理** | 留在 `worker.py` | 放在 `utils/` | ⚠️ 细微差别 |
| **utils函数** | `estimate_session_tokens` 在 config | 独立 `utils/token_estimator.py` | ⚠️ 细微差别 |

---

## Opus 4.6 方案的亮点（我建议采纳）

### 1. `worker_config.py` —— 集中配置管理

**这是最重要的设计决策。**

当前 `worker.py` 顶部有一堆全局变量：
```python
running = True
classifier = None
scorer = None
kimi_semaphore = None
db_lock = threading.Lock()
```

Opus 把它们集中到 `worker_config.py`，所有子模块通过 `import worker_config as cfg` 访问。

**好处：**
- 避免循环导入问题
- 全局状态变更一目了然
- 新增配置时不需要改5个文件

**关键警告（Opus已指出）：**
```python
# ❌ 错误：这样拿不到更新后的值
from worker_config import scorer  # 永远是 None

# ✅ 正确：通过模块属性访问
import worker_config as cfg
cfg.scorer  # 能拿到 init_engines() 后赋值的对象
```

### 2. 重试逻辑独立（`worker_retry.py`）

Opus 把 `_retry_tasks_batch()` 和 `_retry_single_task()` 独立出来。

**理由：**
- 重试逻辑最近刚大修（并行化改造）
- 未来可能还要优化（如指数退避、死信队列）
- 独立后更容易单元测试

**我的判断：** 有道理。重试和评分虽然是调用关系，但职责不同。评分是"正向流程"，重试是"异常处理"。

---

## 我的方案的优势（建议保留）

### 1. `task_fetcher.py` 独立

Opus 把 `fetch_and_group_tasks()` 留在 `worker.py` 主入口。

**但我认为应该独立出来：**
- `fetch_and_group_tasks()` 是 Worker 的核心调度逻辑（150行，不小）
- 它决定了任务如何分组、如何轮询队列
- 未来如果要支持优先级队列、动态调度，改动的都是这部分

### 2. `utils/` 目录

锁、`estimate_session_tokens`、信号处理等工具函数放在 `utils/`。

**好处：**
- 不污染 worker 核心目录
- 其他模块（如 `batch_analyzer.py`）也可以复用
- 符合 Python 项目惯例

---

## 综合建议：合并方案

### 最终文件结构（推荐）

```
cs-analyzer/
├── worker.py              # 主入口+编排（~15KB）
├── worker_config.py       # 共享配置+全局状态（~3KB）⭐ Opus亮点
├── worker_db.py           # 数据库写入层（~12KB）
├── worker_merge.py        # 会话合并+去重（~10KB）
├── worker_batch.py        # 批量评分核心（~12KB）
├── worker_retry.py        # 重试逻辑（~8KB）⭐ Opus亮点
├── worker_fetcher.py      # 任务获取/分组（~8KB）⭐ 我的补充
├── utils/
│   ├── __init__.py
│   ├── lock.py            # PID文件锁
│   ├── signal_handler.py  # 信号处理
│   └── token_estimator.py # Token估算
├── smart_scoring_v2.py    # 不动
├── task_queue.py          # 不动
└── intent_classifier_v3.py # 不动
```

### 相比 Opus 4.6 的改动

1. **新增 `worker_fetcher.py`** —— 把 `fetch_and_group_tasks()` 从 `worker.py` 拆出来
2. **新增 `utils/` 目录** —— 锁、信号处理、Token估算放这里
3. **Opus 的其他设计全部采纳**（config集中、retry独立）

---

## 执行顺序（修正版）

按 Opus 4.6 的步骤，但增加一步：

**Step 0：** 备份 `worker.py` → `worker.py.bak`

**Step 1：** 新建 `worker_config.py`
- 剪切所有配置常量、全局变量、estimate_session_tokens、calculate_adaptive_batch_size
- worker.py 顶部加 `import worker_config as cfg`
- 所有 `running` → `cfg.running`，`scorer` → `cfg.scorer`
- **测试：** 跑 `--help` 确认能加载

**Step 2：** 新建 `worker_db.py`
- 剪切整个异步写入队列 + save_to_database + _save_result_sync + _log_inconsistency
- **测试：** 跑一轮10通会话

**Step 3：** 新建 `utils/` 目录
- 剪切 lock.py、signal_handler.py、token_estimator.py
- **测试：** 跑 `--help`

**Step 4：** 新建 `worker_merge.py`
- 剪切 6 个合并函数
- **测试：** 跑10通，验证合并逻辑

**Step 5：** 新建 `worker_fetcher.py`
- 剪切 fetch_and_group_tasks + _fetch_failed_tasks_for_retry
- **测试：** 跑10通

**Step 6：** 新建 `worker_batch.py`
- 剪切 _batch_score_with_limit_v2 + _batch_score_with_limit
- **测试：** 跑50通

**Step 7：** 新建 `worker_retry.py`
- 剪切 _retry_tasks_batch + _retry_single_task
- **测试：** 跑50通，制造几个失败看重试是否工作

**Step 8：** 全量回归
- 跑359通完整测试
- 对比拆分前后的日志：任务数、成功数、失败数、耗时

---

## 关键风险点

### 风险1：全局状态同步

**问题：** `cfg.scorer = SmartScoringEngine(...)` 在 `worker.py` 中执行后，`worker_batch.py` 能否看到？

**答案：** 可以。Python 模块是单例，只要都用 `import worker_config as cfg`，`cfg.scorer` 就是同一个对象。

**测试方法：**
```python
# worker.py
import worker_config as cfg
cfg.scorer = SmartScoringEngine()
print(f"worker.py: {id(cfg.scorer)}")

# worker_batch.py
import worker_config as cfg
print(f"worker_batch.py: {id(cfg.scorer)}")  # 应该和上面相同
```

### 风险2：嵌套函数 `score_one_batch`

Opus 特别指出：`_batch_score_with_limit_v2` 内部定义了嵌套函数 `score_one_batch`，依赖外层 `total_batches` 局部变量。

**处理：** 原样搬迁，不要拆散。嵌套函数跟着外层函数一起走。

### 风险3：废弃代码清理

Opus 建议：`_batch_score_with_limit`（旧版）和 `process_group_async` 保留不删，后续独立任务清理。

**我的建议：** 同意。拆分时不删任何代码，只物理迁移。清理废弃代码作为后续独立任务。

---

## 最终建议

**采用"合并方案"（Opus 4.6 + 我的补充）：**

1. **Opus 的 `worker_config.py` 设计必须采纳** —— 这是避免全局状态混乱的关键
2. **Opus 的 `worker_retry.py` 独立必须采纳** —— 重试逻辑需要独立演进
3. **我的 `worker_fetcher.py` 建议采纳** —— 任务获取/分组是核心调度逻辑，不该留在入口文件
4. **我的 `utils/` 目录建议采纳** —— 工具函数归集，符合 Python 惯例

**如果金总授权，我可以按上述步骤执行。** 每步都验证，出问题可以单独回滚。
