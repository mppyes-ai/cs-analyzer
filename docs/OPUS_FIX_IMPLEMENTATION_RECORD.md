# Opus 4.6 修复实施记录 (2026-04-18)

**实施时间**: 2026-04-18 20:21
**实施人员**: 小虾米
**代码审查**: Opus 4.6

---

## 实施的修复

### 1. P0: 去重前置 (修正Bug版本)

**问题**: 批次77包含重复会话（任务164/172同session_id），导致API耗时232.6s

**修复文件**: `worker.py`

**新增函数**:
```python
def deduplicate_sessions(tasks: List[Dict]) -> List[Dict]:
    """【Opus修复-P0】在送入模型前去重，保留最早的任务
    
    消除批次77的重复会话问题（任务164/172同session_id）
    """
    seen_sessions = {}
    unique_tasks = []
    
    # 按created_at排序，保留最早的任务
    for task in sorted(tasks, key=lambda x: x.get('created_at', '')):
        session_id = task.get('session_id')
        task_id = task.get('task_id')
        
        if session_id in seen_sessions:
            # 重复会话，取消重复任务
            kept_task_id = seen_sessions[session_id]
            cancel_task(task_id, reason=f"Duplicate session (kept task {kept_task_id})")
            print(f"   🔄 去重: 取消重复任务 {task_id} (保留 {kept_task_id}, session: {session_id})")
        else:
            # 首次出现，记录并保留
            seen_sessions[session_id] = task_id
            unique_tasks.append(task)
    
    if len(unique_tasks) < len(tasks):
        print(f"   ✅ 去重完成: {len(tasks)} → {len(unique_tasks)} 个任务")
    
    return unique_tasks
```

**调用位置**:
1. `_prepare_merged_tasks_sync()` - 会话合并前去重
2. `fetch_and_group_tasks()` - 跨批次去重

**预期效果**:
- 批次77从5任务→4任务
- API耗时从232.6s降至~140s
- 日志中出现"去重完成"记录

---

### 2. P1: 超长会话检测

**问题**: 批次46包含客诉会话（任务398，30条消息），API耗时189.0s

**修复文件**: `worker.py`

**修改函数**: `calculate_adaptive_batch_size()`

**新增检测逻辑**:
```python
# 【Opus修复】P1: 检查是否有超长会话（单通>10K tokens）
MAX_TOKENS_PER_TASK = 10000
for s in sessions[:base_size]:
    if estimate_session_tokens(s) > MAX_TOKENS_PER_TASK:
        print(f"   ⚠️ 检测到超长会话({estimate_session_tokens(s)} tokens)，降级为单通处理")
        return 1  # 单通处理超长会话

# 【Opus修复】P1: 检查消息数是否过多（>25条）
for s in sessions[:base_size]:
    if len(s.get('messages', [])) > 25:
        print(f"   ⚠️ 检测到超多消息会话({len(s.get('messages', []))}条)，降级为单通处理")
        return 1  # 单通处理超长对话
```

**预期效果**:
- 任务398（30条消息）自动单通处理
- 避免阻塞其他3个正常任务
- 该批次整体耗时从189s降至~140s

---

## 验证清单（修复后测试）

运行400通测试时检查：

- [ ] `worker.log`中出现"🔄 去重:"日志
- [ ] `worker.log`中出现"⚠️ 检测到超长会话"或"⚠️ 检测到超多消息会话"日志
- [ ] 批次77 API耗时≤160s（原232s）
- [ ] 批次46 API耗时≤140s（原189s）
- [ ] `sessions`表无重复session_id
- [ ] 总运行时间≤10分钟

---

## 代码变更统计

| 文件 | 变更类型 | 行数 |
|------|----------|------|
| `worker.py` | 新增函数 | +30 |
| `worker.py` | 修改函数 | +25 |

**语法验证**: ✅ 通过 `python3.14 -m py_compile`

---

## 预期总效果

| 指标 | 修复前 | 修复后 | 提升 |
|------|--------|--------|------|
| 批次77耗时 | 232.6s | ~140s | -40% |
| 批次46耗时 | 189.0s | ~140s | -26% |
| 400通总耗时 | ~14.9分钟 | ~8-10分钟 | -33% |

---

**状态**: ✅ 修复实施完成，待测试验证
