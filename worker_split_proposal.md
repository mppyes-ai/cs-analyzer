## 拆分建议

### 当前结构分析
worker.py: 1713行，约32个函数/类

**核心问题：**
1. **职责混杂** —— 数据库写入、会话合并、API调用、重试逻辑、信号处理全部挤在一个文件
2. **版本堆积** —— v2.6.1 → v2.6.5 的修复都是追加代码，没有重构
3. **阅读困难** —— 每次我需要读代码时只能分段读取，容易遗漏关键逻辑

---

### 拆分方案（按职责分层）

**建议拆分为5个模块：**

```
cs-analyzer/
├── worker.py                    # 入口 + 主循环（~200行）
├── worker_core/
│   ├── __init__.py
│   ├── db_writer.py            # 异步数据库写入队列（~120行）
│   │   └── queue_save_result, start_db_writer, stop_db_writer, wait_for_db_writes
│   ├── session_merger.py       # 会话合并/去重逻辑（~250行）
│   │   └── find_related_sessions, merge_session_data, deduplicate_sessions, _prepare_merged_tasks_sync
│   ├── batch_scorer.py         # 批量评分核心（~400行）
│   │   └── _batch_score_with_limit, _batch_score_with_limit_v2, _retry_tasks_batch, _retry_single_task
│   └── task_fetcher.py         # 任务获取/分组（~150行）
│       └── fetch_and_group_tasks, _fetch_failed_tasks_for_retry
└── utils/
    ├── __init__.py
    ├── lock.py                  # 文件锁（~60行）
    ├── token_estimator.py       # Token估算（~80行）
    └── signal_handler.py        # 信号处理（~40行）
```

**拆分后各文件行数预估：**
| 文件 | 行数 | 职责 |
|------|------|------|
| worker.py | ~200 | 入口、模式分发、主循环 |
| db_writer.py | ~120 | 异步写入队列 |
| session_merger.py | ~250 | 会话合并/去重 |
| batch_scorer.py | ~400 | 批量评分+重试 |
| task_fetcher.py | ~150 | 任务获取/分组 |
| lock.py | ~60 | 进程锁 |
| token_estimator.py | ~80 | Token估算 |
| signal_handler.py | ~40 | 信号处理 |

---

### 为什么这样拆？

**1. 单一职责**
- `batch_scorer.py` 只关心评分逻辑，不碰数据库
- `db_writer.py` 只关心队列写入，不碰API调用
- 修改评分逻辑时，不需要担心破坏数据库写入

**2. 便于测试**
- `batch_scorer.py` 可以独立单元测试（mock API调用）
- `session_merger.py` 可以独立测试合并逻辑
- 当前1713行根本不可能做有效单元测试

**3. 版本演进**
- 新增评分算法？只改 `batch_scorer.py`
- 优化数据库写入？只改 `db_writer.py`
- 不会导致整个文件冲突

**4. 阅读效率**
- 我需要读重试逻辑时，只读 `batch_scorer.py`（~400行）
- 不需要在1713行中翻找

---

### 实施建议

**方案A：保守拆分（最小改动）**
只拆出变化最频繁的两个模块：
1. `batch_scorer.py` —— 评分+重试逻辑（最核心，修改最多）
2. `session_merger.py` —— 合并逻辑（已验证有bug，需重构）
其余保留在 worker.py 中

**方案B：完整拆分（推荐）**
按上述5模块完整拆分
- 工作量：约2-3小时
- 风险：需要测试所有模式（serial/parallel/grouped/async-batch）
- 收益：长期维护成本大幅降低

**方案C：渐进拆分（折中）**
每次修复/新增功能时，顺便拆分相关代码
- 工作量：分散到日常开发中
- 风险：低
- 缺点：拆分不彻底，可能产生临时状态

---

### 我的倾向

**推荐方案B（完整拆分）**，理由：

1. **当前代码已经"技术债务"严重** —— Opus 4.6 诊断的Issue #8（超长函数）和 #10（裸except）都与此相关
2. **拆分后便于并行开发** —— 如果后续还要优化评分算法、增加新模型支持，独立模块更容易协作
3. **减少我的阅读开销** —— 1713行每次只能分段读，拆分后我可以完整读取核心模块

**具体执行步骤：**
1. 先备份 worker.py
2. 按函数依赖关系逐个迁移（从底部依赖少的开始）
3. 每拆一个模块就跑一次测试验证
4. 最后统一跑全量359通测试

---

### 注意事项

**不要犯的错误：**
- ❌ 不要一次性全部剪切粘贴（容易丢失依赖）
- ❌ 不要在拆分同时做逻辑修改（增加风险）
- ❌ 不要遗漏全局变量迁移（如 kimi_semaphore, scorer, classifier）

**应该做的：**
- ✅ 先画依赖图（谁调用谁）
- ✅ 从叶子节点开始拆分（被调用最多的最后拆）
- ✅ 保留原文件的git history（用git mv而不是手动复制）

---

### 如果要执行

需要金总授权的话，我可以：
1. 先分析函数依赖关系，生成拆分方案
2. 逐个模块迁移，每步验证
3. 最后跑一轮全量测试确认

**或者，如果金总想自己安排时间做，我可以先输出详细的依赖关系图和拆分清单。**