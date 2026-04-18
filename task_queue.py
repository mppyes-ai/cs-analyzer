#!/usr/bin/env python3
"""异步任务队列系统 - 客服分析任务队列

基于SQLite的轻量级队列，支持：
- 任务提交（pending）
- 任务处理（processing）
- 结果保存（completed/failed）
- 进度查询

作者: 小虾米
更新: 2026-03-18
"""

import json
import sqlite3
import time
import sys
import os
from datetime import datetime
from typing import List, Dict, Optional

sys.path.insert(0, os.path.dirname(__file__))

# 队列数据库路径
QUEUE_DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'task_queue.db')


def get_queue_connection():
    """获取队列数据库连接，启用WAL模式提升并发性能"""
    conn = sqlite3.connect(QUEUE_DB_PATH, check_same_thread=False)
    
    # 【P1-2修复】启用WAL模式，减少database is locked错误
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    
    return conn


def get_pending_tasks_by_user(user_id: str) -> List[Dict]:
    """
    【N-7】获取指定用户的待处理任务（⚠️ 当前未被调用，死代码）
    
    注意：该函数当前未被任何代码调用。如需使用，需修复：
    1. LIKE 查询存在注入风险（user_id 包含特殊字符时）
    2. JSON 空格差异可能导致匹配失败
    
    如需启用，建议改为：
    - 使用 JSON 函数提取 user_id 进行比较（SQLite 3.38+ 支持）
    - 或建立 user_id 索引列
    
    Args:
        user_id: 用户ID
        
    Returns:
        任务列表
    """
    import warnings
    warnings.warn(
        "get_pending_tasks_by_user is dead code and has SQL injection risks. "
        "Consider using JSON_EXTRACT or a dedicated user_id column instead of LIKE.",
        UserWarning
    )
    conn = get_queue_connection()
    try:
        # 【P1-6修复】添加%通配符确保JSON字段匹配
        cursor = conn.execute(
            """SELECT task_id, session_id, session_data 
               FROM analysis_tasks 
               WHERE status = 'pending' AND session_data LIKE ?
            """,
            (f'%"user_id": "{user_id}"%',)
        )
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


# 【P1-6修复】删除重复的 get_queue_connection 定义
# 该函数已在文件顶部定义


def init_queue_tables():
    """初始化队列表"""
    conn = get_queue_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS analysis_tasks (
            task_id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id TEXT DEFAULT '',
            session_id TEXT NOT NULL,
            session_data TEXT NOT NULL,
            scene TEXT DEFAULT '售前阶段',
            status TEXT DEFAULT 'pending',
            result TEXT,
            error TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            started_at TIMESTAMP,
            completed_at TIMESTAMP,
            retry_count INTEGER DEFAULT 0
        )
    ''')
    
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_status ON analysis_tasks(status)
    ''')
    
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_batch_status ON analysis_tasks(batch_id, status)
    ''')
    
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_session ON analysis_tasks(session_id)
    ''')
    
    conn.commit()
    conn.close()


def submit_task(session_id: str, session_data: Dict, batch_id: str = '', scene: str = '售前阶段') -> int:
    """提交分析任务到队列
    
    Args:
        session_id: 会话ID
        session_data: 会话数据（含messages等）
        batch_id: 批次ID，用于区分不同分析任务
        scene: 场景分类（售前阶段/售中阶段/售后阶段/客诉处理）
        
    Returns:
        任务ID
    """
    init_queue_tables()
    
    conn = get_queue_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT INTO analysis_tasks (session_id, session_data, batch_id, scene, status, created_at)
        VALUES (?, ?, ?, ?, 'pending', ?)
    ''', (session_id, json.dumps(session_data, ensure_ascii=False), batch_id, scene, datetime.now().isoformat()))
    
    task_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    return task_id


def get_pending_task() -> Optional[Dict]:
    """获取一个待处理任务
    
    Returns:
        任务字典，如果没有待处理任务返回None
    """
    conn = get_queue_connection()
    cursor = conn.cursor()
    
    # 获取最老的待处理任务
    cursor.execute('''
        SELECT task_id, session_id, session_data, retry_count
        FROM analysis_tasks
        WHERE status = 'pending'
        ORDER BY created_at ASC
        LIMIT 1
    ''')
    
    row = cursor.fetchone()
    
    if row:
        task_id, session_id, session_data, retry_count = row
        
        # 标记为处理中
        cursor.execute('''
            UPDATE analysis_tasks
            SET status = 'processing', started_at = ?
            WHERE task_id = ?
        ''', (datetime.now().isoformat(), task_id))
        
        conn.commit()
        conn.close()
        
        return {
            'task_id': task_id,
            'session_id': session_id,
            'session_data': json.loads(session_data),
            'retry_count': retry_count
        }
    
    conn.close()
    return None


def complete_task(task_id: int, result: Dict):
    """标记任务完成
    
    Args:
        task_id: 任务ID
        result: 分析结果
    """
    conn = get_queue_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        UPDATE analysis_tasks
        SET status = 'completed',
            result = ?,
            completed_at = ?
        WHERE task_id = ?
    ''', (json.dumps(result, ensure_ascii=False), datetime.now().isoformat(), task_id))
    
    conn.commit()
    conn.close()


def cancel_task(task_id: int, reason: str = 'merged'):
    """取消任务（用于会话合并时取消被合并的任务）
    
    Args:
        task_id: 任务ID
        reason: 取消原因，默认'merged'
    """
    conn = get_queue_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        UPDATE analysis_tasks
        SET status = 'cancelled',
            result = ?,
            completed_at = ?
        WHERE task_id = ?
    ''', (json.dumps({'cancel_reason': reason}, ensure_ascii=False), 
          datetime.now().isoformat(), task_id))
    
    conn.commit()
    conn.close()


def fail_task(task_id: int, error: str):
    """标记任务失败
    
    Args:
        task_id: 任务ID
        error: 错误信息
    """
    conn = get_queue_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        UPDATE analysis_tasks
        SET status = 'failed',
            error = ?,
            completed_at = ?,
            retry_count = retry_count + 1
        WHERE task_id = ?
    ''', (error, datetime.now().isoformat(), task_id))
    
    conn.commit()
    conn.close()


def get_queue_stats(batch_id: str = '') -> Dict:
    """获取队列统计信息
    
    Args:
        batch_id: 批次ID，为空时返回全局统计
        
    Returns:
        统计字典
    """
    init_queue_tables()
    
    conn = get_queue_connection()
    cursor = conn.cursor()
    
    if batch_id:
        cursor.execute('''
            SELECT status, COUNT(*)
            FROM analysis_tasks
            WHERE batch_id = ?
            GROUP BY status
        ''', (batch_id,))
    else:
        cursor.execute('''
            SELECT status, COUNT(*)
            FROM analysis_tasks
            GROUP BY status
        ''')
    
    stats = dict(cursor.fetchall())
    conn.close()
    
    return {
        'pending': stats.get('pending', 0),
        'processing': stats.get('processing', 0),
        'completed': stats.get('completed', 0),
        'failed': stats.get('failed', 0),
        'total': sum(stats.values())
    }


def retry_failed_tasks(max_retries: int = 3, base_delay: int = 30) -> int:
    """重试失败任务（带指数退避策略）
    
    策略：
    - 第1次重试：立即执行（delay=0）
    - 第2次重试：30秒后（delay=30s）
    - 第3次重试：60秒后（delay=60s）
    - 超过max_retries次的任务不再重试
    
    Args:
        max_retries: 最大重试次数（默认3次）
        base_delay: 基础延迟秒数（默认30秒）
        
    Returns:
        重试的任务数量
    """
    import time
    
    conn = get_queue_connection()
    cursor = conn.cursor()
    
    # 获取所有失败任务及其重试次数
    cursor.execute('''
        SELECT task_id, retry_count, error
        FROM analysis_tasks
        WHERE status = 'failed'
        AND retry_count < ?
        ORDER BY retry_count ASC, created_at ASC
    ''', (max_retries,))
    
    failed_tasks = cursor.fetchall()
    
    if not failed_tasks:
        conn.close()
        return 0
    
    retried_count = 0
    
    for task_id, retry_count, error in failed_tasks:
        # 计算延迟时间（指数退避）
        if retry_count == 0:
            delay = 0  # 首次失败立即重试
        else:
            # 指数退避：30s, 60s, 120s...
            delay = base_delay * (2 ** (retry_count - 1))
        
        # 检查是否已达到重试时间（基于completed_at时间）
        cursor.execute('''
            SELECT completed_at FROM analysis_tasks WHERE task_id = ?
        ''', (task_id,))
        result = cursor.fetchone()
        
        if result and result[0]:
            completed_time = datetime.fromisoformat(result[0])
            elapsed_seconds = (datetime.now() - completed_time).total_seconds()
            
            if elapsed_seconds < delay:
                # 还未到重试时间，跳过
                print(f"  ⏳ 任务 {task_id} 还需等待 {int(delay - elapsed_seconds)}s 后重试")
                continue
        
        # 重置任务状态为pending
        cursor.execute('''
            UPDATE analysis_tasks
            SET status = 'pending',
                error = NULL,
                started_at = NULL
            WHERE task_id = ?
        ''', (task_id,))
        
        retried_count += 1
        print(f"  🔄 任务 {task_id} 第 {retry_count + 1} 次重试（延迟 {delay}s）")
    
    conn.commit()
    conn.close()
    
    if retried_count > 0:
        print(f"✅ 已重置 {retried_count}/{len(failed_tasks)} 个失败任务为pending状态")
    
    return retried_count


def force_retry_all_failed(max_retries: int = 3) -> int:
    """【修复】强制立即重试所有可重试的失败任务（不等待延迟时间）
    
    用于--once模式，确保Worker退出前处理完所有可重试任务
    
    Args:
        max_retries: 最大重试次数（默认3次）
        
    Returns:
        重试的任务数量
    """
    conn = get_queue_connection()
    cursor = conn.cursor()
    
    # 获取所有可重试的失败任务（忽略延迟时间）
    cursor.execute('''
        SELECT task_id, retry_count
        FROM analysis_tasks
        WHERE status = 'failed'
        AND retry_count < ?
    ''', (max_retries,))
    
    failed_tasks = cursor.fetchall()
    
    if not failed_tasks:
        conn.close()
        return 0
    
    for task_id, retry_count in failed_tasks:
        # 直接重置为pending，不检查延迟
        cursor.execute('''
            UPDATE analysis_tasks
            SET status = 'pending',
                error = NULL,
                started_at = NULL
            WHERE task_id = ?
        ''', (task_id,))
        print(f"  🔄 强制重试任务 {task_id}（第 {retry_count + 1} 次）")
    
    conn.commit()
    conn.close()
    
    print(f"✅ 强制重置 {len(failed_tasks)} 个失败任务为pending状态")
    return len(failed_tasks)


def clear_completed_tasks(days: int = 7):
    """清理已完成任务（默认保留7天）"""
    conn = get_queue_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        DELETE FROM analysis_tasks
        WHERE status = 'completed'
        AND completed_at < datetime('now', '-' || ? || ' days')
    ''', (days,))
    
    count = cursor.rowcount
    conn.commit()
    conn.close()
    
    return count


# ========== 便捷函数 ==========

def submit_sessions_batch(sessions: List[Dict], batch_id: str = '') -> List[int]:
    """批量提交会话到队列
    
    Args:
        sessions: 会话列表
        batch_id: 批次ID
        
    Returns:
        任务ID列表
    """
    task_ids = []
    for session in sessions:
        task_id = submit_task(session['session_id'], session, batch_id)
        task_ids.append(task_id)
    return task_ids


def get_task_detail(task_id: int) -> Optional[Dict]:
    """获取任务详情
    
    Args:
        task_id: 任务ID
        
    Returns:
        任务详情字典
    """
    conn = get_queue_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT task_id, session_id, status, result, error, 
               created_at, started_at, completed_at, retry_count
        FROM analysis_tasks
        WHERE task_id = ?
    ''', (task_id,))
    
    row = cursor.fetchone()
    conn.close()
    
    if row:
        return {
            'task_id': row[0],
            'session_id': row[1],
            'status': row[2],
            'result': json.loads(row[3]) if row[3] else None,
            'error': row[4],
            'created_at': row[5],
            'started_at': row[6],
            'completed_at': row[7],
            'retry_count': row[8]
        }
    return None


def get_pending_tasks(limit: int = 10) -> List[Dict]:
    """批量获取待处理任务
    
    Args:
        limit: 最大获取数量
        
    Returns:
        任务字典列表
    """
    conn = get_queue_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT task_id, session_id, session_data, retry_count
        FROM analysis_tasks
        WHERE status = 'pending'
        ORDER BY created_at ASC
        LIMIT ?
    ''', (limit,))
    
    rows = cursor.fetchall()
    conn.close()
    
    tasks = []
    for row in rows:
        task_id, session_id, session_data, retry_count = row
        tasks.append({
            'task_id': task_id,
            'session_id': session_id,
            'session_data': json.loads(session_data),
            'retry_count': retry_count
        })
    
    return tasks


def mark_processing(task_id: int):
    """标记任务为处理中状态
    
    Args:
        task_id: 任务ID
    """
    conn = get_queue_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        UPDATE analysis_tasks
        SET status = 'processing', started_at = ?
        WHERE task_id = ?
    ''', (datetime.now().isoformat(), task_id))
    
    conn.commit()
    conn.close()


if __name__ == '__main__':
    # 测试
    init_queue_tables()
    stats = get_queue_stats()
    print(f"队列统计: {stats}")
