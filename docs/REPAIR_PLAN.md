# CS-Analyzer 代码审查修复计划（已更新）

**审查来源**: Claude Opus 4.6  
**审查日期**: 2026-04-17  
**发现问题**: 16项  
**制定日期**: 2026-04-17  
**更新时间**: 2026-04-17 22:00（按Opus 4.6建议调整）

---

## 一、问题总览（已调整）

| 优先级 | 数量 | 类别 |
|--------|------|------|
| 🔴 **P0 (紧急)** | 1 | 安全风险 |
| 🟠 **P1 (高优)** | 6 | 架构/性能/可靠性 |
| 🟡 **P2 (中优)** | 6 | 代码规范/技术债务 |
| 🟢 **P3 (低优)** | 3 | 注释/命名等细节 |

---

## 二、P0 级别（已完成 ✅）

### P0-1: .env 文件本地跟踪问题 ✅
**状态**: **已完成并提交GitHub**
**处理**:
- 文档敏感信息已清理
- `.env` 停止Git跟踪
- `.gitignore` 已更新

---

## 三、P1 级别（本周内 - 立即执行）

### P1-1: 创建 scene_utils.py（原 _fast_scene_classify 重复定义）
**发现**: 完全相同的函数在 `worker.py` 和 `batch_analyzer.py` 中各有一份
**影响**: 维护困难，修改时容易遗漏
**修复时间**: 20分钟
**Opus 4.6 建议**: 不放入 `intent_classifier_v3.py`（太重），创建独立零依赖模块

**修复方案**:
```python
# scene_utils.py - 零外部依赖的轻量场景分类工具
from typing import List, Dict

def classify_scene_by_keywords(messages: List[Dict]) -> str:
    """
    基于关键词快速分类场景
    
    返回: '售前阶段' | '售中阶段' | '售后阶段' | '客诉处理'
    """
    text = ' '.join([m.get('content', '') for m in messages[:3]]).lower()
    
    # 客诉关键词（优先级最高）
    if any(k in text for k in ['投诉', '差评', '退货', '维权']):
        return '客诉处理'
    
    # 售后关键词
    if any(k in text for k in ['安装', '维修', '故障', '售后', '保修']):
        return '售后阶段'
    
    # 售中关键词
    if any(k in text for k in ['订单', '发货', '物流', '快递', '配送']):
        return '售中阶段'
    
    # 默认售前
    return '售前阶段'
```

**替换引用**:
```python
# worker.py 和 batch_analyzer.py 修改
from scene_utils import classify_scene_by_keywords
# 删除本地的 _fast_scene_classify 函数
```

**验证**: 两处调用结果一致

---

### P1-2: 启用 SQLite WAL 模式（原 P0-2 降级）
**发现**: 数据库连接缺乏统一管理，`task_queue.py` 每个函数都直接 `sqlite3.connect()`
**影响**: 潜在 "database is locked" 风险
**修复时间**: 30分钟
**Opus 4.6 建议**: 在 `db_utils.py` 和 `task_queue.py` 中封装 `get_connection()` 统一设置 WAL

**修复方案**:

1. **db_utils.py**（已有 get_connection，只需增强）:
```python
def get_connection(db_path=None):
    """获取数据库连接，统一设置 WAL 模式"""
    if db_path is None:
        db_path = DB_PATH
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    
    # 启用 WAL 模式
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    
    return conn
```

2. **task_queue.py**（新增 get_connection）:
```python
def get_queue_connection():
    """获取队列数据库连接，统一设置 WAL 模式"""
    conn = sqlite3.connect(QUEUE_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    
    # 启用 WAL 模式
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    
    return conn

# 替换所有 sqlite3.connect(QUEUE_DB_PATH) 调用
```

**验证**: 高并发测试无 locked 错误

---

### P1-3: 修复两数据库事务不一致
**发现**: `task_queue.db` 和 `cs_analyzer_new.db` 是两个独立文件，`_save_result_sync` 先写结果库再更新任务状态，可能不一致
**影响**: 结果已保存但任务状态未更新，或反之
**修复时间**: 40分钟
**Opus 4.6 建议**: 保留 MockIntent 构造逻辑，不一致记录用日志而非新建表

**修复方案**:
```python
# worker.py

def save_analysis_result_with_consistency_check(task_id, session_id, result, session_data):
    """
    原子性保存分析结果和更新任务状态
    保留 MockIntent 构造逻辑
    """
    # 从 result 构造 intent（保留原有逻辑）
    intent_data = result.get('_metadata', {}).get('pre_analysis', {})
    intent = MockIntent(
        primary_intent=intent_data.get('primary_intent', '咨询'),
        confidence=intent_data.get('confidence', 0.5),
        secondary_intents=intent_data.get('secondary_intents', []),
        scene=intent_data.get('scene', '售前阶段')
    )
    
    try:
        # 1. 先保存分析结果
        save_to_database(session_id, session_data, intent, result)
        
        # 2. 成功后更新任务状态
        complete_task(task_id, result)
        
        logger.info(f"✅ 任务 {task_id} 结果保存成功")
        
    except Exception as e:
        # 失败时记录不一致
        logger.error(f"❌ 任务 {task_id} 保存失败: {e}")
        
        # 检查是否部分成功（结果已保存但任务状态未更新）
        try:
            conn = get_connection()
            cursor = conn.execute(
                "SELECT 1 FROM sessions WHERE session_id = ?", 
                (session_id,)
            )
            result_exists = cursor.fetchone() is not None
            conn.close()
            
            if result_exists:
                # 结果已保存但任务状态失败
                logger.error(f"🚨 数据不一致: 会话 {session_id} 结果已保存但任务 {task_id} 状态更新失败")
                # 记录到日志文件
                log_inconsistency(session_id, task_id, str(e), "result_saved_task_failed")
            
        except Exception as check_error:
            logger.error(f"无法检查数据一致性: {check_error}")
        
        # 标记任务失败
        fail_task(task_id, str(e))
        raise

def log_inconsistency(session_id, task_id, error, inconsistency_type):
    """记录数据不一致到日志"""
    import datetime
    timestamp = datetime.datetime.now().isoformat()
    log_entry = f"[{timestamp}] {inconsistency_type} | session_id={session_id} | task_id={task_id} | error={error}\n"
    
    with open("data/inconsistency.log", "a") as f:
        f.write(log_entry)
```

**验证**: 模拟异常，确保无数据不一致

---

### P1-4: 简化 SQL 注入修复（降级为 P2 简化方案）
**发现**: `save_correction_v2` 使用 f-string 拼接 SQL
**Opus 4.6 建议**: 白名单已足够安全，只需加显式拒绝，降为 P2

**修复方案**:
```python
# db_utils.py
ALLOWED_FIELDS = {'professionalism_score', 'standardization_score', 
                  'policy_execution_score', 'conversion_score', 'total_score'}

def save_correction_v2(session_id, changed_fields):
    """
    保存矫正记录
    changed_fields: dict {field_name: new_value}
    """
    updates = []
    values = []
    
    for field_name, new_value in changed_fields.items():
        # 白名单检查
        if field_name not in ALLOWED_FIELDS:
            raise ValueError(f"非法字段: {field_name}")
        
        updates.append(f"{field_name} = ?")
        values.append(new_value)
    
    if not updates:
        return
    
    values.append(session_id)
    sql = f"UPDATE sessions SET {', '.join(updates)} WHERE session_id = ?"
    
    conn = get_connection()
    try:
        conn.execute(sql, values)
        conn.commit()
    finally:
        conn.close()
```

---

### P1-5: 盘点 corrections 表（知识库为空问题）
**发现**: `rules` 表为空，评分完全依赖 LLM 通用能力
**Opus 4.6 建议**: 盘点 corrections 表，如数量足够（50+）开始规则提取

**执行步骤**:
1. 查询 corrections 表记录数
2. 分析矫正记录模式
3. 制定规则提取方案

---

### P1-6: find_related_sessions 封装（遗漏项提升）
**发现**: 直接硬编码 `data/task_queue.db` 路径，绕过 `task_queue.py` 封装
**Opus 4.6 建议**: 提升到 P2，在 `task_queue.py` 中增加统一方法

**修复方案**:
```python
# task_queue.py

def get_pending_tasks_by_user(user_id: str) -> List[Dict]:
    """
    获取指定用户的待处理任务
    用于 find_related_sessions 的封装方法
    """
    conn = get_queue_connection()
    try:
        cursor = conn.execute(
            """SELECT session_id, session_data 
               FROM analysis_tasks 
               WHERE status = 'pending' AND session_data LIKE ?
            """,
            (f'%"user_id": "{user_id}"%',)
        )
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()

# worker.py 中替换
# from task_queue import get_pending_tasks_by_user
# related = get_pending_tasks_by_user(user_id)
```

---

## 四、P2 级别（2周内）

| # | 问题 | 文件 | 方案 |
|---|------|------|------|
| P2-1 | JSON解析容错 | smart_scoring_v2.py | 增加括号配对计数提取器 |
| P2-2 | ADAPTIVE_BATCH_MAX=5 | .env | 统一文档和代码 |
| P2-3 | Token估算偏粗 | config.py | 实际测量校准 |
| P2-4 | 重复import | worker.py | 统一整理 |
| P2-5 | print/logging混用 | smart_scoring_v2.py | 统一使用logging |
| P2-6 | KIMI_MAX_CONCURRENT不匹配 | .env | P2-2修复时同步评估 |

---

## 五、P3 级别（按需）

- 注释版本号清理
- Qwen模型命名统一
- 代码格式化

---

## 六、执行计划

### 立即执行（今晚）
- [ ] P1-1: 创建 scene_utils.py
- [ ] P1-2: 启用 SQLite WAL 模式
- [ ] P1-5: 盘点 corrections 表

### 明天
- [ ] P1-3: 修复两数据库事务不一致
- [ ] P1-4: SQL注入简化修复
- [ ] P1-6: find_related_sessions 封装

### 本周
- [ ] P2 级别问题批量处理

---

## 七、验证标准

修复完成后，系统应达到：
- ✅ 无 P0/P1 级别问题
- ✅ 1000通批量分析一次性完成无错误
- ✅ 飞书通知无重复
- ✅ 数据库无 locked 错误

---

**制定者**: 小虾米  
**日期**: 2026-04-17 22:00  
**版本**: v2.0（按Opus 4.6建议调整）