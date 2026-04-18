# CS-Analyzer 问题调查报告 (2026-04-18)

## 问题1: 知识库无数据但WEB端显示规则命中

### 现象描述
- 知识库无任何数据
- 但WEB端显示这些会话均有规则命中：
  `session_0372_a9aad14a`, `session_0370_f6b362e6`, `session_0369_e5eab026`, ...

### 根因分析

#### 1. 前端显示逻辑 (pages/6_🔍_会话分析与矫正.py)
```python
# 从 analysis_data 中提取规则命中数据
referenced_rules = []
for dim_key, dim_info in ds.items():
    rules = dim_info.get('referenced_rules', [])  # ← 从模型返回结果提取
    for rule in rules:
        referenced_rules.append({...})
```

#### 2. 规则来源
- **不是来自知识库** (knowledge_base_v2.get_rule_by_id)
- **来自模型返回的 `referenced_rules` 字段**

#### 3. 问题定位
**这是预期行为，但显示逻辑有误导性**

从代码分析：
```python
# smart_scoring_v2.py 中的评分逻辑
result = {
    'dimension_scores': {
        'professionalism': {
            'score': 4,
            'referenced_rules': ['标准话术规范', '响应时效要求']  # ← 模型生成的规则名
        }
    }
}
```

**即使知识库为空，模型仍会返回 `referenced_rules`**
- 模型根据内置的评分标准生成规则名称
- 这些规则名称是**文本描述**，不是知识库中的规则ID
- 前端显示时将其当作规则名展示

#### 4. 证据
数据库中无 `rule_matches` 表：
```sql
SELECT COUNT(*) FROM rule_matches;
-- Error: no such table: rule_matches
```

说明规则命中**不是**通过知识库规则匹配产生的，而是模型直接生成的文本。

### 建议修复

#### 方案A: 区分"知识库规则"和"模型规则"
```python
# 前端显示时添加标记
if rule_exists_in_knowledge_base(rule_name):
    display = f"📚 {rule_name}"  # 知识库规则
else:
    display = f"🤖 {rule_name} (模型生成)"  # 模型生成的规则描述
```

#### 方案B: 知识库为空时隐藏规则命中
```python
if not has_knowledge_base_data():
    referenced_rules = []  # 清空显示
    show_info("当前知识库为空，规则命中由模型自动生成")
```

#### 方案C: 添加规则来源标识
```python
# 在模型返回时标记来源
referenced_rules = [{
    'name': rule_name,
    'source': 'model_generated' if not is_in_kb else 'knowledge_base'
}]
```

---

## 问题2: 数据库写入顺序逻辑

### 用户问题
同时发送6组(A/B/C/D/E/F)会话分析给KIMI，按什么顺序写入数据库？
- 按KIMI返回顺序（假设D/A/B/E/C/F）？
- 还是按发送顺序（A/B/C/D/E/F）？

### 答案: **按API返回顺序写入（D/A/B/E/C/F）**

#### 1. 代码证据 (worker.py)

**批次评分逻辑**:
```python
# 【Opus修复】使用 as_completed 替代 gather
batch_tasks = [score_one_batch(i, batch) for i, batch in enumerate(batches)]

# as_completed: 先完成的先处理
for task in asyncio.as_completed(batch_tasks):
    batch_results = await task
    for task, result in zip(batch, batch_results):
        await loop.run_in_executor(None, _save_result_sync, task, result)  # ← 立即保存
```

**关键流程**:
```
1. 发送批次A/B/C/D/E/F（并发）
2. as_completed等待任何一个完成
3. 假设D先返回 → 立即执行_save_result_sync → 写入数据库
4. 假设A第二返回 → 立即执行_save_result_sync → 写入数据库
5. ...以此类推
```

#### 2. 保存逻辑 (_save_result_sync)
```python
def _save_result_sync(task: Dict, result: Dict):
    """同步保存结果"""
    # 1. 保存分析结果
    save_to_database(session_id, session_data, intent, result, ...)
    
    # 2. 更新任务状态
    complete_task(task_id, result)
```

**特点**:
- 无批次内排序逻辑
- 无延迟批量写入
- **哪个任务先完成，哪个先写入**

#### 3. 对比: 如果是gather方式
```python
# gather方式（旧版）
results = await asyncio.gather(*batch_tasks)  # 等待全部完成
for batch, batch_results in zip(batches, results):  # 按发送顺序处理
    for task, result in zip(batch, batch_results):
        save_to_database(...)  # ← 按发送顺序写入
```

| 方式 | 写入顺序 | 特点 |
|------|----------|------|
| **as_completed** (当前) | **API返回顺序** (D/A/B/E/C/F) | 先完成先写入，无阻塞 |
| gather (旧版) | 发送顺序 (A/B/C/D/E/F) | 等全部完成再写入，有木桶效应 |

### 验证方法
如需验证，可查询数据库写入时间：
```sql
SELECT session_id, created_at 
FROM sessions 
ORDER BY created_at;
-- 观察created_at顺序是否与发送顺序一致
```

### 影响分析

#### 正面影响
- **性能提升**: 不需要等待最慢批次
- **实时性**: 结果立即可查

#### 潜在问题
- **顺序不一致**: 先发送的可能后写入（如果API响应慢）
- **进度显示**: 用户可能困惑为什么后提交的会话先显示结果

### 建议（如需要顺序一致性）

如需按发送顺序写入，可修改 `_batch_score_with_limit_v2`:

```python
async def _batch_score_with_limit_v2(tasks, base_batch_size):
    # ... 评分逻辑 ...
    
    # 方案: 收集所有结果后按顺序写入
    results_map = {}  # batch_idx -> results
    
    for coro in asyncio.as_completed(batch_tasks_map.keys()):
        batch_idx, batch = batch_tasks_map[coro]
        batch_results = await coro
        results_map[batch_idx] = (batch, batch_results)
    
    # 按批次索引顺序写入（即发送顺序）
    for i in range(total_batches):
        if i in results_map:
            batch, batch_results = results_map[i]
            for task, result in zip(batch, batch_results):
                await loop.run_in_executor(None, _save_result_sync, task, result)
```

**权衡**:
- 优点: 写入顺序与发送顺序一致
- 缺点: 需要等待所有批次完成才能开始写入，失去as_completed的实时性优势

---

## 总结

| 问题 | 答案 | 建议 |
|------|------|------|
| 知识库为空但显示规则命中 | **预期行为** - 模型生成规则名，非知识库匹配 | 添加来源标识区分 |
| 数据库写入顺序 | **API返回顺序** (D/A/B/E/C/F) | 当前设计合理，无需修改 |

---

**报告编制**: 小虾米
**编制时间**: 2026-04-18 17:05
