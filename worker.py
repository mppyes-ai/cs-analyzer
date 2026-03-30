#!/usr/bin/env python3
"""异步分析工作进程 - v2.1（支持串行/并行/预分组并行切换）

后台运行，从队列中获取任务并处理
支持三种模式：
  - 串行模式（--serial）：支持会话合并，顺序处理
  - 并行模式（--parallel）：速度快，不支持会话合并
  - 预分组并行模式（--grouped）：组间并行+组内串行，支持会话合并（推荐）

用法:
    python3 worker.py                    # 默认模式（预分组并行）
    python3 worker.py --serial           # 串行模式
    python3 worker.py --parallel         # 纯并行模式
    python3 worker.py --grouped          # 预分组并行模式（显式指定）
    python3 worker.py --grouped --max-groups 6 --batch-size 30
    python3 worker.py --daemon           # 后台运行
    python3 worker.py --once             # 处理完当前队列后退出
    python3 worker.py --window 30        # 设置合并窗口（分钟）

作者: 小虾米
更新: 2026-03-23（新增预分组并行模式）
"""

import os
from dotenv import load_dotenv
load_dotenv()
import sys
import time
import argparse
import signal
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from collections import defaultdict

sys.path.insert(0, os.path.dirname(__file__))

from task_queue import (
    init_queue_tables, get_pending_task, get_pending_tasks, complete_task, fail_task,
    get_queue_stats, retry_failed_tasks, cancel_task, mark_processing, QUEUE_DB_PATH
)
from intent_classifier_v3 import RobustIntentClassifier
from smart_scoring_v2 import SmartScoringEngine
import sqlite3
import json

# 全局变量
running = True
classifier = None
scorer = None
MERGE_WINDOW_MINUTES = 30  # 默认合并窗口
MAX_WORKERS = 3  # 并行模式默认并发数

# 线程锁（数据库操作）
db_lock = threading.Lock()


def signal_handler(sig, frame):
    """处理退出信号"""
    global running
    print("\n⚠️ 收到退出信号，正在保存当前任务...")
    running = False


def init_engines():
    """初始化分析引擎"""
    global classifier, scorer
    
    print("🔄 初始化分析引擎...")
    
    # 获取API Key
    api_key = os.getenv('MOONSHOT_API_KEY')
    if not api_key:
        # 尝试从配置文件读取
        config_path = os.path.expanduser('~/.openclaw/config.yaml')
        if os.path.exists(config_path):
            import yaml
            with open(config_path) as f:
                config = yaml.safe_load(f)
                api_key = config.get('moonshot', {}).get('apiKey')
    
    if not api_key:
        raise ValueError("未找到MOONSHOT_API_KEY，请设置环境变量或在配置文件中配置")
    
    classifier = RobustIntentClassifier()
    scorer = SmartScoringEngine(api_key=api_key)
    
    print("✅ 引擎初始化完成")


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

    for keyword in TRANSFER_KEYWORDS:
        if keyword in content:
            return True
    return False


def find_related_sessions(main_task: dict, window_minutes: int = MERGE_WINDOW_MINUTES) -> dict:
    """
    查找关联会话（改造版）

    分类返回：
    - mergeable: 同客服可合并任务
    - transfer_chain: 不同客服但转接相关任务
    - same_user: 同用户但无关联任务

    Args:
        main_task: 主任务
        window_minutes: 合并窗口（分钟）

    Returns:
        {
            'mergeable': [],      # 同客服可合并
            'transfer_chain': [], # 不同客服但可能是转接
            'same_user': []       # 同用户但无关联
        }
    """
    main_data = main_task.get("session_data", {})
    if isinstance(main_data, str):
        import json
        main_data = json.loads(main_data)
    main_user_id = main_data.get('user_id', '')
    main_staff = main_data.get('staff_name', '')
    main_messages = main_data.get('messages', [])
    
    if not main_messages:
        return {'mergeable': [], 'transfer_chain': [], 'same_user': []}
    
    # 获取主任务的时间范围
    main_start = parse_timestamp(main_messages[0].get('timestamp', ''))
    main_end = parse_timestamp(main_messages[-1].get('timestamp', ''))
    
    if not main_start or not main_end:
        return {'mergeable': [], 'transfer_chain': [], 'same_user': []}
    
    # 查询数据库中的其他待处理任务（从task_queue.db）
    db_path = os.path.join(os.path.dirname(__file__), 'data', 'task_queue.db')
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # 获取所有待处理任务（从 task_queue 表）
    cursor.execute('''
        SELECT task_id, session_id, session_data 
        FROM analysis_tasks 
        WHERE status = 'pending' AND task_id != ?
    ''', (main_task.get('task_id'),))
    
    result = {
        'mergeable': [],      # 同客服可合并
        'transfer_chain': [], # 不同客服但可能是转接
        'same_user': []       # 同用户但无关联
    }

    for row in cursor.fetchall():
        task_id, session_id, session_data_json = row
        try:
            task_data = json.loads(session_data_json)
            task_user_id = task_data.get('user_id', '')
            task_staff = task_data.get('staff_name', '')
            task_messages = task_data.get('messages', [])

            # 只处理同一用户
            if task_user_id != main_user_id:
                continue
            if not task_messages:
                continue

            # 检查时间间隔
            task_start = parse_timestamp(task_messages[0].get('timestamp', ''))
            task_end = parse_timestamp(task_messages[-1].get('timestamp', ''))

            if not task_start or not task_end:
                continue

            # 检查时间是否重叠或在窗口内
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

            # 分类：同一客服 vs 不同客服
            if task_staff == main_staff:
                result['mergeable'].append(task_info)
            else:
                # 不同客服，检查是否是转接场景
                # 条件1：时间间隔极短（<2分钟）
                # 条件2：检测到转接关键词
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
    """
    合并会话数据
    
    Args:
        main_task: 主任务
        mergeable_tasks: 可合并的任务列表
        
    Returns:
        合并后的会话数据
    """
    main_data = main_task.get("session_data", {})
    if isinstance(main_data, str):
        import json
        main_data = json.loads(main_data)
    main_messages = main_data.get('messages', [])
    
    # 收集所有消息
    all_messages = main_messages.copy()
    merged_session_ids = [main_task.get('session_id')]
    
    for task in mergeable_tasks:
        all_messages.extend(task['session_data'].get('messages', []))
        merged_session_ids.append(task['session_id'])
    
    # 按时间排序
    all_messages.sort(key=lambda x: parse_timestamp(x.get('timestamp', '')) or datetime.min)
    
    # 确定新的时间范围
    timestamps = [parse_timestamp(m.get('timestamp', '')) for m in all_messages]
    valid_timestamps = [t for t in timestamps if t]
    
    new_start = min(valid_timestamps).isoformat()[:19] if valid_timestamps else ''
    new_end = max(valid_timestamps).isoformat()[:19] if valid_timestamps else ''
    
    # 构建合并后的会话数据
    merged_data = {
        'session_id': main_task.get('session_id'),  # 保留主会话ID
        'user_id': main_data.get('user_id'),
        'staff_name': main_data.get('staff_name'),
        'messages': all_messages,
        'session_count': len(merged_session_ids),  # 合并的会话数
        'merged_session_ids': merged_session_ids,  # 记录合并了哪些会话
        'start_time': new_start,
        'end_time': new_end,
        'is_merged': len(merged_session_ids) > 1
    }
    
    return merged_data


def process_transfer_chain(main_task: dict, chain_tasks: list) -> dict:
    """
    处理转接链关系

    不合并消息，只建立关系标记
    """
    main_data = main_task.get("session_data", {})
    if isinstance(main_data, str):
        import json
        main_data = json.loads(main_data)
    main_session_id = main_task.get('session_id')
    main_user_id = main_data.get('user_id', '')
    main_staff = main_data.get('staff_name', '')

    # 收集所有相关会话
    all_sessions = [{
        'session_id': main_session_id,
        'staff_name': main_staff,
        'start_time': main_data.get('messages', [{}])[0].get('timestamp', ''),
        'end_time': main_data.get('messages', [{}])[-1].get('timestamp', '')
    }]

    for task in chain_tasks:
        task_data = task['session_data']
        all_sessions.append({
            'session_id': task['session_id'],
            'staff_name': task_data.get('staff_name', ''),
            'start_time': task_data.get('messages', [{}])[0].get('timestamp', ''),
            'end_time': task_data.get('messages', [{}])[-1].get('timestamp', '')
        })

    # 按时间排序
    all_sessions.sort(key=lambda x: x['start_time'] or '9999')

    # 找到主会话在链中的位置
    main_position = next((i for i, s in enumerate(all_sessions)
                         if s['session_id'] == main_session_id), 0)

    # 构建链信息
    chain_info = {
        'chain_id': f"chain_{main_user_id}_{datetime.now().strftime('%Y%m%d')}",
        'position': main_position,
        'total_in_chain': len(all_sessions),
        'prev_session': all_sessions[main_position - 1]['session_id'] if main_position > 0 else None,
        'next_session': all_sessions[main_position + 1]['session_id'] if main_position < len(all_sessions) - 1 else None
    }

    # 确定转接方向
    transfer_from = chain_info['prev_session']
    transfer_to = chain_info['next_session']

    # 标记关联的所有会话ID
    related_ids = [s['session_id'] for s in all_sessions if s['session_id'] != main_session_id]

    # 确定转接原因（从关键词检测）
    transfer_reason = ''
    if has_transfer_keyword(main_data):
        transfer_reason = '售前转售后'  # 可细化检测

    # 构建标记数据
    transfer_data = {
        'is_transfer': True,
        'transfer_from': transfer_from,
        'transfer_to': transfer_to,
        'transfer_reason': transfer_reason,
        'related_sessions': related_ids,
        'session_chain_info': chain_info,
        'session_count': 1,  # 转接链模式不合并，保持1
        'is_merged': False
    }

    # 更新主任务数据
    main_data.update(transfer_data)

    return main_data


def process_task(task: dict, window_minutes: int = MERGE_WINDOW_MINUTES) -> bool:
    """处理单个任务（支持会话合并）
    
    Args:
        task: 任务字典
        window_minutes: 合并窗口（分钟）
        
    Returns:
        是否成功
    """
    task_id = task['task_id']
    session_id = task['session_id']
    
    print(f"\n📋 处理任务 #{task_id}: {session_id}")
    start_time = time.time()
    
    try:
        # ========== 步骤1: 查找关联会话 ==========
        print("   🔍 查找关联会话...")
        related = find_related_sessions(task, window_minutes)

        mergeable_tasks = related['mergeable']
        transfer_chain_tasks = related['transfer_chain']

        # 准备会话数据
        if mergeable_tasks:
            # 【合并模式】同一客服，合并消息
            print(f"   📦 发现 {len(mergeable_tasks)} 个可合并会话（同客服）")
            merged_data = merge_session_data(task, mergeable_tasks)
            session_data = merged_data

            # 取消被合并的任务
            for t in mergeable_tasks:
                cancel_task(t['task_id'])
                print(f"   🔄 任务 #{t['task_id']} 已合并到当前任务")

            is_merged = True
            session_count = len(merged_data.get('merged_session_ids', [1]))
            is_transfer = False

        elif transfer_chain_tasks:
            # 【转接链模式】不同客服，建立关联但不合并
            print(f"   🔀 发现 {len(transfer_chain_tasks)} 个转接相关会话（跨客服）")
            session_data = process_transfer_chain(task, transfer_chain_tasks)
            # 不取消转接链任务，它们会独立分析

            is_merged = False
            session_count = 1
            is_transfer = True

        else:
            # 【独立模式】无关联
            session_data = task['session_data']
            if isinstance(session_data, str):
                import json
                session_data = json.loads(session_data)
            is_merged = False
            session_count = 1
            is_transfer = False
        
        # ========== 步骤2: 意图分类 ==========
        intent = classifier.classify(session_data['messages'])
        
        # ========== 步骤3: 智能评分（一次性分析完整会话） ==========
        result = scorer.score_session(session_data)
        
        if result and 'dimension_scores' in result:
            ds = result['dimension_scores']
            prof = ds.get('professionalism', {}).get('score', 0)
            std = ds.get('standardization', {}).get('score', 0)
            pol = ds.get('policy_execution', {}).get('score', 0)
            conv = ds.get('conversion', {}).get('score', 0)
            total = prof + std + pol + conv
            
            merge_info = f" [合并{session_count}个]" if is_merged else ""
            print(f"   评分{merge_info}: 专业{prof} 标准{std} 政策{pol} 转化{conv} = {total}/20")
            
            # 将意图分类来源添加到 result 中（用于后续分析）
            result['_metadata'] = result.get('_metadata', {})
            result['_metadata']['intent_classification'] = {
                'source': getattr(intent, 'source', 'unknown'),
                'scene': intent.scene,
                'sub_scene': intent.sub_scene,
                'intent': intent.intent,
                'sentiment': intent.sentiment,
                'confidence': getattr(intent, 'confidence', 0),
                'latency_ms': getattr(intent, 'latency_ms', 0),
                'is_complaint': getattr(intent, 'is_complaint', False),
                'complaint_type': getattr(intent, 'complaint_type', '')
            }
            
            # ========== 步骤4: 保存到分析数据库 ==========
            save_to_database(session_id, session_data, intent, result, session_count)
            
            # ========== 步骤5: 标记任务完成 ==========
            # 记录意图分类来源（用于五层漏斗架构测试评估）
            intent_source = getattr(intent, 'source', 'unknown')
            # 统一显示为 qwen2.5:7b
            if intent_source == 'qwen2.5':
                display_source = 'qwen2.5:7b'
            else:
                display_source = intent_source
            intent_latency = getattr(intent, 'latency_ms', 0)
            print(f"   📊 意图来源: {display_source} ({intent_latency:.0f}ms)")
            
            complete_task(task_id, {
                'intent': {
                    'scene': intent.scene,
                    'sub_scene': intent.sub_scene,
                    'sentiment': intent.sentiment,
                    'source': intent_source,
                    'latency_ms': intent_latency,
                    'confidence': getattr(intent, 'confidence', 0),
                    'is_complaint': getattr(intent, 'is_complaint', False)
                },
                'scores': {
                    'professionalism': prof,
                    'standardization': std,
                    'policy_execution': pol,
                    'conversion': conv,
                    'total': total
                },
                'processing_time': time.time() - start_time,
                'is_merged': is_merged,
                'session_count': session_count
            })
            
            print(f"   ✅ 完成 (耗时: {time.time()-start_time:.1f}s)")
            return True
        else:
            raise ValueError("评分结果异常")
            
    except Exception as e:
        error_msg = str(e)
        print(f"   ❌ 失败: {error_msg}")
        fail_task(task_id, error_msg)
        return False


def save_to_database(session_id: str, session_data: dict, intent, result: dict, session_count: int = 1):
    """保存分析结果到数据库"""
    
    # 连接分析数据库
    db_path = os.path.join(os.path.dirname(__file__), 'data', 'cs_analyzer_new.db')
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # 提取分数
    ds = result.get('dimension_scores', {})
    prof = ds.get('professionalism', {}).get('score', 0)
    std = ds.get('standardization', {}).get('score', 0)
    pol = ds.get('policy_execution', {}).get('score', 0)
    conv = ds.get('conversion', {}).get('score', 0)
    total = prof + std + pol + conv
    
    # 获取客服名称
    staff_name = ''
    for m in session_data.get('messages', []):
        if m.get('role') == 'staff':
            staff_name = m.get('sender', '')
            break
    
    messages = session_data.get('messages', [])
    
    # 提取独立字段数据（从 summary 部分）
    summary = result.get('summary', {})
    strengths = summary.get('strengths', [])
    issues = summary.get('issues', [])
    suggestions = summary.get('suggestions', [])
    
    # 提取时间（从 messages 或 session_data）
    start_time = session_data.get('start_time', '')
    end_time = session_data.get('end_time', '')
    
    # 如果没有，从 messages 提取
    if not start_time and messages:
        start_time = messages[0].get('timestamp', '')
    if not end_time and messages:
        end_time = messages[-1].get('timestamp', '')
    
    # 提取转接相关字段
    is_transfer = session_data.get('is_transfer', False)
    transfer_from = session_data.get('transfer_from')
    transfer_to = session_data.get('transfer_to')
    transfer_reason = session_data.get('transfer_reason', '')
    related_sessions = session_data.get('related_sessions', [])
    session_chain_info = session_data.get('session_chain_info', {})

    # 检查是否已存在
    cursor.execute('SELECT session_id FROM sessions WHERE session_id = ?', (session_id,))
    if cursor.fetchone():
        # 更新
        cursor.execute('''
            UPDATE sessions SET
                professionalism_score = ?,
                standardization_score = ?,
                policy_execution_score = ?,
                conversion_score = ?,
                total_score = ?,
                analysis_json = ?,
                strengths = ?,
                issues = ?,
                suggestions = ?,
                session_count = ?,
                start_time = ?,
                end_time = ?,
                created_at = ?,
                is_transfer = ?,
                transfer_from = ?,
                transfer_to = ?,
                transfer_reason = ?,
                related_sessions = ?
            WHERE session_id = ?
        ''', (prof, std, pol, conv, total,
              json.dumps(result, ensure_ascii=False),
              json.dumps(strengths, ensure_ascii=False),
              json.dumps(issues, ensure_ascii=False),
              json.dumps(suggestions, ensure_ascii=False),
              session_count,
              start_time,
              end_time,
              datetime.now().isoformat(),
              1 if is_transfer else 0,
              transfer_from,
              transfer_to,
              transfer_reason,
              json.dumps(related_sessions, ensure_ascii=False),
              session_id))
    else:
        # 插入
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
            session_count,
            start_time,
            end_time,
            datetime.now().isoformat(),
            1 if is_transfer else 0,
            transfer_from,
            transfer_to,
            transfer_reason,
            json.dumps(related_sessions, ensure_ascii=False)
        ))
    
    conn.commit()
    conn.close()


def process_single_task_parallel(task: dict) -> dict:
    """并行模式：处理单个任务（简化版，不含合并逻辑）
    
    Args:
        task: 任务字典
        
    Returns:
        {'success': True/False, 'task_id': id, 'error': msg}
    """
    task_id = task['task_id']
    session_id = task['session_id']
    
    try:
        # 标记为处理中
        with db_lock:
            mark_processing(task_id)
        
        session_data = task['session_data']
        messages = session_data.get('messages', [])
        
        # 1. 意图分类（本地模型，快速）
        intent = classifier.classify(messages)
        
        # 2. 智能评分（Kimi API，耗时）
        result = scorer.score_session(session_data)
        
        if result and 'dimension_scores' in result:
            ds = result['dimension_scores']
            prof = ds.get('professionalism', {}).get('score', 0)
            std = ds.get('standardization', {}).get('score', 0)
            pol = ds.get('policy_execution', {}).get('score', 0)
            conv = ds.get('conversion', {}).get('score', 0)
            total = prof + std + pol + conv
            
            # 3. 保存结果（线程安全）
            with db_lock:
                _save_result_parallel(session_id, session_data, intent, result, total)
                complete_task(task_id, {
                    'intent': {
                        'scene': intent.scene,
                        'sub_scene': intent.sub_scene,
                        'sentiment': intent.sentiment,
                        'source': getattr(intent, 'source', 'unknown'),
                        'latency_ms': getattr(intent, 'latency_ms', 0),
                    },
                    'scores': {
                        'professionalism': prof,
                        'standardization': std,
                        'policy_execution': pol,
                        'conversion': conv,
                        'total': total
                    }
                })
            
            print(f"   ✅ 任务#{task_id}完成: {total}/20分")
            return {'success': True, 'task_id': task_id}
        else:
            raise ValueError("评分结果异常")
            
    except Exception as e:
        error_msg = str(e)
        print(f"   ❌ 任务#{task_id}失败: {error_msg[:80]}")
        with db_lock:
            fail_task(task_id, error_msg)
        return {'success': False, 'task_id': task_id, 'error': error_msg}


def _save_result_parallel(session_id: str, session_data: dict, intent, result: dict, total: int):
    """并行模式：保存分析结果（线程安全版本）"""
    
    db_path = os.path.join(os.path.dirname(__file__), 'data', 'cs_analyzer_new.db')
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        ds = result.get('dimension_scores', {})
        prof = ds.get('professionalism', {}).get('score', 0)
        std = ds.get('standardization', {}).get('score', 0)
        pol = ds.get('policy_execution', {}).get('score', 0)
        conv = ds.get('conversion', {}).get('score', 0)
        
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
        
        cursor.execute('''
            INSERT OR REPLACE INTO sessions 
            (session_id, user_id, staff_name, messages, summary, 
             professionalism_score, standardization_score, policy_execution_score, conversion_score,
             total_score, analysis_json, strengths, issues, suggestions, session_count, start_time, end_time, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
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
            start_time,
            end_time,
            datetime.now().isoformat()
        ))
        
        conn.commit()
    finally:
        conn.close()


def run_worker(once: bool = False, interval: float = 2.0, window_minutes: int = MERGE_WINDOW_MINUTES):
    """运行工作进程
    
    Args:
        once: 是否只处理一轮就退出
        interval: 轮询间隔（秒）
        window_minutes: 会话合并窗口（分钟）
    """
    global running, MERGE_WINDOW_MINUTES
    
    MERGE_WINDOW_MINUTES = window_minutes
    
    # 注册信号处理器
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    print("=" * 60)
    print("🚀 客服分析工作进程启动 [串行模式]")
    print(f"   会话合并窗口: {window_minutes} 分钟")
    print("=" * 60)
    
    # 初始化
    init_queue_tables()
    init_engines()
    
    processed = 0
    failed = 0
    merged_count = 0
    
    print(f"\n⏳ 开始轮询任务队列（间隔: {interval}s）...")
    print("提示: 按Ctrl+C停止\n")
    
    try:
        while running:
            # 获取待处理任务
            task = get_pending_task()
            
            if task:
                # 处理任务（支持合并）
                success = process_task(task, window_minutes)
                if success:
                    processed += 1
                    # 检查是否是合并任务
                    if task.get('session_data', {}).get('is_merged'):
                        merged_count += 1
                else:
                    failed += 1
                
                # 如果只处理一轮
                if once:
                    break
                    
                # 短暂休息，避免CPU占用过高
                time.sleep(0.5)
            else:
                # 没有pending任务，尝试重试失败的
                if not once:
                    retried = retry_failed_tasks()
                    if retried > 0:
                        print(f"🔄 自动重试 {retried} 个失败任务")
                        continue  # 立即处理重试的任务
                
                # 仍然没有任务，等待
                if once:
                    print("✅ 队列已空，退出")
                    break
                    
                time.sleep(interval)
                
    except Exception as e:
        print(f"\n❌ 工作进程异常: {e}")
    finally:
        # 清理资源
        if classifier:
            classifier.close()
        
        print("\n" + "=" * 60)
        print("📊 工作进程结束")
        print(f"   成功处理: {processed}")
        print(f"   合并会话: {merged_count}")
        print(f"   失败: {failed}")
        print("=" * 60)


def run_parallel_worker(max_workers: int = MAX_WORKERS, batch_size: int = 10):
    """运行并行工作进程
    
    Args:
        max_workers: 最大并发数（默认3）
        batch_size: 每批获取任务数（默认10）
    """
    global running, MAX_WORKERS
    
    MAX_WORKERS = max_workers
    
    # 注册信号处理器
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    print("=" * 60)
    print("🚀 客服分析工作进程启动 [并行模式]")
    print(f"   并发数: {max_workers}")
    print(f"   批大小: {batch_size}")
    print("=" * 60)
    
    # 初始化
    init_queue_tables()
    init_engines()
    
    total_processed = 0
    total_failed = 0
    
    try:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            while running:
                # 获取一批pending任务
                tasks = get_pending_tasks(limit=batch_size)
                
                if not tasks:
                    # 没有任务，尝试重试失败的
                    retry_failed_tasks()
                    
                    # 仍然没有任务，等待
                    print("⏳ 队列已空，等待新任务...")
                    time.sleep(2.0)
                    continue
                
                print(f"\n📋 本批获取 {len(tasks)} 个任务，开始并行处理...")
                
                # 提交所有任务到线程池
                future_to_task = {
                    executor.submit(process_single_task_parallel, task): task 
                    for task in tasks
                }
                
                # 等待完成
                for future in as_completed(future_to_task):
                    result = future.result()
                    if result['success']:
                        total_processed += 1
                    else:
                        total_failed += 1
                
                print(f"   本批完成: {len(tasks)}个")
                
    except Exception as e:
        print(f"\n❌ 工作进程异常: {e}")
    finally:
        if classifier:
            classifier.close()
        
        print("\n" + "=" * 60)
        print("📊 并行工作进程结束")
        print(f"   成功处理: {total_processed}")
        print(f"   失败: {total_failed}")
        print("=" * 60)


def fetch_and_group_tasks(batch_size: int = 20) -> Dict[str, List[Dict]]:
    """获取待处理任务并按 user_id 分组
    
    Args:
        batch_size: 每批获取任务数
        
    Returns:
        {user_id: [task1, task2, ...]}
    """
    import pandas as pd
    
    conn = sqlite3.connect(QUEUE_DB_PATH)
    
    # 获取一批待处理任务
    df = pd.read_sql_query("""
        SELECT * FROM analysis_tasks 
        WHERE status = 'pending'
        ORDER BY created_at ASC
        LIMIT ?
    """, conn, params=(batch_size,))
    
    conn.close()
    
    if df.empty:
        return {}
    
    # 标记为 processing 状态（防止其他worker重复获取）
    task_ids = df['task_id'].tolist()
    if task_ids:
        cursor = conn.cursor()
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
    for _, task in df.iterrows():
        # 解析 session_data 获取 user_id
        try:
            session_data = json.loads(task['session_data']) if task['session_data'] else {}
        except Exception:
            session_data = {}
        
        user_id = session_data.get('user_id', 'unknown')
        groups[user_id].append(task.to_dict())
    
    return dict(groups)


def process_group(user_id: str, tasks: List[Dict], window_minutes: int = MERGE_WINDOW_MINUTES):
    """处理单个用户的所有任务（组内串行，支持合并）
    
    Args:
        user_id: 用户ID
        tasks: 该用户的任务列表
        window_minutes: 合并窗口（分钟）
    """
    # 按时间排序
    tasks.sort(key=lambda t: t.get('created_at', ''))
    
    print(f"   👤 用户 {user_id[:8]}...: {len(tasks)} 个任务，组内串行处理")
    
    processed = 0
    merged = 0
    
    for task in tasks:
        try:
            # 使用现有的 process_task 函数（支持合并逻辑）
            success = process_task(task, window_minutes)
            if success:
                processed += 1
                # 检查是否合并
                if task.get('session_data', {}).get('is_merged'):
                    merged += 1
        except Exception as e:
            print(f"   ❌ 任务 {task['task_id']} 处理失败: {e}")
            fail_task(task['task_id'], str(e))
    
    print(f"   ✅ 用户 {user_id[:8]}... 完成: {processed}/{len(tasks)}, 合并: {merged}")
    return {'processed': processed, 'total': len(tasks), 'merged': merged}


def run_grouped_parallel_worker(max_groups: int = 4, batch_size: int = 20, 
                                 window_minutes: int = MERGE_WINDOW_MINUTES):
    """运行预分组并行工作进程（组间并行，组内串行，支持合并）
    
    Args:
        max_groups: 最大并发组数（默认4）
        batch_size: 每批获取任务数（默认20）
        window_minutes: 会话合并窗口（分钟）
    """
    global running, MERGE_WINDOW_MINUTES
    
    MERGE_WINDOW_MINUTES = window_minutes
    
    # 注册信号处理器
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    print("=" * 60)
    print("🚀 客服分析工作进程启动 [预分组并行模式]")
    print(f"   最大并发组: {max_groups}")
    print(f"   每批任务数: {batch_size}")
    print(f"   合并窗口: {window_minutes} 分钟")
    print("=" * 60)
    
    # 初始化
    init_queue_tables()
    init_engines()
    
    total_processed = 0
    total_groups = 0
    total_merged = 0
    
    try:
        while running:
            # 1. 获取并分组
            groups = fetch_and_group_tasks(batch_size=batch_size)
            
            if not groups:
                # 没有任务，尝试重试失败的
                retry_failed_tasks()
                
                # 仍然没有任务，等待
                print("⏳ 队列已空，等待新任务...")
                time.sleep(2.0)
                continue
            
            print(f"\n📦 获取 {len(groups)} 个用户组，共 {sum(len(t) for t in groups.values())} 个任务")
            total_groups += len(groups)
            
            # 2. 组间并行（ThreadPoolExecutor）
            with ThreadPoolExecutor(max_workers=max_groups) as executor:
                future_to_user = {}
                
                for user_id, tasks in groups.items():
                    # 限制每组任务数（避免用户倾斜）
                    tasks_limited = tasks[:5]  # 每组最多5个
                    if len(tasks) > 5:
                        print(f"   ⚠️ 用户 {user_id[:8]}... 任务过多({len(tasks)}个)，本次处理前5个")
                    
                    future = executor.submit(process_group, user_id, tasks_limited, window_minutes)
                    future_to_user[future] = user_id
                
                # 等待所有组完成
                for future in as_completed(future_to_user):
                    user_id = future_to_user[future]
                    try:
                        result = future.result()
                        total_processed += result['processed']
                        total_merged += result['merged']
                    except Exception as e:
                        print(f"   ❌ 用户组 {user_id[:8]}... 处理失败: {e}")
            
            print(f"   本批完成: {len(groups)}组, {sum(len(t) for t in groups.values())}任务")
                
    except Exception as e:
        print(f"\n❌ 工作进程异常: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if classifier:
            classifier.close()
        
        print("\n" + "=" * 60)
        print("📊 预分组并行工作进程结束")
        print(f"   处理组数: {total_groups}")
        print(f"   成功处理: {total_processed}")
        print(f"   合并会话: {total_merged}")
        print("=" * 60)


def main():
    # 读取 .env 配置作为默认值
    worker_mode = os.getenv('WORKER_MODE', 'serial')
    default_parallel = (worker_mode.lower() == 'parallel')
    default_grouped = (worker_mode.lower() == 'grouped')
    default_max_workers = int(os.getenv('WORKER_MAX_WORKERS', '3'))
    default_max_groups = int(os.getenv('WORKER_MAX_GROUPS', '4'))
    default_batch_size = int(os.getenv('WORKER_BATCH_SIZE', '10'))
    default_interval = float(os.getenv('WORKER_POLL_INTERVAL', '2.0'))
    default_window = int(os.getenv('MERGE_WINDOW_MINUTES', '30'))
    
    parser = argparse.ArgumentParser(description='客服分析异步工作进程')
    parser.add_argument('--daemon', action='store_true', help='后台模式运行')
    parser.add_argument('--once', action='store_true', help='处理完当前队列后退出')
    parser.add_argument('--interval', type=float, default=default_interval, help=f'轮询间隔（秒，默认{default_interval}）')
    parser.add_argument('--window', type=int, default=default_window, help=f'会话合并窗口（分钟，默认{default_window}）')
    parser.add_argument('--parallel', action='store_true', default=default_parallel, help=f'启用并行模式（默认{"并行" if default_parallel else "串行"}）')
    parser.add_argument('--grouped', action='store_true', default=default_grouped, help=f'启用预分组并行模式（默认{"预分组" if default_grouped else "否"}）')
    parser.add_argument('--serial', action='store_true', help='强制使用串行模式（覆盖.env配置）')
    parser.add_argument('--max-workers', type=int, default=default_max_workers, help=f'并行模式并发数（默认{default_max_workers}）')
    parser.add_argument('--max-groups', type=int, default=default_max_groups, help=f'预分组模式并发组数（默认{default_max_groups}）')
    parser.add_argument('--batch-size', type=int, default=default_batch_size, help=f'每批获取任务数（默认{default_batch_size}）')
    
    args = parser.parse_args()
    
    # 如果指定了 --serial，强制串行模式
    if args.serial:
        args.parallel = False
        args.grouped = False
    
    # 如果指定了 --grouped，启用预分组模式
    if args.grouped:
        args.parallel = False
    
    if args.daemon:
        # 后台模式
        import subprocess
        cmd = [
            sys.executable, __file__,
            '--once' if args.once else '',
            '--interval', str(args.interval),
            '--window', str(args.window)
        ]
        if args.grouped:
            cmd.extend(['--grouped', '--max-groups', str(args.max_groups), '--batch-size', str(args.batch_size)])
        elif args.parallel:
            cmd.extend(['--parallel', '--max-workers', str(args.max_workers), '--batch-size', str(args.batch_size)])
        # 过滤掉空字符串
        cmd = [c for c in cmd if c]
        subprocess.Popen(cmd, stdout=open('/tmp/worker.log', 'a'), stderr=subprocess.STDOUT)
        mode_str = "预分组并行" if args.grouped else ("并行" if args.parallel else "串行")
        print(f"🚀 工作进程已在后台启动 [{mode_str}模式]")
        print("   日志: tail -f /tmp/worker.log")
    else:
        # 前台模式
        if args.grouped:
            run_grouped_parallel_worker(max_groups=args.max_groups, batch_size=args.batch_size, window_minutes=args.window)
        elif args.parallel:
            run_parallel_worker(max_workers=args.max_workers, batch_size=args.batch_size)
        else:
            run_worker(once=args.once, interval=args.interval, window_minutes=args.window)


if __name__ == '__main__':
    main()
