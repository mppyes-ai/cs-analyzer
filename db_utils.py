"""数据库工具模块 - cs-analyzer专用"""
import sqlite3
import pandas as pd
import json
import os

# 数据库路径
DB_PATH = os.path.join(os.path.dirname(__file__), "data", "cs_analyzer_new.db")

def get_connection():
    """获取数据库连接,启用WAL模式提升并发性能"""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)

    # 【P1-2修复】启用WAL模式,减少database is locked错误
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

    return conn

def init_sessions_table():
    """初始化会话表(如果不存在)"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            user_id TEXT,
            staff_name TEXT,
            messages TEXT,
            summary TEXT,
            professionalism_score INTEGER,
            standardization_score INTEGER,
            policy_execution_score INTEGER,
            conversion_score INTEGER,
            total_score INTEGER,
            analysis_json TEXT,
            strengths TEXT,
            issues TEXT,
            suggestions TEXT,
            session_count INTEGER DEFAULT 1,
            start_time TEXT,
            end_time TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_transfer INTEGER DEFAULT 0,
            transfer_from TEXT,
            transfer_to TEXT,
            transfer_reason TEXT,
            related_sessions TEXT
        )
    """)

    conn.commit()
    conn.close()

def load_sessions():
    """加载所有会话"""
    conn = get_connection()
    df = pd.read_sql_query("""
        SELECT
            session_id,
            user_id,
            staff_name,
            messages,
            summary,
            professionalism_score,
            standardization_score,
            policy_execution_score,
            conversion_score,
            total_score,
            analysis_json,
            strengths,
            issues,
            suggestions,
            session_count,
            start_time,
            end_time,
            created_at,
            is_transfer,
            transfer_from,
            transfer_to,
            transfer_reason,
            related_sessions
        FROM sessions
        ORDER BY created_at DESC
    """, conn)
    conn.close()
    return df

def get_session_by_id(session_id):
    """根据ID获取单一会话"""
    conn = get_connection()
    df = pd.read_sql_query("""
        SELECT * FROM sessions WHERE session_id = ?
    """, conn, params=(session_id,))
    conn.close()
    return df.iloc[0] if not df.empty else None


# ========== 矫正系统 ==========

def init_correction_tables():
    """初始化矫正相关表"""
    conn = get_connection()
    cursor = conn.cursor()

    # 矫正记录表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS corrections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            changed_fields TEXT NOT NULL,
            reason TEXT NOT NULL,
            other_reason TEXT DEFAULT '',  -- 单独存储"其他说明",方便筛选分析
            corrected_by TEXT DEFAULT 'admin',
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 规则草案表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS rule_drafts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            correction_id INTEGER,
            rule_type TEXT,
            trigger_condition TEXT,
            rule_content TEXT,
            source_session TEXT,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()

def save_correction_v2(session_id, changed_fields, reason, other_reason="", corrected_by="admin", status="pending", auto_extract_rule=True):
    """保存矫正记录（V3版本 - 支持自动规则提取）
    
    Args:
        session_id: 会话ID
        changed_fields: JSON格式 [{"field": "professionalism_score", "old": 3, "new": 4}, ...]
        reason: 各维度的矫正说明（拼接文本）
        other_reason: 其他说明（单独字段，方便筛选分析）
        corrected_by: 矫正人
        status: 状态，默认'pending'，"无需矫正"时用'no_action'
        auto_extract_rule: 是否自动提取规则（默认True）
    """
    conn = get_connection()
    cursor = conn.cursor()

    # 1. 保存矫正记录
    cursor.execute("""
        INSERT INTO corrections (session_id, changed_fields, reason, other_reason, corrected_by, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
    """, (session_id, json.dumps(changed_fields, ensure_ascii=False), reason, other_reason, corrected_by, status))
    
    correction_id = cursor.lastrowid
    
    # 2. 【v2.6.6修复】自动提取规则（无论是否矫正）
    if auto_extract_rule:
        try:
            # 获取会话数据（使用当前连接，避免database is locked）
            cursor.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,))
            row = cursor.fetchone()
            
            if row:
                # 构建会话数据字典
                columns = [description[0] for description in cursor.description]
                session_data = dict(zip(columns, row))
                
                # 提取规则
                from rule_extractor_v2 import extract_rule_from_session
                rule_draft = extract_rule_from_session(session_id, session_data, reason)
                
                if rule_draft:
                    # 保存规则草案（使用当前连接）
                    cursor.execute("""
                        INSERT INTO rule_drafts (correction_id, rule_type, trigger_condition, rule_content, source_session, status, created_at)
                        VALUES (?, ?, ?, ?, ?, 'pending', datetime('now'))
                    """, (correction_id, 
                          rule_draft.get('rule_type', 'general'),
                          json.dumps(rule_draft.get('trigger', {}), ensure_ascii=False),
                          json.dumps(rule_draft, ensure_ascii=False),
                          session_id))
                    
                    print(f"   ✅ 自动提取规则: {rule_draft.get('rule_id', 'unknown')}")
                    
                    # 如果回答正确且无需矫正，自动提交审批
                    if status == 'no_action':
                        # 更新状态为pending_approval
                        draft_id = cursor.lastrowid
                        # 【修复】rule_drafts表可能没有submitted_by等字段，只更新status
                        cursor.execute("""
                            UPDATE rule_drafts 
                            SET status = 'pending_approval'
                            WHERE id = ?
                        """, (draft_id,))
                        print(f"   ✅ 规则已提交审批: {rule_draft.get('rule_id', 'unknown')}")
                        
        except Exception as e:
            print(f"   ⚠️ 规则提取失败: {e}")
            import traceback
            traceback.print_exc()
    
    # 3. 更新 sessions 表中的实际分数（仅当实际修改了分值时）
    has_real_change = any(f.get('old') != f.get('new') for f in changed_fields)
    if has_real_change:
        for field_data in changed_fields:
            field_name = field_data.get('field')
            new_value = field_data.get('new')
            old_value = field_data.get('old')
            # 只有实际修改了才更新
            # 白名单校验，防止SQL注入
            ALLOWED_FIELDS = {'professionalism_score', 'standardization_score', 
                            'policy_execution_score', 'conversion_score', 'total_score'}
            if field_name not in ALLOWED_FIELDS:
                raise ValueError(f"非法字段: {field_name}")
            # 【修复】移除冗余的 field_name in ALLOWED_FIELDS 检查，前面已确保
            if new_value is not None and new_value != old_value:
                cursor.execute(f"""
                    UPDATE sessions SET {field_name} = ? WHERE session_id = ?
                """, (new_value, session_id))
        
        # 重新计算总分
        cursor.execute("""
            SELECT 
                COALESCE(professionalism_score, 0) +
                COALESCE(standardization_score, 0) +
                COALESCE(policy_execution_score, 0) +
                COALESCE(conversion_score, 0)
            FROM sessions WHERE session_id = ?
        """, (session_id,))
        result = cursor.fetchone()
        if result and result[0] is not None:
            total_score = result[0]
            cursor.execute("""
                UPDATE sessions SET total_score = ? WHERE session_id = ?
            """, (total_score, session_id))
    
    conn.commit()
    conn.close()
    
    return correction_id

def get_pending_corrections():
    """获取待处理的矫正记录(简化版 - 通过 rules 表关联判断)

    返回未提取规则且不是 no_action 的矫正记录
    """
    init_correction_tables()  # 确保表存在
    conn = get_connection()
    df = pd.read_sql_query("""
        SELECT c.* FROM corrections c
        LEFT JOIN rules r ON c.id = r.source_correction_id
        WHERE r.rule_id IS NULL
          AND c.status != 'no_action'
        ORDER BY c.created_at DESC
    """, conn)
    conn.close()
    return df

def get_correction_by_id(correction_id):
    """根据ID获取矫正记录"""
    conn = get_connection()
    df = pd.read_sql_query("""
        SELECT * FROM corrections WHERE id = ?
    """, conn, params=(correction_id,))
    conn.close()
    return df.iloc[0] if not df.empty else None

def update_correction_status(correction_id, status):
    """更新矫正记录状态"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE corrections SET status = ? WHERE id = ?
    """, (status, correction_id))
    conn.commit()
    conn.close()

def get_corrected_ids():
    """获取已矫正的会话ID列表"""
    try:
        conn = get_connection()
        df = pd.read_sql_query("""
            SELECT DISTINCT session_id FROM corrections
        """, conn)
        conn.close()
        return df['session_id'].tolist()
    except:
        return []




# ========== 规则草案 ==========

def submit_rule_for_approval(rule_id, submitted_by="system", reason=""):
    """提交规则审批
    
    Args:
        rule_id: 规则ID
        submitted_by: 提交人
        reason: 提交原因
    """
    try:
        conn = get_connection()
        cursor = conn.cursor()
        
        # 更新规则状态为pending_approval
        cursor.execute("""
            UPDATE rule_drafts 
            SET status = 'pending_approval', 
                submitted_by = ?,
                submitted_reason = ?,
                submitted_at = datetime('now')
            WHERE id = ? OR rule_content LIKE ?
        """, (submitted_by, reason, rule_id, f'%"rule_id": "{rule_id}"%'))
        
        conn.commit()
        conn.close()
        
        print(f"   ✅ 规则已提交审批: {rule_id}")
        return True
        
    except Exception as e:
        print(f"   ⚠️ 提交审批失败: {e}")
        return False

def save_rule_draft(correction_id, rule_type, trigger_condition, rule_content, source_session):
    """保存规则草案"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO rule_drafts (correction_id, rule_type, trigger_condition, rule_content, source_session, status, created_at)
        VALUES (?, ?, ?, ?, ?, 'pending', datetime('now'))
    """, (correction_id, rule_type, trigger_condition, rule_content, source_session))

    conn.commit()
    draft_id = cursor.lastrowid
    conn.close()
    return draft_id

def get_pending_rule_drafts():
    """获取待审核的规则草案"""
    conn = get_connection()
    df = pd.read_sql_query("""
        SELECT * FROM rule_drafts
        WHERE status = 'pending'
        ORDER BY created_at DESC
    """, conn)
    conn.close()
    return df

def get_rule_draft_by_id(draft_id):
    """根据ID获取规则草案"""
    conn = get_connection()
    df = pd.read_sql_query("""
        SELECT * FROM rule_drafts WHERE id = ?
    """, conn, params=(draft_id,))
    conn.close()
    return df.iloc[0] if not df.empty else None

def update_rule_draft_status(draft_id, status):
    """更新规则草案状态"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE rule_drafts SET status = ? WHERE id = ?
    """, (status, draft_id))
    conn.commit()
    conn.close()


# ========== 统计 ==========

def get_correction_stats():
    """获取矫正统计(简化版 - 不再使用复杂状态)"""
    try:
        conn = get_connection()
        stats = {}

        cursor = conn.cursor()
        # 总矫正数
        cursor.execute("SELECT COUNT(*) FROM corrections")
        stats['total'] = cursor.fetchone()[0]

        # 待处理数(未提取规则)- 通过关联 rules 表判断
        cursor.execute("""
            SELECT COUNT(*) FROM corrections c
            LEFT JOIN rules r ON c.id = r.source_correction_id
            WHERE r.rule_id IS NULL AND c.status != 'no_action'
        """)
        stats['pending'] = cursor.fetchone()[0]

        # 已处理数(已提取规则或无行动)
        cursor.execute("""
            SELECT COUNT(*) FROM corrections c
            LEFT JOIN rules r ON c.id = r.source_correction_id
            WHERE r.rule_id IS NOT NULL OR c.status = 'no_action'
        """)
        stats['processed'] = cursor.fetchone()[0]

        conn.close()
        return stats
    except:
        return {'total': 0, 'pending': 0, 'processed': 0}


def get_corrected_score(session_id, dimension, original_score):
    """获取矫正后的评分(兼容旧版本)"""
    try:
        conn = get_connection()
        cursor = conn.cursor()

        # 检查是否有 V2 格式的矫正记录
        cursor.execute("PRAGMA table_info(corrections)")
        columns = [row[1] for row in cursor.fetchall()]

        if 'changed_fields' in columns:
            # V2 格式,需要解析 JSON
            cursor.execute("""
                SELECT changed_fields FROM corrections
                WHERE session_id = ? AND status = 'synced'
                ORDER BY created_at DESC LIMIT 1
            """, (session_id,))
            result = cursor.fetchone()
            if result:
                import json
                changed_fields = json.loads(result[0])
                for field in changed_fields:
                    if field.get('field') == f"{dimension}_score":
                        conn.close()
                        return field.get('new', original_score)
        else:
            # 旧格式
            cursor.execute("""
                SELECT corrected_score FROM corrections
                WHERE session_id = ? AND dimension = ?
                ORDER BY created_at DESC LIMIT 1
            """, (session_id, dimension))
            result = cursor.fetchone()
            if result:
                conn.close()
                return result[0]

        conn.close()
        return original_score
    except:
        return original_score


def is_session_corrected(session_id):
    """检查会话是否已矫正"""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM corrections WHERE session_id = ?", (session_id,))
        count = cursor.fetchone()[0]
        conn.close()
        return count > 0
    except:
        return False


def get_correction_with_session(correction_id):
    """获取矫正记录及关联的会话数据"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT c.*, s.messages, s.summary, s.staff_name
        FROM corrections c
        LEFT JOIN sessions s ON c.session_id = s.session_id
        WHERE c.id = ?
    """, (correction_id,))

    result = cursor.fetchone()
    conn.close()

    if result:
        columns = ['id', 'session_id', 'changed_fields', 'reason', 'corrected_by',
                   'status', 'created_at', 'messages', 'summary', 'staff_name']
        return dict(zip(columns, result))
    return None


def get_correction_by_session(session_id):
    """根据会话ID获取矫正记录"""
    import pandas as pd
    conn = get_connection()

    df = pd.read_sql_query("""
        SELECT * FROM corrections
        WHERE session_id = ?
        ORDER BY created_at DESC
    """, conn, params=(session_id,))

    conn.close()
    return df