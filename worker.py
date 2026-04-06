#!/usr/bin/env python3.14
"""异步分析工作进程 - v2.6.2（智能批量大小）

后台运行，从队列中获取任务并处理
支持四种模式：
  - 串行模式（--serial）：支持会话合并，顺序处理
  - 并行模式（--parallel）：速度快，不支持会话合并
  - 预分组并行模式（--grouped）：组间并行+组内串行，支持会话合并
  - 【v2.6.2】异步批量模式（--async-batch）：智能批量 + 真正并行（推荐）

【v2.6.2 新特性】
  - 智能批量：先count队列总数，一次性取完（上限150）
  - 废弃 WORKER_BATCH_SIZE，改用 WORKER_MAX_BATCH_SIZE
  - 自适应批量：根据Token估算自动调整5-50通/批
  - Token安全上限：200K（可配置）
  - Kimi并发提升至90（可配置）

用法:
    python3 worker.py                    # 默认模式（异步批量）
    python3 worker.py --serial           # 串行模式
    python3 worker.py --parallel         # 纯并行模式
    python3 worker.py --grouped          # 预分组并行模式
    python3 worker.py --async-batch      # 异步批量模式（推荐）
    python3 worker.py --daemon           # 后台运行
    python3 worker.py --once             # 处理完当前队列后退出
    python3 worker.py --window 30        # 设置合并窗口（分钟）

环境变量配置 (.env):
    BATCH_SCORE_SIZE=30              # 基础批量大小
    MAX_TOKENS_PER_BATCH=200000      # Token安全上限
    ADAPTIVE_BATCH_MIN=10            # 最小批量
    ADAPTIVE_BATCH_MAX=50            # 最大批量
    KIMI_MAX_CONCURRENT=90           # Kimi并发数

作者: 小虾米
更新: 2026-04-04（v2.6 Phase 1+2: 自适应批量大小）
"""

import os
import sys

# ========== 【v2.6.3新增】启动前依赖检查 ==========
def _check_dependencies():
    """检查必要的依赖模块是否存在"""
    required_modules = [
        ('dotenv', 'python-dotenv'),
        ('openai', 'openai'),
        ('pandas', 'pandas'),
        ('sqlite3', None),  # 标准库，无pip包名
        ('sentence_transformers', 'sentence-transformers'),
        ('httpx', 'httpx'),
        ('sklearn', 'scikit-learn'),
        ('numpy', 'numpy'),
    ]
    
    missing = []
    for mod_name, pip_name in required_modules:
        try:
            __import__(mod_name)
        except ImportError:
            missing.append((mod_name, pip_name or mod_name))
    
    if missing:
        print("❌ Worker启动失败：缺少必要依赖")
        print("")
        print("缺失的模块：")
        for mod_name, pip_name in missing:
            print(f"  - {mod_name} (pip install {pip_name})")
        print("")
        print("请运行以下命令安装：")
        pip_cmd = " ".join([f"{pip_name}" for _, pip_name in missing])
        print(f"  python3 -m pip install {pip_cmd}")
        print("")
        sys.exit(1)

# _check_dependencies()
# ========== 依赖检查结束 ==========

from dotenv import load_dotenv
load_dotenv()
import sys
import time
import argparse
import signal
import threading
import errno
import asyncio
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Dict, List

sys.path.insert(0, os.path.dirname(__file__))

from task_queue import (
    init_queue_tables, get_pending_task, get_pending_tasks, complete_task, fail_task,
    get_queue_stats, retry_failed_tasks, cancel_task, mark_processing, QUEUE_DB_PATH
)
from intent_classifier_v3 import RobustIntentClassifier
from smart_scoring_v2 import SmartScoringEngine
import sqlite3
import json

# ========== v2.6 Phase 2: 自适应批量配置 ==========
TOKENS_PER_CHAR = float(os.getenv('TOKENS_PER_CHAR', '0.67'))
OUTPUT_TOKENS_PER_SESSION = int(os.getenv('OUTPUT_TOKENS_PER_SESSION', '600'))
SYSTEM_PROMPT_TOKENS = int(os.getenv('SYSTEM_PROMPT_TOKENS', '900'))
MAX_TOKENS_PER_BATCH = int(os.getenv('MAX_TOKENS_PER_BATCH', '200000'))
ADAPTIVE_BATCH_MIN = int(os.getenv('ADAPTIVE_BATCH_MIN', '10'))
ADAPTIVE_BATCH_MAX = int(os.getenv('ADAPTIVE_BATCH_MAX', '50'))


def estimate_session_tokens(session_data: Dict) -> int:
    """估算单通会话的Token数（包含所有开销）"""
    messages = session_data.get('messages', [])
    # 会话内容字符数
    content_chars = sum(len(m.get('content', '')) for m in messages)
    # 转换为token + system prompt分摊 + output
    content_tokens = int(content_chars * TOKENS_PER_CHAR)
    # 加上输出开销（评分结果JSON）
    return SYSTEM_PROMPT_TOKENS + content_tokens + OUTPUT_TOKENS_PER_SESSION


def calculate_adaptive_batch_size(sessions: List[Dict], base_size: int = 30) -> int:
    """计算自适应批量大小
    
    策略：
    1. 先按base_size估算总token
    2. 如果超过MAX_TOKENS_PER_BATCH，按比例缩减
    3. 如果远低于上限且会话很短，尝试增加批量
    4. 始终在[ADAPTIVE_BATCH_MIN, ADAPTIVE_BATCH_MAX]范围内
    """
    # 估算base_size的token
    base_tokens = sum(estimate_session_tokens(s) for s in sessions[:base_size])
    
    if base_tokens > MAX_TOKENS_PER_BATCH:
        # 超出上限，按比例缩减
        ratio = MAX_TOKENS_PER_BATCH / base_tokens
        adjusted_size = int(base_size * ratio * 0.9)  # 留10%buffer
        return max(adjusted_size, ADAPTIVE_BATCH_MIN)
    
    # 计算平均单通token
    avg_tokens = base_tokens / base_size
    
    # 如果平均token较低，尝试扩大批量，但不超过20（避免API返回截断）
    if avg_tokens < 3000:  # 短会话
        potential_size = int(MAX_TOKENS_PER_BATCH / avg_tokens * 0.9)
        return min(potential_size, 20, len(sessions))  # 【v2.6.2】限制最大20
    
    # 默认使用base_size，但不超过20
    return min(base_size, 20, len(sessions))  # 【v2.6.2】限制最大20


# ========== 单例锁机制（使用 PID 文件）==========
PID_FILE = '/tmp/cs_analyzer_worker.pid'

def acquire_lock():
    """获取单例锁，防止多个 worker 同时运行"""
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE, 'r') as f:
                old_pid = int(f.read().strip())
            
            try:
                os.kill(old_pid, 0)
                print(f"❌ Worker 已在运行 (PID: {old_pid})")
                print(f"   如需重启，请先执行: pkill -f 'python3 worker.py'")
                return False
            except ProcessLookupError:
                print(f"🧹 清理残留锁文件 (PID {old_pid} 已不存在)")
                os.unlink(PID_FILE)
        except (ValueError, IOError) as e:
            print(f"🧹 清理损坏的锁文件: {e}")
            try:
                os.unlink(PID_FILE)
            except:
                pass
    
    try:
        with open(PID_FILE, 'w') as f:
            f.write(str(os.getpid()))
        print(f"✅ 获取锁成功 (PID: {os.getpid()})")
        return True
    except Exception as e:
        print(f"⚠️ 创建锁文件失败: {e}")
        return False

def release_lock():
    """释放单例锁"""
    try:
        if os.path.exists(PID_FILE):
            with open(PID_FILE, 'r') as f:
                pid_in_file = int(f.read().strip())
            
            if pid_in_file == os.getpid():
                os.unlink(PID_FILE)
                print("✅ 锁已释放")
    except Exception as e:
        print(f"⚠️ 释放锁失败: {e}")

# ========== 全局变量 ==========
running = True
classifier = None
scorer = None
MERGE_WINDOW_MINUTES = 30
MAX_WORKERS = 3
BATCH_SCORE_SIZE = int(os.getenv('BATCH_SCORE_SIZE', '3'))
KIMI_MAX_CONCURRENT = int(os.getenv('KIMI_MAX_CONCURRENT', '5'))
kimi_semaphore = None  # 在init_engines中初始化
db_lock = threading.Lock()


def signal_handler(sig, frame):
    """处理退出信号"""
    global running
    print("\n⚠️ 收到退出信号，正在保存当前任务...")
    running = False


def init_engines():
    """初始化分析引擎"""
    global classifier, scorer, kimi_semaphore
    
    print("🔄 初始化分析引擎...")
    
    api_key = os.getenv('MOONSHOT_API_KEY')
    if not api_key:
        config_path = os.path.expanduser('~/.openclaw/config.yaml')
        if os.path.exists(config_path):
            import yaml
            with open(config_path) as f:
                config = yaml.safe_load(f)
                api_key = config.get('moonshot', {}).get('apiKey')
    
    if not api_key:
        raise ValueError("未找到MOONSHOT_API_KEY，请设置环境变量或在配置文件中配置")
    
    # 初始化Kimi并发信号量
    kimi_semaphore = asyncio.Semaphore(KIMI_MAX_CONCURRENT)
    print(f"✅ Kimi并发控制: 最大{KIMI_MAX_CONCURRENT}并发")
    
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
    return any(kw in content for kw in TRANSFER_KEYWORDS)


def find_related_sessions(main_task: dict, window_minutes: int = MERGE_WINDOW_MINUTES) -> dict:
    """查找关联会话"""
    main_data = main_task.get("session_data", {})
    if isinstance(main_data, str):
        main_data = json.loads(main_data)
    main_user_id = main_data.get('user_id', '')
    main_staff = main_data.get('staff_name', '')
    main_messages = main_data.get('messages', [])
    
    if not main_messages:
        return {'mergeable': [], 'transfer_chain': [], 'same_user': []}
    
    main_start = parse_timestamp(main_messages[0].get('timestamp', ''))
    main_end = parse_timestamp(main_messages[-1].get('timestamp', ''))
    
    if not main_start or not main_end:
        return {'mergeable': [], 'transfer_chain': [], 'same_user': []}
    
    db_path = os.path.join(os.path.dirname(__file__), 'data', 'task_queue.db')
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT task_id, session_id, session_data 
        FROM analysis_tasks 
        WHERE status = 'pending' AND task_id != ?
    ''', (main_task.get('task_id'),))
    
    result = {'mergeable': [], 'transfer_chain': [], 'same_user': []}

    for row in cursor.fetchall():
        task_id, session_id, session_data_json = row
        try:
            task_data = json.loads(session_data_json)
            task_user_id = task_data.get('user_id', '')
            task_staff = task_data.get('staff_name', '')
            task_messages = task_data.get('messages', [])

            if task_user_id != main_user_id or not task_messages:
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
    """合并会话数据"""
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


def save_to_database(session_id: str, session_data: dict, intent, result: dict, session_count: int = 1):
    """保存分析结果到数据库"""
    db_path = os.path.join(os.path.dirname(__file__), 'data', 'cs_analyzer_new.db')
    conn = sqlite3.connect(db_path)
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
              session_count, start_time, end_time, datetime.now().isoformat(),
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
            session_count, start_time, end_time, datetime.now().isoformat(),
            1 if is_transfer else 0, transfer_from, transfer_to, transfer_reason,
            json.dumps(related_sessions, ensure_ascii=False)
        ))
    
    conn.commit()
    conn.close()


# ========== 【v2.4新增】异步批量处理 ==========

async def process_group_async(user_id: str, tasks: List[Dict], 
                               window_minutes: int = MERGE_WINDOW_MINUTES,
                               batch_size: int = 3) -> Dict:
    """异步处理单个用户的所有任务（组内异步+批量评分）
    
    Args:
        user_id: 用户ID
        tasks: 该用户的任务列表
        window_minutes: 合并窗口（分钟）
        batch_size: 批量评分大小（默认3通/批）
    """
    tasks.sort(key=lambda t: t.get('created_at', ''))
    print(f"   👤 用户 {user_id[:8]}...: {len(tasks)} 个任务，异步批量处理")
    
    # 第一步：会话合并（在线程池中执行）
    loop = asyncio.get_event_loop()
    merged_tasks = await loop.run_in_executor(None, _prepare_merged_tasks_sync, tasks, window_minutes)
    print(f"   📦 合并后: {len(merged_tasks)} 个独立分析单元")
    
    # 第二步：批量评分（异步+限流）
    results = await _batch_score_with_limit(merged_tasks, batch_size)
    
    # 统计
    processed = sum(1 for r in results if 'error' not in r)
    merged = sum(1 for t in merged_tasks if t.get('is_merged'))
    
    print(f"   ✅ 用户 {user_id[:8]}... 完成: {processed}/{len(merged_tasks)}, 合并: {merged}")
    return {'processed': processed, 'total': len(tasks), 'merged': merged}


def _prepare_merged_tasks_sync(tasks: List[Dict], window_minutes: int) -> List[Dict]:
    """同步准备合并任务（在线程池中执行）"""
    merged_tasks = []
    
    for task in tasks:
        try:
            related = find_related_sessions(task, window_minutes)
            mergeable_tasks = related['mergeable']
            
            if mergeable_tasks:
                merged_data = merge_session_data(task, mergeable_tasks)
                for t in mergeable_tasks:
                    cancel_task(t['task_id'])
                task['session_data'] = merged_data
                task['is_merged'] = True
            else:
                session_data = task['session_data']
                if isinstance(session_data, str):
                    session_data = json.loads(session_data)
                task['session_data'] = session_data
                task['is_merged'] = False
            
            merged_tasks.append(task)
        except Exception as e:
            print(f"   ⚠️ 任务准备失败: {e}")
            fail_task(task['task_id'], str(e))
    
    return merged_tasks


async def _batch_score_with_limit(tasks: List[Dict], batch_size: int) -> List[Dict]:
    """带限流的批量评分"""
    global kimi_semaphore
    
    if not tasks:
        return []
    
    # 按batch_size分组
    batches = [tasks[i:i+batch_size] for i in range(0, len(tasks), batch_size)]
    all_results = []
    
    for batch_idx, batch in enumerate(batches):
        print(f"   🔄 处理批次 {batch_idx+1}/{len(batches)} ({len(batch)}通)")
        
        # 使用信号量控制并发
        async with kimi_semaphore:
            sessions = [t['session_data'] for t in batch]
            
            # 执行批量评分
            batch_results = await scorer.score_sessions_batch_async(sessions)
            
            # 保存结果到数据库（在线程池中执行）
            loop = asyncio.get_event_loop()
            for task, result in zip(batch, batch_results):
                # 【Bug修复】检查result是否包含有效的评分字段
                has_valid_scores = (
                    'error' not in result and
                    result.get('dimension_scores') is not None and
                    result.get('summary') is not None
                )
                if has_valid_scores:
                    await loop.run_in_executor(None, _save_result_sync, task, result)
                    complete_task(task['task_id'], result)
                else:
                    error_msg = result.get('error', '评分结果不完整（缺少dimension_scores或summary）')
                    print(f"   ⚠️ 任务 {str(task['task_id'])[:20]}... 评分无效: {error_msg}")
                    fail_task(task['task_id'], error_msg)
            
            all_results.extend(batch_results)
    
    return all_results


def _save_result_sync(task: Dict, result: Dict):
    """同步保存结果"""
    try:
        session_id = task['session_id']
        session_data = task['session_data']
        
        # 构造意图对象
        intent_data = result.get('_metadata', {}).get('pre_analysis', {})
        class MockIntent:
            pass
        intent = MockIntent()
        for k, v in intent_data.items():
            setattr(intent, k, v)
        
        save_to_database(session_id, session_data, intent, result, 
                        session_data.get('session_count', 1))
    except Exception as e:
        print(f"   ❌ 保存结果失败: {e}")


# ========== 原有模式（串行/并行/分组） ==========

def process_group(user_id: str, tasks: List[Dict], window_minutes: int = MERGE_WINDOW_MINUTES):
    """处理单个用户的所有任务（组内串行，支持合并）"""
    tasks.sort(key=lambda t: t.get('created_at', ''))
    print(f"   👤 用户 {user_id[:8]}...: {len(tasks)} 个任务，组内串行处理")
    
    processed = 0
    merged = 0
    
    for task in tasks:
        try:
            success = process_task_sync(task, window_minutes)
            if success:
                processed += 1
                session_data = task.get('session_data', {})
                if isinstance(session_data, str):
                    session_data = json.loads(session_data)
                if session_data.get('is_merged'):
                    merged += 1
        except Exception as e:
            print(f"   ❌ 任务 {task['task_id']} 处理失败: {e}")
            fail_task(task['task_id'], str(e))
    
    print(f"   ✅ 用户 {user_id[:8]}... 完成: {processed}/{len(tasks)}, 合并: {merged}")
    return {'processed': processed, 'total': len(tasks), 'merged': merged}


def process_task_sync(task: dict, window_minutes: int = MERGE_WINDOW_MINUTES) -> bool:
    """同步处理单个任务（原有逻辑）"""
    task_id = task['task_id']
    session_id = task['session_id']
    
    print(f"\n📋 处理任务 #{task_id}: {session_id}")
    start_time = time.time()
    
    try:
        related = find_related_sessions(task, window_minutes)
        mergeable_tasks = related['mergeable']
        
        if mergeable_tasks:
            print(f"   📦 发现 {len(mergeable_tasks)} 个可合并会话")
            merged_data = merge_session_data(task, mergeable_tasks)
            for t in mergeable_tasks:
                cancel_task(t['task_id'])
            session_data = merged_data
            is_merged = True
            session_count = len(merged_data.get('merged_session_ids', [1]))
        else:
            session_data = task['session_data']
            if isinstance(session_data, str):
                session_data = json.loads(session_data)
            is_merged = False
            session_count = 1
        
        intent = classifier.classify(session_data['messages'])
        result = scorer.score_session(session_data)
        
        if result and 'dimension_scores' in result:
            ds = result['dimension_scores']
            total = sum(ds.get(d, {}).get('score', 0) for d in ['professionalism', 'standardization', 'policy_execution', 'conversion'])
            print(f"   评分{' [合并]' if is_merged else ''}: {total}/20")
            
            save_to_database(session_id, session_data, intent, result, session_count)
            complete_task(task_id, result)
            print(f"   ✅ 完成 (耗时: {time.time()-start_time:.1f}s)")
            return True
        else:
            raise ValueError("评分结果异常")
    except Exception as e:
        error_msg = str(e)
        print(f"   ❌ 失败: {error_msg}")
        fail_task(task_id, error_msg)
        return False


def fetch_and_group_tasks(max_batch_size: int = 150) -> Dict[str, List[Dict]]:
    """【v2.6.2】智能获取待处理任务并按 user_id 分组
    
    优化点：
    - 先count队列中pending任务总数
    - 如果总数 <= max_batch_size，一次性全取
    - 如果总数 > max_batch_size，取max_batch_size个
    - 实现"看人数打饭"策略，避免多次轮询
    
    Args:
        max_batch_size: 单次处理上限（默认150），防止内存溢出
    """
    import pandas as pd
    
    conn = sqlite3.connect(QUEUE_DB_PATH)
    cursor = conn.cursor()
    
    # 【v2.6.2】智能感知：先count pending任务总数
    cursor.execute("SELECT COUNT(*) FROM analysis_tasks WHERE status = 'pending'")
    total_pending = cursor.fetchone()[0]
    
    # 如果总数 <= max_batch_size，全取；否则取max_batch_size
    limit = total_pending if total_pending <= max_batch_size else max_batch_size
    
    if total_pending > max_batch_size:
        print(f"   📊 队列共有 {total_pending} 个任务，本次处理前 {limit} 个")
    else:
        print(f"   📊 队列共有 {total_pending} 个任务，全部取出处理")
    
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
    
    return dict(groups)


# ========== Worker运行模式 ==========

def run_grouped_parallel_worker(max_groups: int = 4, max_batch_size: int = 150, 
                                 window_minutes: int = MERGE_WINDOW_MINUTES):
    """【v2.6.2】运行预分组并行工作进程（组间并行，组内串行）"""
    global running, MERGE_WINDOW_MINUTES
    MERGE_WINDOW_MINUTES = window_minutes
    
    if not acquire_lock():
        return
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    print("=" * 60)
    print("🚀 客服分析工作进程启动 [预分组并行模式 v2.6.2]")
    print(f"   最大并发组: {max_groups}")
    print(f"   单次处理上限: {max_batch_size}")
    print("=" * 60)
    
    init_queue_tables()
    init_engines()
    
    total_processed = 0
    total_groups = 0
    total_merged = 0
    
    try:
        while running:
            groups = fetch_and_group_tasks(max_batch_size=max_batch_size)
            
            if not groups:
                retry_failed_tasks()
                print("⏳ 队列已空，等待新任务...")
                time.sleep(2.0)
                continue
            
            print(f"\n📦 获取 {len(groups)} 个用户组")
            total_groups += len(groups)
            
            with ThreadPoolExecutor(max_workers=max_groups) as executor:
                future_to_user = {}
                
                for user_id, tasks in groups.items():
                    tasks_limited = tasks[:5]
                    if len(tasks) > 5:
                        print(f"   ⚠️ 用户 {user_id[:8]}... 任务过多({len(tasks)}个)，本次处理前5个")
                    
                    future = executor.submit(process_group, user_id, tasks_limited, window_minutes)
                    future_to_user[future] = user_id
                
                for future in as_completed(future_to_user):
                    user_id = future_to_user[future]
                    try:
                        result = future.result()
                        total_processed += result['processed']
                        total_merged += result['merged']
                    except Exception as e:
                        print(f"   ❌ 用户组 {user_id[:8]}... 处理失败: {e}")
                
    except Exception as e:
        print(f"\n❌ 工作进程异常: {e}")
    finally:
        if classifier:
            classifier.close()
        release_lock()
        
        print("\n" + "=" * 60)
        print(f"📊 处理组数: {total_groups}, 成功: {total_processed}, 合并: {total_merged}")
        print("=" * 60)


def _fast_scene_classify(messages: List[Dict]) -> str:
    """快速场景分类（关键词规则，不调用Ollama）"""
    # 取前3条消息作为判断依据
    text = ' '.join([m.get('content', '') for m in messages[:3]]).lower()
    
    # 售中阶段关键词（用户已下单）
    if any(k in text for k in ['订单', '发货', '物流', '退款', '取消订单', '改地址', '我的订单']):
        return '售中阶段'
    
    # 售后阶段关键词
    if any(k in text for k in ['安装', '维修', '故障', '售后', '保修', '坏了', '不工作']):
        return '售后阶段'
    
    # 客诉处理关键词（高优先级）
    if any(k in text for k in ['投诉', '差评', '退货', '举报', '欺骗', '骗子', '虚假宣传']):
        return '客诉处理'
    
    # 默认售前阶段
    return '售前阶段'


async def _batch_score_with_limit_v2(tasks: List[Dict], base_batch_size: int) -> List[Dict]:
    """【v2.6 Phase 2】带限流的批量评分（自适应批量大小）
    
    新增：
    1. 根据会话Token估算动态调整批量大小
    2. 超长会话自动降级，短会话自动扩容
    3. 确保不超过MAX_TOKENS_PER_BATCH安全上限
    """
    global kimi_semaphore
    
    if not tasks:
        return []
    
    # 【v2.6】自适应批量：计算最优批量大小
    sessions = [t['session_data'] for t in tasks]
    optimal_batch_size = calculate_adaptive_batch_size(sessions, base_batch_size)
    
    print(f"\n   📊 自适应批量: 基础={base_batch_size}, 优化后={optimal_batch_size}, 总任务={len(tasks)}")
    
    # 按优化后的batch_size分组
    batches = [tasks[i:i+optimal_batch_size] for i in range(0, len(tasks), optimal_batch_size)]
    total_batches = len(batches)
    
    # 预估总token（用于日志）
    total_tokens = sum(estimate_session_tokens(s['session_data']) for s in tasks)
    print(f"   💾 预估总Token: {total_tokens:,} (上限: {MAX_TOKENS_PER_BATCH:,})")
    
    async def score_one_batch(batch_idx: int, batch: List[Dict]) -> List[Dict]:
        """评分单个批次（内部使用信号量限流）"""
        batch_tokens = sum(estimate_session_tokens(t['session_data']) for t in batch)
        print(f"   🔄 批次 {batch_idx+1}/{total_batches} ({len(batch)}通, ~{batch_tokens:,}tokens) 启动")
        
        async with kimi_semaphore:
            batch_sessions = [t['session_data'] for t in batch]
            
            # 【v2.5】构建预分析结果（从task的_scene字段）
            pre_analyses = []
            for task in batch:
                scene = task.get('_scene', '售前阶段')
                pre_analyses.append({
                    'scene': scene,
                    'sub_scene': '其他',
                    'intent': '咨询',
                    'sentiment': 'neutral',
                    'confidence': 0.8,
                    'reasoning': '基于关键词规则快速分类',
                    'source': 'fast_classify'
                })
            
            # 执行批量评分（传入预分析结果，跳过内部预分析）
            batch_results = await scorer.score_sessions_batch_async(batch_sessions, pre_analyses)
            
            # 保存结果到数据库（在线程池中执行）
            loop = asyncio.get_event_loop()
            for task, result in zip(batch, batch_results):
                # 【Bug修复】检查result是否包含有效的评分字段
                has_valid_scores = (
                    'error' not in result and
                    result.get('dimension_scores') is not None and
                    result.get('summary') is not None
                )
                if has_valid_scores:
                    await loop.run_in_executor(None, _save_result_sync, task, result)
                    complete_task(task['task_id'], result)
                else:
                    error_msg = result.get('error', '评分结果不完整（缺少dimension_scores或summary）')
                    print(f"   ⚠️ 任务 {str(task['task_id'])[:20]}... 评分无效: {error_msg}")
                    fail_task(task['task_id'], error_msg)
            
            print(f"   ✅ 批次 {batch_idx+1}/{total_batches} 完成")
            return batch_results
    
    # 【v2.5.1修复】所有批次同时启动，真正并行竞争信号量
    print(f"\n🚀 启动 {total_batches} 个评分批次（并发限制: {KIMI_MAX_CONCURRENT}）")
    all_results_nested = await asyncio.gather(*[
        score_one_batch(i, batch) for i, batch in enumerate(batches)
    ])
    
    # 展平结果
    all_results = []
    for batch_results in all_results_nested:
        all_results.extend(batch_results)
    
    return all_results


async def run_async_batch_worker(max_groups: int = 4, max_batch_size: int = 150,
                                  window_minutes: int = MERGE_WINDOW_MINUTES,
                                  score_batch_size: int = 30):
    """【v2.6.1】运行异步批量工作进程 - 跨场景合并优化
    
    核心优化：
    - 不再按场景分组，所有会话统一批量处理
    - 场景信息通过pre_analysis传入，由模型自行处理
    - 实现真正的20-40通/批超大批量
    """
    global running, MERGE_WINDOW_MINUTES, kimi_semaphore
    MERGE_WINDOW_MINUTES = window_minutes
    
    if not acquire_lock():
        return
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    print("=" * 60)
    print("🚀 客服分析工作进程启动 [异步批量模式 v2.6.2 - 智能批量]")
    print(f"   最大并发组: {max_groups}")
    print(f"   单次处理上限: {max_batch_size}")
    print(f"   基础批量评分: {score_batch_size}通/批")
    print(f"   自适应范围: [{ADAPTIVE_BATCH_MIN}, {ADAPTIVE_BATCH_MAX}]")
    print(f"   Token上限: {MAX_TOKENS_PER_BATCH:,}")
    print(f"   Kimi并发: {KIMI_MAX_CONCURRENT}")
    print("=" * 60)
    
    init_queue_tables()
    init_engines()
    
    total_processed = 0
    total_api_calls = 0
    
    try:
        while running:
            groups = fetch_and_group_tasks(max_batch_size=max_batch_size)
            
            if not groups:
                retry_failed_tasks()
                print("⏳ 队列已空，等待新任务...")
                await asyncio.sleep(2.0)
                continue
            
            # 收集所有任务（跨用户）
            all_tasks = []
            for user_id, user_tasks in groups.items():
                for task in user_tasks[:5]:
                    all_tasks.append(task)
            
            print(f"\n📦 获取 {len(all_tasks)} 个任务，来自 {len(groups)} 个用户")
            
            # 【v2.6.1优化】跨场景合并：不再按场景分组，统一批量处理
            # 场景信息已通过 _scene 字段保存在 task 中，会传给 pre_analysis
            print(f"\n🔍 对 {len(all_tasks)} 通会话进行快速场景分类...")
            for task in all_tasks:
                session_data = task.get('session_data', {})
                if isinstance(session_data, str):
                    session_data = json.loads(session_data)
                messages = session_data.get('messages', [])
                task['_scene'] = _fast_scene_classify(messages)
            
            # 统计场景分布（仅用于日志）
            scene_groups = defaultdict(list)
            for task in all_tasks:
                scene = task.get('_scene', '其他')
                scene_groups[scene].append(task)
            scene_summary = {k: len(v) for k, v in scene_groups.items()}
            print(f"\n📊 场景分布: {scene_summary}")
            
            # 【v2.6.1核心优化】跨场景统一批量处理
            print(f"\n🚀 跨场景合并处理: {len(all_tasks)} 通会话")
            
            # 统一进行会话合并
            loop = asyncio.get_event_loop()
            merged_tasks = await loop.run_in_executor(
                None, _prepare_merged_tasks_sync, all_tasks, window_minutes
            )
            
            # 跨场景批量评分（传入场景信息）
            results = await _batch_score_with_limit_v2(merged_tasks, score_batch_size)
            
            # 统计
            processed = sum(1 for r in results if 'error' not in r)
            total_processed += processed
            total_api_calls += (len(merged_tasks) + score_batch_size - 1) // score_batch_size
            
            print(f"\n   ✅ 本批完成: {len(all_tasks)}通, API调用: {total_api_calls}次")
                
    except Exception as e:
        print(f"\n❌ 工作进程异常: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if classifier:
            classifier.close()
        release_lock()
        
        print("\n" + "=" * 60)
        print("📊 异步批量工作进程结束")
        print(f"   成功处理: {total_processed}")
        print(f"   总API调用: {total_api_calls}次")
        print("=" * 60)


def main():
    """主入口"""
    worker_mode = os.getenv('WORKER_MODE', 'grouped')
    default_async_batch = (worker_mode.lower() == 'async-batch')
    default_grouped = (worker_mode.lower() == 'grouped')
    default_parallel = (worker_mode.lower() == 'parallel')
    
    default_max_groups = int(os.getenv('WORKER_MAX_GROUPS', '4'))
    # 【v2.6.2】优先使用 WORKER_MAX_BATCH_SIZE，兼容旧的 WORKER_BATCH_SIZE
    default_max_batch_size = int(os.getenv('WORKER_MAX_BATCH_SIZE',
                                           os.getenv('WORKER_BATCH_SIZE', '150')))
    default_window = int(os.getenv('MERGE_WINDOW_MINUTES', '30'))
    default_score_batch = int(os.getenv('BATCH_SCORE_SIZE', '30'))
    
    parser = argparse.ArgumentParser(description='客服分析异步工作进程 v2.6')
    parser.add_argument('--daemon', action='store_true', help='后台模式运行')
    parser.add_argument('--once', action='store_true', help='处理完当前队列后退出')
    parser.add_argument('--window', type=int, default=default_window, help=f'合并窗口（默认{default_window}分钟）')
    parser.add_argument('--async-batch', action='store_true', default=default_async_batch, help='异步批量模式（推荐）')
    parser.add_argument('--grouped', action='store_true', default=default_grouped, help='预分组并行模式')
    parser.add_argument('--parallel', action='store_true', default=default_parallel, help='并行模式')
    parser.add_argument('--serial', action='store_true', help='串行模式')
    parser.add_argument('--max-groups', type=int, default=default_max_groups, help=f'最大并发组（默认{default_max_groups}）')
    parser.add_argument('--max-batch-size', type=int, default=default_max_batch_size, help=f'单次处理上限（默认{default_max_batch_size}）')
    parser.add_argument('--score-batch-size', type=int, default=default_score_batch, help=f'批量评分大小（默认{default_score_batch}通）')
    
    args = parser.parse_args()
    
    # 模式优先级
    if args.serial:
        mode = 'serial'
    elif args.parallel:
        mode = 'parallel'
    elif args.grouped:
        mode = 'grouped'
    elif args.async_batch:
        mode = 'async-batch'
    else:
        mode = 'grouped'  # 默认
    
    if args.daemon:
        import subprocess
        cmd = [sys.executable, __file__, f'--{mode}', '--once',
               '--window', str(args.window),
               '--max-groups', str(args.max_groups),
               '--max-batch-size', str(args.max_batch_size)]
        if mode == 'async-batch':
            cmd.extend(['--score-batch-size', str(args.score_batch_size)])
        subprocess.Popen(cmd, stdout=open('/tmp/worker.log', 'a'), stderr=subprocess.STDOUT)
        print(f"🚀 工作进程已在后台启动 [{mode}模式]")
    else:
        if mode == 'async-batch':
            asyncio.run(run_async_batch_worker(
                max_groups=args.max_groups,
                max_batch_size=args.max_batch_size,
                window_minutes=args.window,
                score_batch_size=args.score_batch_size
            ))
        elif mode == 'grouped':
            run_grouped_parallel_worker(
                max_groups=args.max_groups,
                max_batch_size=args.max_batch_size,
                window_minutes=args.window
            )
        else:
            print(f"⚠️ 模式 {mode} 暂未在此版本实现")


if __name__ == '__main__':
    main()
