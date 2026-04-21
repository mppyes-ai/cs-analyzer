"""任务获取模块 - 从队列中获取并分组任务

包含功能：
- fetch_and_group_tasks: 智能获取待处理任务并按 user_id 分组
- _fetch_failed_tasks_for_retry: 获取可重试的失败任务

Usage:
    from task_fetcher import fetch_and_group_tasks, _fetch_failed_tasks_for_retry
    groups = fetch_and_group_tasks(max_batch_size=150, once=False)
    failed_groups = _fetch_failed_tasks_for_retry(max_retries=3)
"""

import json
import sqlite3
from collections import defaultdict
from typing import Dict, List

import worker_config as cfg
from task_queue import get_queue_connection, fail_task, cancel_task, QUEUE_DB_PATH
from session_merge import deduplicate_sessions


def fetch_and_group_tasks(max_batch_size: int = 150, once: bool = False) -> Dict[str, List[Dict]]:
    """【v2.6.2】智能获取待处理任务并按 user_id 分组
    
    优化点：
    - 先count队列中pending任务总数
    - 如果总数 <= max_batch_size，一次性全取
    - 如果总数 > max_batch_size，取max_batch_size个
    - 【v2.6.3修复】--once模式下取全部任务，不受max_batch_size限制
    - 实现"看人数打饭"策略，避免多次轮询
    
    Args:
        max_batch_size: 单次处理上限（默认150），防止内存溢出
        once: 是否为--once模式（True时取全部任务）
    """
    import pandas as pd
    
    conn = get_queue_connection()
    cursor = conn.cursor()
    
    # 【v2.6.2】智能感知：先count pending任务总数
    cursor.execute("SELECT COUNT(*) FROM analysis_tasks WHERE status = 'pending'")
    total_pending = cursor.fetchone()[0]
    
    # 【v2.6.3修复】--once模式下取全部任务，不受max_batch_size限制
    if once:
        limit = total_pending
        print(f"   📊 --once模式：队列共有 {total_pending} 个任务，全部取出处理")
    elif total_pending <= max_batch_size:
        limit = total_pending
        print(f"   📊 队列共有 {total_pending} 个任务，全部取出处理")
    else:
        limit = max_batch_size
        print(f"   📊 队列共有 {total_pending} 个任务，本次处理前 {limit} 个")
    
    df = pd.read_sql_query("""
        SELECT * FROM analysis_tasks 
        WHERE status = 'pending'
        ORDER BY created_at ASC
        LIMIT ?
    """, conn, params=(limit,))
    
    if df.empty:
        conn.close()
        return {}
    
    task_ids = df['task_id'].tolist()
    if task_ids:
        placeholders = ','.join(['?' for _ in task_ids])
        cursor.execute(f"""
            UPDATE analysis_tasks 
            SET status = 'processing', started_at = datetime('now')
            WHERE task_id IN ({placeholders})
        """, task_ids)
        conn.commit()
    conn.close()
    
    groups = defaultdict(list)
    for _, task in df.iterrows():
        try:
            session_data = json.loads(task['session_data']) if task['session_data'] else {}
        except Exception:
            session_data = {}
        
        user_id = session_data.get('user_id', 'unknown')
        groups[user_id].append(task.to_dict())
    
    # 【Opus修复-P0】跨批次去重：同一用户可能有重复session_id
    for user_id in groups:
        groups[user_id] = deduplicate_sessions(groups[user_id])
    
    return dict(groups)


def _fetch_failed_tasks_for_retry(max_retries: int = 3) -> Dict[str, List[Dict]]:
    """【Opus修复】直接获取可重试的 failed 任务（不走 pending 中转）
    
    避免 --once 模式下失败任务需要重置为 pending 再重新 fetch 的两阶段断层
    
    Args:
        max_retries: 最大重试次数
        
    Returns:
        按 user_id 分组的失败任务字典
    """
    conn = get_queue_connection()
    cursor = conn.cursor()
    
    # 【Opus修复】直接查询 failed 任务，不等待延迟时间
    cursor.execute("""
        SELECT task_id, session_id, session_data, retry_count
        FROM analysis_tasks
        WHERE status = 'failed' AND retry_count < ?
        ORDER BY retry_count ASC, created_at ASC
    """, (max_retries,))
    
    rows = cursor.fetchall()
    
    if not rows:
        conn.close()
        return {}
    
    # 【Opus修复】直接标记为 processing（不在 pending 中中转）
    task_ids = [row[0] for row in rows]
    placeholders = ','.join(['?' for _ in task_ids])
    cursor.execute(f"""
        UPDATE analysis_tasks
        SET status = 'processing', started_at = datetime('now')
        WHERE task_id IN ({placeholders})
    """, task_ids)
    conn.commit()
    conn.close()
    
    # 按 user_id 分组
    groups = defaultdict(list)
    for task_id, session_id, session_data_json, retry_count in rows:
        try:
            session_data = json.loads(session_data_json) if session_data_json else {}
        except Exception:
            session_data = {}
        user_id = session_data.get('user_id', 'unknown')
        groups[user_id].append({
            'task_id': task_id,
            'session_id': session_id,
            'session_data': session_data,
            'retry_count': retry_count
        })
    
    return dict(groups)
