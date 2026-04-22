"""数据库操作模块 - 处理分析结果的保存和一致性检查

包含功能：
- save_to_database: 保存分析结果到数据库
- _save_result_sync: 同步保存结果（带事务一致性检查）
- _log_inconsistency: 记录数据不一致日志

Usage:
    from db_operations import save_to_database, _save_result_sync
    save_to_database(session_id, session_data, intent, result)
"""

import datetime
import json
import os
from typing import Dict

from task_queue import complete_task, fail_task


def save_to_database(session_id: str, session_data: dict, intent, result: dict, session_count: int = 1):
    """保存分析结果到数据库
    
    【Bug修复】使用 try/finally 确保连接始终关闭，避免连接泄漏
    """
    from db_utils import get_connection
    conn = get_connection()
    try:
        cursor = conn.cursor()
        
        ds = result.get('dimension_scores', {})
        prof = ds.get('professionalism', {}).get('score', 0)
        std = ds.get('standardization', {}).get('score', 0)
        pol = ds.get('policy_execution', {}).get('score', 0)
        conv = ds.get('conversion', {}).get('score', 0)
        total = prof + std + pol + conv
        
        staff_name = ''
        for m in session_data.get('messages', []):
            if m.get('role') == 'staff':
                staff_name = m.get('sender', '')
                break
        
        messages = session_data.get('messages', [])
        summary = result.get('summary', {})
        strengths = summary.get('strengths', [])
        issues = summary.get('issues', [])
        suggestions = summary.get('suggestions', [])
        
        start_time = session_data.get('start_time', '')
        end_time = session_data.get('end_time', '')
        
        if not start_time and messages:
            start_time = messages[0].get('timestamp', '')
        if not end_time and messages:
            end_time = messages[-1].get('timestamp', '')
        
        is_transfer = session_data.get('is_transfer', False)
        transfer_from = session_data.get('transfer_from')
        transfer_to = session_data.get('transfer_to')
        transfer_reason = session_data.get('transfer_reason', '')
        related_sessions = session_data.get('related_sessions', [])

        cursor.execute('SELECT session_id FROM sessions WHERE session_id = ?', (session_id,))
        if cursor.fetchone():
            cursor.execute('''
                UPDATE sessions SET
                    professionalism_score = ?, standardization_score = ?,
                    policy_execution_score = ?, conversion_score = ?, total_score = ?,
                    analysis_json = ?, strengths = ?, issues = ?, suggestions = ?,
                    session_count = ?, start_time = ?, end_time = ?, created_at = ?,
                    is_transfer = ?, transfer_from = ?, transfer_to = ?, transfer_reason = ?, related_sessions = ?
                WHERE session_id = ?
            ''', (prof, std, pol, conv, total,
                  json.dumps(result, ensure_ascii=False),
                  json.dumps(strengths, ensure_ascii=False),
                  json.dumps(issues, ensure_ascii=False),
                  json.dumps(suggestions, ensure_ascii=False),
                  session_count, start_time, end_time, datetime.datetime.now().isoformat(),
                  1 if is_transfer else 0, transfer_from, transfer_to, transfer_reason,
                  json.dumps(related_sessions, ensure_ascii=False), session_id))
        else:
            cursor.execute('''
                INSERT INTO sessions
                (session_id, user_id, staff_name, messages, summary,
                 professionalism_score, standardization_score, policy_execution_score, conversion_score,
                 total_score, analysis_json, strengths, issues, suggestions, session_count, start_time, end_time, created_at,
                 is_transfer, transfer_from, transfer_to, transfer_reason, related_sessions)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                session_id,
                next((m.get('sender') for m in messages if m.get('role') in ('user', 'customer')), 'unknown'),
                staff_name,
                json.dumps(messages, ensure_ascii=False),
                result.get('session_analysis', {}).get('theme', ''),
                prof, std, pol, conv, total,
                json.dumps(result, ensure_ascii=False),
                json.dumps(strengths, ensure_ascii=False),
                json.dumps(issues, ensure_ascii=False),
                json.dumps(suggestions, ensure_ascii=False),
                session_count, start_time, end_time, datetime.datetime.now().isoformat(),
                1 if is_transfer else 0, transfer_from, transfer_to, transfer_reason,
                json.dumps(related_sessions, ensure_ascii=False)
            ))
        
        conn.commit()
    finally:
        conn.close()


def _save_result_sync(task: Dict, result: Dict):
    """同步保存结果（带事务一致性检查）"""
    import logging
    logger = logging.getLogger(__name__)
    
    task_id = task.get('task_id', 'unknown')
    session_id = task['session_id']
    session_data = task['session_data']
    
    try:
        # 构造意图对象（保留原有逻辑）
        intent_data = result.get('_metadata', {}).get('pre_analysis', {})
        class MockIntent:
            pass
        intent = MockIntent()
        for k, v in intent_data.items():
            setattr(intent, k, v)
        
        # 1. 先保存分析结果
        save_to_database(session_id, session_data, intent, result, 
                        session_data.get('session_count', 1))
        
        # 2. 成功后更新任务状态
        complete_task(task_id, result)
        
        logger.info(f"✅ 任务 {task_id} 结果保存成功")
        
    except Exception as e:
        error_msg = str(e)
        logger.error(f"❌ 任务 {task_id} 保存失败: {error_msg}")
        
        # 【P1-3修复】检查是否部分成功（结果已保存但任务状态未更新）
        try:
            from db_utils import get_connection
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
                _log_inconsistency(session_id, task_id, error_msg, "result_saved_task_failed")
            
        except Exception as check_error:
            logger.error(f"无法检查数据一致性: {check_error}")
        
        # 重新抛出异常，让上层处理
        raise


def _log_inconsistency(session_id: str, task_id: str, error: str, inconsistency_type: str):
    """记录数据不一致到日志文件"""
    timestamp = datetime.datetime.now().isoformat()
    log_entry = f"[{timestamp}] {inconsistency_type} | session_id={session_id} | task_id={task_id} | error={error}\n"
    
    log_file = os.path.join(os.path.dirname(__file__), 'data', 'inconsistency.log')
    try:
        with open(log_file, "a") as f:
            f.write(log_entry)
    except Exception as e:
        print(f"⚠️ 无法写入不一致日志: {e}")
