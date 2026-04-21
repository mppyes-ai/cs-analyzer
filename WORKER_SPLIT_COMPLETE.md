# CS-Analyzer Worker.py 完整拆分总结

## 拆分历程

| 步骤 | 时间 | 主要工作 | 涉及文件 |
|------|------|----------|----------|
| Step 1 | 2026-04-21 10:20 | 配置与全局状态拆分 | `worker_config.py` |
| Step 2 | 2026-04-21 11:00 | 会话合并模块拆分 | `session_merge.py` |
| Step 3 | 2026-04-21 11:30 | 批量评分模块拆分 | `batch_scoring.py` |
| Step 4 | 2026-04-21 12:00 | 数据库操作模块拆分 | `db_operations.py` |
| Step 5 | 2026-04-21 13:43-14:04 | 数据库写入队列 + 任务获取 | `db_writer.py`, `task_fetcher.py` |

---

## 最终模块结构

```
CS-Analyzer/
├── worker.py              # 674行 - Worker主循环、运行模式、主入口
├── worker_config.py       # 配置常量、工具函数、全局状态
├── db_writer.py           # 异步数据库写入队列
├── task_fetcher.py        # 任务获取与失败重试
├── batch_scoring.py       # 批量评分与重试逻辑
├── db_operations.py       # 数据库CRUD操作
├── session_merge.py       # 会话合并与去重
├── task_queue.py          # 队列管理
├── smart_scoring_v2.py    # 评分引擎（Kimi API交互）
└── intent_classifier_v3.py # 意图分类引擎
```

---

## 各模块职责

### worker.py (674行)
**保留内容：**
- 进程管理：`acquire_lock`, `release_lock`, `signal_handler`
- 引擎初始化：`init_engines`
- 运行模式：
  - `run_grouped_parallel_worker` - 预分组并行模式
  - `run_async_batch_worker` - 异步批量模式（推荐）
- 同步处理：`process_group`, `process_task_sync`
- 主入口：`main`

**依赖：** `worker_config`, `db_writer`, `task_fetcher`, `batch_scoring`, `session_merge`, `task_queue`, `db_utils`, `db_operations`

### worker_config.py
**内容：**
- 环境变量配置（BATCH_SCORE_SIZE, MAX_TOKENS_PER_BATCH等）
- Token估算工具函数：`estimate_session_tokens`, `calculate_adaptive_batch_size`
- 全局可变状态：`running`, `classifier`, `scorer`, `kimi_semaphore`, `db_lock`
- 日志目录和PID文件路径

### db_writer.py (新建)
**内容：**
- `_db_writer_loop` - 后台线程处理写入队列
- `start_db_writer` / `stop_db_writer` - 启动/停止线程
- `queue_save_result` - 将结果加入异步队列
- `wait_for_db_writes` / `wait_for_db_writes_async` - 等待写入完成

**特点：** 非阻塞写入，解耦API调用和数据库IO

### task_fetcher.py (新建)
**内容：**
- `fetch_and_group_tasks` - 智能获取待处理任务并按user_id分组
- `_fetch_failed_tasks_for_retry` - 获取可重试的失败任务

**特点：** 支持"看人数打饭"策略，--once模式下取全部任务

### batch_scoring.py
**内容：**
- `_batch_score_with_limit_v2` - 主评分函数（自适应批量）
- `_batch_score_with_limit` - 旧版（已废弃）
- `_retry_tasks_batch` - 批量重试（并行化）
- `_retry_single_task` - 单通重试（降级）

**循环引用修复：** 不再从worker导入，改为从`db_operations`和`db_writer`导入

### db_operations.py
**内容：**
- `save_to_database` - 保存会话到数据库
- `_save_result_sync` - 同步保存评分结果
- `_log_inconsistency` - 记录不一致性
- `fetch_all_sessions` - 批量查询

### session_merge.py
**内容：**
- `find_related_sessions` - 查找关联会话
- `merge_session_data` - 合并会话数据
- `deduplicate_sessions` - 去重
- `parse_timestamp`, `has_transfer_keyword` - 工具函数

---

## 关键修复

### 1. 循环引用消除
**问题：** batch_scoring.py 从 worker.py 导入 `_save_result_sync` 和 `queue_save_result`
**修复：** 改为从 `db_operations` 和 `db_writer` 直接导入

### 2. 尾部雪崩修复
**问题：** 30个失败任务串行重试花费12分钟
**修复：** `_retry_tasks_batch` 改为 `asyncio.gather` 并发执行
**效果：** 90-100%从15分钟 → 4.4分钟

### 3. 评分无效根因
**问题：** BATCH_SCORE_SIZE=30 时 Prompt Token 过大（15K-25K），API返回JSON截断
**修复：** BATCH_SCORE_SIZE=10，每批Token约6K-10K
**效果：** 失败数从30 → 0，总耗时从19.3分钟 → 8.2分钟

---

## 当前配置 (.env)

```ini
BATCH_SCORE_SIZE=10              # 基础批量大小（已优化）
MAX_TOKENS_PER_BATCH=200000      # Token安全上限
ADAPTIVE_BATCH_MIN=3            # 最小批量
ADAPTIVE_BATCH_MAX=5             # 最大批量
KIMI_MAX_CONCURRENT=90           # Kimi并发数
MERGE_WINDOW_MINUTES=0           # 关闭会话合并
```

---

## 验证状态

| 检查项 | 状态 |
|--------|------|
| worker.py 语法检查 | ✅ 通过 |
| worker.py 导入测试 | ✅ 通过 |
| 模块间循环引用 | ✅ 无 |
| 17通测试评分 | ✅ 正常 |
| 预分析显示 | ✅ 正常 |

---

## 后续建议

1. **运行稳定性验证：** 建议用100-200通真实数据测试完整流程
2. **错误处理增强：** _db_writer_loop 的异常处理可以进一步完善
3. **监控优化：** 考虑添加更详细的进度追踪和错误上报

---

*最后更新：2026-04-21 14:10*
