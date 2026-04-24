#!/usr/bin/env python3.14
"""异步分析工作进程 - v2.6.5（异步写入队列优化版）

后台运行，从队列中获取任务并处理
支持四种模式：
  - 串行模式（--serial）：支持会话合并，顺序处理
  - 并行模式（--parallel）：速度快，不支持会话合并
  - 预分组并行模式（--grouped）：组间并行+组内串行，支持会话合并
  - 【v2.6.5】异步批量模式（--async-batch）：异步写入队列 + 真正并行（推荐）

【v2.6.5 新特性】
  - 【P0修复】异步写入队列：解耦API调用和数据库写入，消除事件循环阻塞
  - 智能批量：先count队列总数，一次性取完（上限150）
  - 废弃 WORKER_BATCH_SIZE，改用 WORKER_MAX_BATCH_SIZE
  - 自适应批量：根据Token估算自动调整5-50通/批
  - Token安全上限：200K（可配置）
  - Kimi并发提升至100（可配置）

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
    ADAPTIVE_BATCH_MIN=3            # 最小批量
    ADAPTIVE_BATCH_MAX=5             # 最大批量
    KIMI_MAX_CONCURRENT=90           # Kimi并发数

作者: 小虾米
更新: 2026-04-18（v2.6.5 Phase 1: 异步写入队列优化）
"""

import os
import sys

# ========== 【v2.6.6拆分 Step 1】导入共享配置 ==========
import worker_config as cfg
from config import LLM_CONFIG  # 【双模式LLM】导入统一LLM配置

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
import time
import argparse
import signal
import threading  # 【v2.6.5】导入线程模块
import queue  # 【v2.6.5】导入队列模块
import errno
import asyncio
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Dict, List

sys.path.insert(0, os.path.dirname(__file__))

from task_queue import (
    init_queue_tables, get_pending_task, get_pending_tasks, complete_task, fail_task,
    get_queue_stats, retry_failed_tasks, cancel_task, mark_processing, QUEUE_DB_PATH,
    force_retry_all_failed,  # 【修复】导入强制重试函数
    get_queue_connection    # 【P2-1修复】导入统一的数据库连接函数
)
from db_utils import init_sessions_table
from intent_classifier_v3 import RobustIntentClassifier
from smart_scoring_v2 import SmartScoringEngine
from scene_utils import classify_scene_by_keywords  # 【P1-1修复】提取到独立模块
from session_merge import (
    parse_timestamp,
    has_transfer_keyword,
    find_related_sessions,
    merge_session_data,
    deduplicate_sessions,
)
from db_operations import save_to_database, _save_result_sync, _log_inconsistency
from db_writer import start_db_writer, stop_db_writer, queue_save_result, wait_for_db_writes, wait_for_db_writes_async
from task_fetcher import fetch_and_group_tasks, _fetch_failed_tasks_for_retry
from batch_scoring import (
    _batch_score_with_limit,
    _batch_score_with_limit_v2,
    _retry_tasks_batch,
    _retry_single_task,
)
import sqlite3  # 【N-5修复保留】用于 --once 模式检查 processing 任务
import json

# ========== v2.6 Phase 2: 自适应批量配置（已迁移到 worker_config.py）==========
# 所有配置常量、工具函数、全局变量已通过 `import worker_config as cfg` 共享
# 详见 worker_config.py

def acquire_lock():
    """获取单例锁，防止多个 worker 同时运行"""
    if os.path.exists(cfg.PID_FILE):
        try:
            with open(cfg.PID_FILE, 'r') as f:
                old_pid = int(f.read().strip())
            
            try:
                os.kill(old_pid, 0)
                print(f"❌ Worker 已在运行 (PID: {old_pid})")
                print(f"   如需重启，请先执行: pkill -f 'python3 worker.py'")
                return False
            except ProcessLookupError:
                print(f"🧹 清理残留锁文件 (PID {old_pid} 已不存在)")
                os.unlink(cfg.PID_FILE)
        except (ValueError, IOError) as e:
            print(f"🧹 清理损坏的锁文件: {e}")
            try:
                os.unlink(cfg.PID_FILE)
            except:
                pass
    
    try:
        with open(cfg.PID_FILE, 'w') as f:
            f.write(str(os.getpid()))
        print(f"✅ 获取锁成功 (PID: {os.getpid()})")
        return True
    except Exception as e:
        print(f"⚠️ 创建锁文件失败: {e}")
        return False

def release_lock():
    """释放单例锁"""
    try:
        if os.path.exists(cfg.PID_FILE):
            with open(cfg.PID_FILE, 'r') as f:
                pid_in_file = int(f.read().strip())
            
            if pid_in_file == os.getpid():
                os.unlink(cfg.PID_FILE)
                print("✅ 锁已释放")
    except Exception as e:
        print(f"⚠️ 释放锁失败: {e}")

# ========== 【v2.6.6拆分 Step 5】数据库写入队列（已迁移到 db_writer.py）==========
# 已通过 from db_writer import ... 导入


def signal_handler(sig, frame):
    """处理退出信号"""
    print("\n⚠️ 收到退出信号，正在保存当前任务...")
    cfg.running = False


def init_engines():
    """初始化分析引擎"""
    print("🔄 初始化分析引擎...")
    
    llm_mode = LLM_CONFIG["mode"]
    print(f"   LLM模式: {llm_mode}")
    
    if llm_mode == "local":
        # 本地模式 (LM Studio)
        api_key = LLM_CONFIG.get("api_key", "not-needed")
        base_url = LLM_CONFIG["base_url"]
        model = LLM_CONFIG["model"]
        print(f"   本地模型: {model}")
        print(f"   Base URL: {base_url}")
    else:
        # 云端模式 (Moonshot)
        api_key = LLM_CONFIG.get("api_key")
        if not api_key:
            raise ValueError("未找到MOONSHOT_API_KEY，请设置环境变量或在配置文件中配置")
        base_url = LLM_CONFIG["base_url"]
        model = LLM_CONFIG["model"]
        print(f"   云端模型: {model}")
    
    # 初始化Kimi并发信号量
    cfg.kimi_semaphore = asyncio.Semaphore(cfg.KIMI_MAX_CONCURRENT)
    print(f"✅ LLM并发控制: 最大{cfg.KIMI_MAX_CONCURRENT}并发")
    
    cfg.classifier = RobustIntentClassifier()
    cfg.scorer = SmartScoringEngine(
        api_key=api_key,
        base_url=base_url,
        model=model
    )
    
    print("✅ 引擎初始化完成")


# ========== 会话合并模块（已迁移到 session_merge.py）==========
# parse_timestamp, has_transfer_keyword, find_related_sessions, merge_session_data
# 已通过 from session_merge import ... 导入


async def process_group_async(user_id: str, tasks: List[Dict], 
                               window_minutes: int = cfg.MERGE_WINDOW_MINUTES,
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


# ========== 去重模块（已迁移到 session_merge.py）==========
# deduplicate_sessions 已通过 from session_merge import ... 导入


def _prepare_merged_tasks_sync(tasks: List[Dict], window_minutes: int) -> List[Dict]:
    """同步准备合并任务（在线程池中执行）
    
    【Opus修复】添加去重步骤，消除重复会话问题
    """
    # 【Opus修复-P0】先去重，避免同一session_id重复处理
    tasks = deduplicate_sessions(tasks)
    
    # === 会话概况追踪 START ===
    try:
        for t in tasks:
            sd = t.get('session_data', {})
            if isinstance(sd, str):
                sd = json.loads(sd)
            msgs = sd.get('messages', [])
            msg_chars = sum(len(m.get('content', '')) for m in msgs)
            user_msgs = sum(1 for m in msgs if m.get('role') in ('user', 'customer'))
            staff_msgs = sum(1 for m in msgs if m.get('role') == 'staff')
            print(f"   📊 SESSION_PROFILE|sid={t.get('session_id','')[:20]}|msgs={len(msgs)}|user={user_msgs}|staff={staff_msgs}|chars={msg_chars}|merged={t.get('is_merged', False)}")
    except Exception as e:
        print(f"   ⚠️ SESSION_PROFILE logging failed: {e}")
    # === 会话概况追踪 END ===
    
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


def process_group(user_id: str, tasks: List[Dict], window_minutes: int = cfg.MERGE_WINDOW_MINUTES):
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


def process_task_sync(task: dict, window_minutes: int = cfg.MERGE_WINDOW_MINUTES) -> bool:
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
        
        intent = cfg.classifier.classify(session_data['messages'])
        result = cfg.scorer.score_session(session_data)
        
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


def run_grouped_parallel_worker(max_groups: int = 4, max_batch_size: int = 150, 
                                 window_minutes: int = cfg.MERGE_WINDOW_MINUTES):
    """【v2.6.2】运行预分组并行工作进程（组间并行，组内串行）"""

    cfg.MERGE_WINDOW_MINUTES = window_minutes
    
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
    init_sessions_table()
    init_engines()
    
    total_processed = 0
    total_groups = 0
    total_merged = 0
    
    try:
        while cfg.running:
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
        if cfg.classifier:
            cfg.classifier.close()
        release_lock()
        
        print("\n" + "=" * 60)
        print(f"📊 处理组数: {total_groups}, 成功: {total_processed}, 合并: {total_merged}")
        print("=" * 60)


async def run_async_batch_worker(max_groups: int = 4, max_batch_size: int = 150,
                                  window_minutes: int = cfg.MERGE_WINDOW_MINUTES,
                                  score_batch_size: int = 30,
                                  once: bool = False):
    """【v2.6.4】运行异步批量工作进程 - Opus修复版
    
    P0修复：
    1. 同进程内重试失败任务，消除两阶段断层（避免Worker进程重启开销）
    2. 失败重试降级为单通评分，消除批次连锁失败
    
    核心优化：
    - 不再按场景分组，所有会话统一批量处理
    - 场景信息通过pre_analysis传入，由模型自行处理
    - 实现真正的20-40通/批超大批量
    """

    cfg.MERGE_WINDOW_MINUTES = window_minutes
    
    if not acquire_lock():
        return
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    print("=" * 60)
    print("🚀 客服分析工作进程启动 [异步批量模式 v2.6.4 - Opus修复版]")
    print(f"   最大并发组: {max_groups}")
    print(f"   单次处理上限: {max_batch_size}")
    print(f"   基础批量评分: {score_batch_size}通/批")
    print(f"   自适应范围: [{cfg.ADAPTIVE_BATCH_MIN}, {cfg.ADAPTIVE_BATCH_MAX}]")
    print(f"   Token上限: {cfg.MAX_TOKENS_PER_BATCH:,}")
    print(f"   Kimi并发: {cfg.KIMI_MAX_CONCURRENT}")
    print("=" * 60)
    
    init_queue_tables()
    init_sessions_table()
    init_engines()
    start_db_writer()  # 【v2.6.5】启动数据库写入线程
    
    total_processed = 0
    total_api_calls = 0
    
    try:
        while cfg.running:
            groups = fetch_and_group_tasks(max_batch_size=max_batch_size, once=once)
            
            if not groups:
                # 【Opus修复】优先直接获取失败任务并单通重试（并发执行）
                failed_groups = _fetch_failed_tasks_for_retry(max_retries=3)
                if failed_groups:
                    all_failed_tasks = [t for tasks in failed_groups.values() for t in tasks]
                    print(f"🔄 发现 {len(all_failed_tasks)} 个失败任务，并发重试...")
                    
                    # 【P2修复】改为批量重试（3-5通/批），而非单个重试
                    retry_results = await _retry_tasks_batch(all_failed_tasks, batch_size=5)
                    
                    # 统计成功数
                    successful_retries = sum(1 for r in retry_results if r and isinstance(r, dict) and 'error' not in r)
                    total_processed += successful_retries
                    print(f"   ✅ 重试完成: {successful_retries}/{len(all_failed_tasks)} 成功")
                    continue  # 重试完成后继续主循环
                
                # 【P1修复】--once模式下，等待数据库写入完成后再检查processing任务
                if once:
                    # 【新增】等待异步写入队列消化完成，避免竞态
                    print("⏳ 等待数据库写入完成...")
                    await wait_for_db_writes_async(timeout=30)
                    print("✅ 数据库写入完成")
                    
                    conn = sqlite3.connect(QUEUE_DB_PATH)
                    cursor = conn.cursor()
                    cursor.execute("SELECT COUNT(*) FROM analysis_tasks WHERE status='processing'")
                    processing_count = cursor.fetchone()[0]
                    conn.close()
                    if processing_count > 0:
                        print(f"🔄 --once模式：还有 {processing_count} 个processing任务，重置为pending")
                        conn = sqlite3.connect(QUEUE_DB_PATH)
                        cursor = conn.cursor()
                        cursor.execute("UPDATE analysis_tasks SET status='pending', started_at=NULL WHERE status='processing'")
                        conn.commit()
                        conn.close()
                        continue
                    # 没有可重试任务且队列为空，可以安全退出
                    print("⏳ 队列已空，--once模式：准备退出")
                    break
                else:
                    # 非--once模式，继续等待新任务
                    print("⏳ 队列已空，等待新任务...")
                    await asyncio.sleep(2.0)
                continue
            
            # 收集所有任务（跨用户）
            all_tasks = []
            for user_id, user_tasks in groups.items():
                for task in user_tasks[:5]:
                    all_tasks.append(task)
            
            print(f"\n📦 获取 {len(all_tasks)} 个任务，来自 {len(groups)} 个用户")
            
            # 【修复】优先使用数据库中的 scene 字段，缺失时才重新分类
            print(f"\n🔍 对 {len(all_tasks)} 通会话进行场景检查...")
            for task in all_tasks:
                session_data = task.get('session_data', {})
                if isinstance(session_data, str):
                    session_data = json.loads(session_data)
                messages = session_data.get('messages', [])
                
                # 优先使用数据库持久化的 scene，缺失时才重新分类
                scene = task.get('scene')
                if not scene:
                    scene = classify_scene_by_keywords(messages)
                task['_scene'] = scene
            
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
        stop_db_writer()  # 【v2.6.5】停止数据库写入线程
        if cfg.classifier:
            cfg.classifier.close()
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
        subprocess.Popen(cmd, stdout=open(os.path.join(cfg.LOGS_DIR, 'worker.log'), 'a'), stderr=subprocess.STDOUT)
        print(f"🚀 工作进程已在后台启动 [{mode}模式]")
    else:
        if mode == 'async-batch':
            asyncio.run(run_async_batch_worker(
                max_groups=args.max_groups,
                max_batch_size=args.max_batch_size,
                window_minutes=args.window,
                score_batch_size=args.score_batch_size,
                once=args.once
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
