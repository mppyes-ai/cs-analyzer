"""会话合并模块 - 处理客服会话的关联、合并与去重

包含功能：
- parse_timestamp: 时间字符串解析
- has_transfer_keyword: 转接关键词检测
- find_related_sessions: 查找关联会话（同用户、同客服、时间窗口内）
- merge_session_data: 合并多个会话数据
- deduplicate_sessions: 会话去重（保留最早任务）

Usage:
    from session_merge import find_related_sessions, merge_session_data, deduplicate_sessions
"""

import json
from datetime import datetime
from typing import Dict, List

from task_queue import get_queue_connection, cancel_task


def parse_timestamp(ts_str):
    """解析时间字符串"""
    if not ts_str:
        return None
    try:
        formats = [
            '%Y-%m-%d %H:%M:%S',
            '%Y-%m-%d %H:%M',
            '%Y-%m-%dT%H:%M:%S',
            '%Y-%m-%dT%H:%M:%S.%f',
        ]
        for fmt in formats:
            try:
                return datetime.strptime(str(ts_str)[:19], fmt)
            except Exception:
                continue
        return None
    except Exception:
        return None


def has_transfer_keyword(session_data: dict) -> bool:
    """检测会话中是否包含转接关键词"""
    TRANSFER_KEYWORDS = [
        "转接售后", "为您转接", "转给售后", "售后专员",
        "安排售后", "售后同事", "转接售前", "升级处理",
        "主管处理", "经理处理", "专家坐席"
    ]
    messages = session_data.get('messages', [])
    content = ' '.join([m.get('content', '') for m in messages])
    return any(kw in content for kw in TRANSFER_KEYWORDS)


def find_related_sessions(main_task: dict, window_minutes: int) -> dict:
    """查找关联会话
    
    在指定时间窗口内查找与主任务同用户的关联会话。
    分为三类：
    - mergeable: 同客服，可合并
    - transfer_chain: 不同客服但可能转接
    - same_user: 不同客服，同用户但非转接
    """
    main_data = main_task.get("session_data", {})
    if isinstance(main_data, str):
        main_data = json.loads(main_data)
    main_user_id = main_data.get('user_id', '')
    main_staff = main_data.get('staff_name', '')
    main_messages = main_data.get('messages', [])
    
    # 【修复】user_id为空时，不进行合并搜索
    if not main_user_id:
        return {'mergeable': [], 'transfer_chain': [], 'same_user': []}
    
    # 【修复】主任务必须有用户消息才合并
    main_user_msgs = [m for m in main_messages if m.get('role') in ('user', 'customer')]
    if not main_user_msgs:
        return {'mergeable': [], 'transfer_chain': [], 'same_user': []}
    
    if not main_messages:
        return {'mergeable': [], 'transfer_chain': [], 'same_user': []}
    
    main_start = parse_timestamp(main_messages[0].get('timestamp', ''))
    main_end = parse_timestamp(main_messages[-1].get('timestamp', ''))
    
    if not main_start or not main_end:
        return {'mergeable': [], 'transfer_chain': [], 'same_user': []}
    
    conn = get_queue_connection()
    cursor = conn.cursor()
    
    # 【优化】使用user_id索引精准查找，O(N²)→O(N)，仅需匹配同用户任务
    cursor.execute('''
        SELECT task_id, session_id, session_data 
        FROM analysis_tasks 
        WHERE user_id = ? AND status IN ('pending', 'processing') AND task_id != ?
    ''', (main_user_id, main_task.get('task_id')))
    
    result = {'mergeable': [], 'transfer_chain': [], 'same_user': []}

    for row in cursor.fetchall():
        task_id, session_id, session_data_json = row
        try:
            task_data = json.loads(session_data_json)
            task_staff = task_data.get('staff_name', '')
            task_messages = task_data.get('messages', [])
            
            if not task_messages:
                continue
            
            # 【优化】跳过无用户消息的候选（已在submit_task过滤，保留防护）
            task_user_msgs = [m for m in task_messages if m.get('role') in ('user', 'customer')]
            if not task_user_msgs:
                continue

            task_start = parse_timestamp(task_messages[0].get('timestamp', ''))
            task_end = parse_timestamp(task_messages[-1].get('timestamp', ''))

            if not task_start or not task_end:
                continue

            gap_before = (main_start - task_end).total_seconds() / 60
            gap_after = (task_start - main_end).total_seconds() / 60

            if not (0 <= gap_before <= window_minutes or 0 <= gap_after <= window_minutes):
                continue

            task_info = {
                'task_id': task_id,
                'session_id': session_id,
                'session_data': task_data,
                'start_time': task_start,
                'end_time': task_end,
                'gap_minutes': min(abs(gap_before), abs(gap_after))
            }

            if task_staff == main_staff:
                result['mergeable'].append(task_info)
            else:
                is_transfer = (task_info['gap_minutes'] < 2 or
                              has_transfer_keyword(task_data) or
                              has_transfer_keyword(main_data))
                if is_transfer:
                    result['transfer_chain'].append(task_info)
                else:
                    result['same_user'].append(task_info)

        except Exception as e:
            print(f"   ⚠️ 解析任务 {task_id} 失败: {e}")
            continue

    conn.close()
    return result


def merge_session_data(main_task: dict, mergeable_tasks: list) -> dict:
    """合并会话数据
    
    将主任务和可合并任务的会话数据合并为一个新的会话。
    按时间排序所有消息，生成新的时间范围。
    """
    main_data = main_task.get("session_data", {})
    if isinstance(main_data, str):
        main_data = json.loads(main_data)
    main_messages = main_data.get('messages', [])
    
    all_messages = main_messages.copy()
    merged_session_ids = [main_task.get('session_id')]
    
    for task in mergeable_tasks:
        all_messages.extend(task['session_data'].get('messages', []))
        merged_session_ids.append(task['session_id'])
    
    all_messages.sort(key=lambda x: parse_timestamp(x.get('timestamp', '')) or datetime.min)
    
    timestamps = [parse_timestamp(m.get('timestamp', '')) for m in all_messages]
    valid_timestamps = [t for t in timestamps if t]
    
    new_start = min(valid_timestamps).isoformat()[:19] if valid_timestamps else ''
    new_end = max(valid_timestamps).isoformat()[:19] if valid_timestamps else ''
    
    return {
        'session_id': main_task.get('session_id'),
        'user_id': main_data.get('user_id'),
        'staff_name': main_data.get('staff_name'),
        'messages': all_messages,
        'session_count': len(merged_session_ids),
        'merged_session_ids': merged_session_ids,
        'start_time': new_start,
        'end_time': new_end,
        'is_merged': len(merged_session_ids) > 1
    }


def deduplicate_sessions(tasks: List[Dict]) -> List[Dict]:
    """【Opus修复-P0】在送入模型前去重，保留最早的任务
    
    消除批次77的重复会话问题（任务164/172同session_id）
    
    Args:
        tasks: 任务列表
        
    Returns:
        去重后的任务列表
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
        print(f"   ✅ 去重完成: {len(tasks)} → {len(unique_tasks)} 个任务 (去除 {len(tasks) - len(unique_tasks)} 个重复)")
    
    return unique_tasks