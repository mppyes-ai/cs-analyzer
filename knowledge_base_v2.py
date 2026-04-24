"""结构化规则知识库模块 - CS-Analyzer v2

基于大牛A&B反馈的混合双擎架构：
- SQLite: 存储完整结构化规则（主数据）
- LanceDB: 存储复合文本向量（检索索引）

作者: 小虾米
更新: 2026-03-17
"""

import sqlite3
import json
import os
from datetime import datetime
from typing import List, Dict, Optional, Tuple
import pandas as pd

# 数据库路径
DB_PATH = os.path.join(os.path.dirname(__file__), "data", "cs_analyzer_new.db")
LANCE_DB_PATH = os.path.join(os.path.dirname(__file__), "data", "knowledge.lance")

# ========== 数据库连接 ==========

def get_connection():
    """获取SQLite数据库连接"""
    return sqlite3.connect(DB_PATH, check_same_thread=False)

# ========== 表结构初始化 ==========

def init_rules_tables():
    """初始化结构化规则表（v2架构）"""
    conn = get_connection()
    cursor = conn.cursor()
    
    # 主规则表 - 存储完整结构化规则
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS rules (
            rule_id TEXT PRIMARY KEY,
            rule_type TEXT NOT NULL DEFAULT 'scoring',
            
            -- 场景信息
            scene_category TEXT,
            scene_sub_category TEXT,
            scene_description TEXT,
            
            -- 触发条件
            trigger_keywords TEXT,  -- JSON数组
            trigger_intent TEXT,
            trigger_mood TEXT,
            trigger_dimension_hint TEXT,  -- JSON数组
            trigger_confidence_threshold REAL DEFAULT 0.7,
            trigger_valid_from TEXT,
            trigger_valid_to TEXT,
            
            -- 评分规则
            rule_dimension TEXT,
            rule_priority TEXT DEFAULT 'high',
            rule_criteria TEXT,
            rule_score_guide TEXT,  -- JSON对象
            rule_weight_adjustment TEXT,
            
            -- 案例（简化存储，详细案例可单独表）
            examples TEXT,  -- JSON数组
            
            -- 推理说明
            reasoning TEXT,  -- JSON对象
            
            -- 关联
            related_rules TEXT,  -- JSON数组
            related_products TEXT,  -- JSON数组
            tags TEXT,  -- JSON数组
            
            -- 来源
            source_type TEXT,
            source_session_id TEXT,
            source_correction_id TEXT,
            source_staff_name TEXT,
            
            -- 状态管理
            status TEXT DEFAULT 'pending',  -- pending/approved/rejected/deprecated
            created_at TEXT,
            updated_at TEXT,
            approved_at TEXT,
            approved_by TEXT,
            version INTEGER DEFAULT 1,
            
            -- 统计
            retrieval_count INTEGER DEFAULT 0,
            last_retrieved_at TEXT,
            effectiveness_score REAL,
            
            -- 完整JSON备份
            full_json TEXT
        )
    """)
    
    # 规则案例详细表（可选，用于存储多案例）
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS rule_examples (
            example_id INTEGER PRIMARY KEY AUTOINCREMENT,
            rule_id TEXT NOT NULL,
            case_type TEXT,  -- positive/negative
            dialogue_snippet TEXT,
            ai_score_before INTEGER,
            human_corrected_score INTEGER,
            explanation TEXT,
            key_moment TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (rule_id) REFERENCES rules(rule_id) ON DELETE CASCADE
        )
    """)
    
    # 创建索引
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_rules_status ON rules(status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_rules_scene ON rules(scene_category)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_rules_dimension ON rules(rule_dimension)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_rules_correction ON rules(source_correction_id)")
    
    conn.commit()
    conn.close()
    print("✅ 规则表初始化完成")

# ========== 复合文本生成 ==========

def generate_combined_text(rule_data: Dict) -> str:
    """生成用于向量化的复合文本
    
    格式: "场景描述：[xxx]。触发关键词：[xxx]。核心判定：[xxx]。标签：[xxx]"
    
    Args:
        rule_data: 规则数据字典
        
    Returns:
        拼接后的复合文本
    """
    scene_desc = rule_data.get('scene', {}).get('description', '')
    
    keywords = rule_data.get('trigger', {}).get('keywords', [])
    keywords_str = ','.join(keywords) if keywords else ''
    
    criteria = rule_data.get('rule', {}).get('criteria', '')
    
    tags = rule_data.get('tags', [])
    tags_str = ','.join(tags) if tags else ''
    
    combined = f"场景描述：{scene_desc}。触发关键词：{keywords_str}。核心判定：{criteria}。标签：{tags_str}"
    return combined

# ========== 规则CRUD ==========

def save_rule_draft_v2(rule_data: Dict, correction_id: str = None) -> str:
    """保存规则草案（v2结构化存储）
    
    Args:
        rule_data: 符合v2 schema的规则JSON
        correction_id: 关联的矫正记录ID
        
    Returns:
        rule_id: 生成的规则ID
    """
    init_rules_tables()
    conn = get_connection()
    cursor = conn.cursor()
    
    # 生成rule_id
    import uuid
    rule_id = f"rule_{uuid.uuid4().hex[:8]}"
    
    now = datetime.now().isoformat()
    
    # 提取字段
    scene = rule_data.get('scene', {})
    trigger = rule_data.get('trigger', {})
    rule = rule_data.get('rule', {})
    source = rule_data.get('source', {})
    reasoning = rule_data.get('reasoning', {})
    
    cursor.execute("""
        INSERT INTO rules (
            rule_id, rule_type,
            scene_category, scene_sub_category, scene_description,
            trigger_keywords, trigger_intent, trigger_mood, trigger_dimension_hint,
            trigger_confidence_threshold, trigger_valid_from, trigger_valid_to,
            rule_dimension, rule_priority, rule_criteria, rule_score_guide,
            examples, reasoning, related_rules, related_products, tags,
            source_type, source_session_id, source_correction_id, source_staff_name,
            status, created_at, updated_at, version, full_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        rule_id,
        rule_data.get('rule_type', 'scoring'),
        scene.get('category'),
        scene.get('sub_category'),
        scene.get('description'),
        json.dumps(trigger.get('keywords', []), ensure_ascii=False),
        trigger.get('intent'),
        trigger.get('mood'),
        json.dumps(trigger.get('dimension_hint', []), ensure_ascii=False),
        trigger.get('confidence_threshold', 0.7),
        trigger.get('valid_from', now),
        trigger.get('valid_to'),
        rule.get('dimension'),
        rule.get('priority', 'high'),
        rule.get('criteria'),
        json.dumps(rule.get('score_guide', {}), ensure_ascii=False),
        json.dumps(rule_data.get('examples', []), ensure_ascii=False),
        json.dumps(reasoning, ensure_ascii=False),
        json.dumps(rule_data.get('related_rules', []), ensure_ascii=False),
        json.dumps(rule_data.get('related_products', []), ensure_ascii=False),
        json.dumps(rule_data.get('tags', []), ensure_ascii=False),
        source.get('type', 'correction'),
        source.get('session_id'),
        correction_id or source.get('correction_id'),
        source.get('staff_name'),
        'pending',
        now,
        now,
        1,
        json.dumps(rule_data, ensure_ascii=False)
    ))
    
    conn.commit()
    conn.close()
    
    return rule_id

def approve_rule(rule_id: str, approved_by: str = 'admin') -> bool:
    """审核通过规则
    
    Args:
        rule_id: 规则ID
        approved_by: 审核人
        
    Returns:
        是否成功
    """
    conn = get_connection()
    cursor = conn.cursor()
    
    now = datetime.now().isoformat()
    
    cursor.execute("""
        UPDATE rules 
        SET status = 'approved', approved_at = ?, approved_by = ?, updated_at = ?
        WHERE rule_id = ?
    """, (now, approved_by, now, rule_id))
    
    conn.commit()
    success = cursor.rowcount > 0
    conn.close()
    
    return success

def reject_rule(rule_id: str, reason: str = None) -> bool:
    """拒绝规则
    
    Args:
        rule_id: 规则ID
        reason: 拒绝原因
        
    Returns:
        是否成功
    """
    conn = get_connection()
    cursor = conn.cursor()
    
    now = datetime.now().isoformat()
    
    cursor.execute("""
        UPDATE rules 
        SET status = 'rejected', updated_at = ?
        WHERE rule_id = ?
    """, (now, rule_id))
    
    conn.commit()
    success = cursor.rowcount > 0
    conn.close()
    
    return success

def update_rule(rule_id: str, updates: Dict) -> bool:
    """更新规则字段
    
    Args:
        rule_id: 规则ID
        updates: 要更新的字段字典
        
    Returns:
        是否成功
    """
    conn = get_connection()
    cursor = conn.cursor()
    
    now = datetime.now().isoformat()
    
    # 构建更新SQL
    allowed_fields = {
        'scene_category', 'scene_sub_category', 'scene_description',
        'trigger_keywords', 'trigger_intent', 'trigger_mood',
        'trigger_valid_from', 'trigger_valid_to',
        'rule_dimension', 'rule_criteria', 'rule_score_guide',
        'tags', 'full_json'
    }
    
    set_clauses = []
    values = []
    
    for field, value in updates.items():
        if field in allowed_fields:
            set_clauses.append(f"{field} = ?")
            # JSON字段需要序列化
            if field in ['trigger_keywords', 'rule_score_guide', 'tags'] and isinstance(value, (list, dict)):
                values.append(json.dumps(value, ensure_ascii=False))
            else:
                values.append(value)
    
    if not set_clauses:
        conn.close()
        return False
    
    set_clauses.append("updated_at = ?")
    values.append(now)
    values.append(rule_id)
    
    sql = f"UPDATE rules SET {', '.join(set_clauses)} WHERE rule_id = ?"
    cursor.execute(sql, values)
    
    conn.commit()
    success = cursor.rowcount > 0
    conn.close()
    
    return success

def delete_rule(rule_id: str) -> bool:
    """删除规则
    
    Args:
        rule_id: 规则ID
        
    Returns:
        是否成功
    """
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("DELETE FROM rules WHERE rule_id = ?", (rule_id,))
    
    conn.commit()
    success = cursor.rowcount > 0
    conn.close()
    
    return success

def get_rules_by_status(status: str = None, search_query: str = None) -> pd.DataFrame:
    """根据状态和搜索条件获取规则列表
    
    Args:
        status: 状态筛选（pending/approved/rejected/all）
        search_query: 搜索关键词
        
    Returns:
        规则DataFrame
    """
    init_rules_tables()
    conn = get_connection()
    
    # 构建WHERE条件
    conditions = []
    params = []
    
    if status and status != 'all':
        conditions.append("status = ?")
        params.append(status)
    
    if search_query:
        conditions.append("(rule_id LIKE ? OR rule_criteria LIKE ? OR scene_category LIKE ?)")
        search_pattern = f"%{search_query}%"
        params.extend([search_pattern, search_pattern, search_pattern])
    
    where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""
    
    df = pd.read_sql_query(f"""
        SELECT 
            rule_id,
            rule_type,
            scene_category,
            scene_sub_category,
            rule_dimension,
            rule_criteria,
            source_correction_id,
            created_at,
            status
        FROM rules 
        {where_clause}
        ORDER BY created_at DESC
    """, conn, params=params if params else None)
    
    conn.close()
    return df

def get_rule_by_id(rule_id: str) -> Optional[Dict]:
    """根据ID获取规则"""
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM rules WHERE rule_id = ?", (rule_id,))
    row = cursor.fetchone()
    
    if not row:
        conn.close()
        return None
    
    # 转换为字典（在关闭连接前获取列信息）
    columns = [desc[0] for desc in cursor.description]
    conn.close()
    rule = dict(zip(columns, row))
    
    # 解析JSON字段
    json_fields = ['trigger_keywords', 'trigger_dimension_hint', 'rule_score_guide', 
                   'examples', 'reasoning', 'related_rules', 'related_products', 'tags']
    for field in json_fields:
        if rule.get(field):
            try:
                rule[field] = json.loads(rule[field])
            except:
                pass
    
    return rule

def get_pending_rules() -> pd.DataFrame:
    """获取待审核的规则列表"""
    init_rules_tables()
    conn = get_connection()
    
    df = pd.read_sql_query("""
        SELECT 
            rule_id,
            rule_type,
            scene_category,
            scene_sub_category,
            rule_dimension,
            rule_criteria,
            source_correction_id,
            created_at
        FROM rules 
        WHERE status = 'pending' 
        ORDER BY created_at DESC
    """, conn)
    
    conn.close()
    return df

def get_approved_rules(scene_category: str = None, dimension: str = None) -> List[Dict]:
    """获取已审核通过的规则（用于检索）
    
    Args:
        scene_category: 场景筛选（可选）
        dimension: 维度筛选（可选）
        
    Returns:
        规则列表
    """
    conn = get_connection()
    cursor = conn.cursor()
    
    query = "SELECT * FROM rules WHERE status = 'approved'"
    params = []
    
    if scene_category:
        query += " AND scene_category = ?"
        params.append(scene_category)
    
    if dimension:
        query += " AND rule_dimension = ?"
        params.append(dimension)
    
    query += " AND (trigger_valid_to IS NULL OR trigger_valid_to > datetime('now'))"
    query += " ORDER BY rule_priority DESC, created_at DESC"
    
    cursor.execute(query, params)
    rows = cursor.fetchall()
    
    columns = [desc[0] for desc in cursor.description]
    
    rules = []
    for row in rows:
        rule = dict(zip(columns, row))
        
        # 解析JSON字段
        json_fields = ['trigger_keywords', 'trigger_dimension_hint', 'rule_score_guide',
                       'examples', 'reasoning', 'related_rules', 'related_products', 'tags']
        for field in json_fields:
            if rule.get(field):
                try:
                    rule[field] = json.loads(rule[field])
                except:
                    pass
        
        rules.append(rule)
    
    conn.close()
    return rules

# ========== LanceDB 向量索引 ==========

def init_lancedb_vector_store():
    """初始化LanceDB向量存储"""
    try:
        import lancedb
        import pyarrow as pa
        
        # 连接数据库
        db = lancedb.connect(LANCE_DB_PATH)
        
        # 检查表是否存在
        if "rule_vectors" not in db.table_names():
            # 创建向量表
            schema = pa.schema([
                pa.field("rule_id", pa.string()),
                pa.field("vector", pa.list_(pa.float32(), 2560)),  # Qwen3-Embedding-4B使用2560维向量
                pa.field("scene_category", pa.string()),
                pa.field("scene_sub_category", pa.string()),
                pa.field("rule_dimension", pa.string()),
                pa.field("trigger_intent", pa.string()),
                pa.field("trigger_mood", pa.string()),
                pa.field("status", pa.string()),
                pa.field("valid_from", pa.string()),
                pa.field("valid_to", pa.string()),
            ])
            
            # 创建空表
            table = db.create_table("rule_vectors", schema=schema)
            print("✅ LanceDB向量表创建成功")
        else:
            print("✅ LanceDB向量表已存在")
            
    except ImportError:
        print("⚠️ LanceDB未安装，向量功能不可用")
        return False
    except Exception as e:
        print(f"⚠️ LanceDB初始化失败: {e}")
        return False
    
    return True

def sync_rule_to_vector_db(rule_id: str, embedding_model = None) -> bool:
    """将规则同步到LanceDB向量索引
    
    Args:
        rule_id: 规则ID
        embedding_model: 嵌入模型（如未提供，使用默认）
        
    Returns:
        是否成功
    """
    try:
        import lancedb
        import numpy as np
        
        # 获取规则
        rule = get_rule_by_id(rule_id)
        if not rule:
            print(f"⚠️ 规则不存在: {rule_id}")
            return False
        
        # 检查状态
        if rule.get('status') != 'approved':
            print(f"⚠️ 规则未审核通过: {rule_id}")
            return False
        
        # 生成复合文本
        combined_text = generate_combined_text({
            'scene': {
                'description': rule.get('scene_description', '')
            },
            'trigger': {
                'keywords': rule.get('trigger_keywords', [])
            },
            'rule': {
                'criteria': rule.get('rule_criteria', '')
            },
            'tags': rule.get('tags', [])
        })
        
        # 生成向量
        if embedding_model is None:
            # 使用默认模型
            from sentence_transformers import SentenceTransformer
            embedding_model = SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2')
        
        vector = embedding_model.encode(combined_text).tolist()
        
        # 写入LanceDB
        db = lancedb.connect(LANCE_DB_PATH)
        table = db.open_table("rule_vectors")
        
        # 删除旧记录（如果存在）
        table.delete(f"rule_id = '{rule_id}'")
        
        # 插入新记录
        table.add([{
            "rule_id": rule_id,
            "vector": vector,
            "scene_category": rule.get('scene_category'),
            "scene_sub_category": rule.get('scene_sub_category'),
            "rule_dimension": rule.get('rule_dimension'),
            "trigger_intent": rule.get('trigger_intent'),
            "trigger_mood": rule.get('trigger_mood'),
            "status": rule.get('status'),
            "valid_from": rule.get('trigger_valid_from'),
            "valid_to": rule.get('trigger_valid_to'),
        }])
        
        print(f"✅ 规则已同步到向量库: {rule_id}")
        return True
        
    except Exception as e:
        print(f"⚠️ 同步到向量库失败: {e}")
        return False

# ========== 使用统一Embedding单例 ==========
from embedding_utils import get_embedding_model as get_global_embedding_model


# ========== 检索接口 ==========

def search_rules_by_vector(query_text: str, top_k: int = 5, 
                           scene_filter: str = None,
                           dimension_filter: str = None,
                           embedding_model = None) -> List[Dict]:
    """向量检索规则
    
    Args:
        query_text: 查询文本
        top_k: 返回数量
        scene_filter: 场景过滤（可选）
        dimension_filter: 维度过滤（可选）
        embedding_model: 嵌入模型（可选，默认使用全局单例）
        
    Returns:
        匹配的规则列表
    """
    try:
        import lancedb
        
        # 使用传入的模型或全局单例
        model = embedding_model or get_global_embedding_model()
        query_vector = model.encode(query_text).tolist()
        
        # 连接LanceDB
        db = lancedb.connect(LANCE_DB_PATH)
        table = db.open_table("rule_vectors")
        
        # 构建过滤条件
        filters = ["status = 'approved'"]
        if scene_filter:
            filters.append(f"scene_category = '{scene_filter}'")
        if dimension_filter:
            filters.append(f"rule_dimension = '{dimension_filter}'")
        
        filter_str = " AND ".join(filters)
        
        # 向量搜索
        results = table.search(query_vector).where(filter_str, prefilter=True).limit(top_k).to_pandas()
        
        # 获取完整规则
        rules = []
        for _, row in results.iterrows():
            rule = get_rule_by_id(row['rule_id'])
            if rule:
                rule['_distance'] = row.get('_distance', 0)
                rules.append(rule)
        
        return rules
        
    except Exception as e:
        print(f"⚠️ 向量检索失败: {e}")
        return []

# ========== 统计 ==========

def get_rules_stats() -> Dict:
    """获取规则库统计"""
    init_rules_tables()
    conn = get_connection()
    cursor = conn.cursor()
    
    stats = {}
    
    # 总数
    cursor.execute("SELECT COUNT(*) FROM rules")
    stats['total'] = cursor.fetchone()[0]
    
    # 各状态数量
    cursor.execute("SELECT status, COUNT(*) FROM rules GROUP BY status")
    for status, count in cursor.fetchall():
        stats[f'status_{status}'] = count
    
    # 各场景数量
    cursor.execute("SELECT scene_category, COUNT(*) FROM rules WHERE status = 'approved' GROUP BY scene_category")
    stats['by_scene'] = {scene: count for scene, count in cursor.fetchall() if scene}
    
    # 各维度数量
    cursor.execute("SELECT rule_dimension, COUNT(*) FROM rules WHERE status = 'approved' GROUP BY rule_dimension")
    stats['by_dimension'] = {dim: count for dim, count in cursor.fetchall() if dim}
    
    conn.close()
    return stats

# ========== 初始化检查 ==========

def check_v2_tables_exist() -> bool:
    """检查v2表结构是否存在"""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='rules'")
        result = cursor.fetchone()
        
        conn.close()
        return result is not None
    except:
        return False

if __name__ == "__main__":
    # 测试初始化
    init_rules_tables()
    init_lancedb_vector_store()
    print("\n📊 规则库统计:", get_rules_stats())
