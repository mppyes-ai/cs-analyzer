"""批量评分模块 - 处理客服会话的批量评分和重试

包含功能：
- _batch_score_with_limit_v2: 主评分函数（自适应批量大小）
- _batch_score_with_limit: 旧版评分函数（已废弃，向后兼容）
- _retry_tasks_batch: 批量重试（并行化）
- _retry_single_task: 单通重试（降级处理）

Usage:
    from batch_scoring import _batch_score_with_limit_v2
    results = await _batch_score_with_limit_v2(tasks, base_batch_size=10)
"""

import asyncio
import json
from typing import Dict, List, Optional

import worker_config as cfg
from task_queue import fail_task


def _extract_session_data(task: Dict) -> Dict:
    """统一提取任务中的会话数据"""
    session_data = task.get('session_data', {})
    if isinstance(session_data, str):
        session_data = json.loads(session_data)
    return session_data


def _has_valid_scores(result: Dict) -> bool:
    """检查评分结果是否包含完整的结构化评分字段"""
    return (
        isinstance(result, dict) and
        'error' not in result and
        result.get('dimension_scores') is not None and
        result.get('summary') is not None
    )


def _needs_single_retry(result: Dict) -> bool:
    """识别需要降级为单通重试的批量结果"""
    return isinstance(result, dict) and bool(result.get('_needs_single_retry'))


def _result_error(result: Dict, default: str) -> str:
    """提取统一的错误信息，避免日志和数据库写入不一致"""
    if isinstance(result, dict):
        return result.get('error', default)
    return default


async def _resolve_batch_results(
    batch: List[Dict],
    batch_results: List[Dict],
    invalid_default_error: str,
    invalid_log_prefix: str,
) -> List[Dict]:
    """处理批量结果，并将结构性失败降级为单通重试"""
    from db_writer import queue_save_result

    final_results = list(batch_results)
    downgrade_items = []

    for idx, (task, result) in enumerate(zip(batch, batch_results)):
        if _needs_single_retry(result):
            downgrade_items.append((idx, task, result))
            print(f"   🔽 任务 {task['task_id']} 标记为单通降级")
            continue

        if _has_valid_scores(result):
            queue_save_result(task, result)
            continue

        error_msg = _result_error(result, invalid_default_error)
        print(f"   {invalid_log_prefix}: 任务 {str(task['task_id'])[:20]}... {error_msg}")
        fail_task(task['task_id'], error_msg)
        final_results[idx] = {'error': error_msg}

    if downgrade_items:
        print(f"   🔽 {len(downgrade_items)} 个任务降级为单通处理")
        single_results = await asyncio.gather(
            *[_retry_single_task(task) for _, task, _ in downgrade_items],
            return_exceptions=True
        )

        for (idx, task, marker), single_result in zip(downgrade_items, single_results):
            if isinstance(single_result, Exception):
                error_msg = f"单通降级异常: {single_result}"
                fail_task(task['task_id'], error_msg)
                print(f"   ❌ 单通降级异常: {error_msg[:100]}")
                final_results[idx] = {'error': error_msg}
            elif single_result is None:
                final_results[idx] = {'error': _result_error(marker, '单通降级失败')}
            else:
                final_results[idx] = single_result

    return final_results


async def _score_task_single(task: Dict, reason: str, default_error: str = '评分结果不完整') -> Dict:
    """对单个任务走单会话评分，绕开批量Prompt的不稳定性"""
    from db_writer import queue_save_result

    task_id = task['task_id']
    session_data = _extract_session_data(task)

    try:
        async with cfg.kimi_semaphore:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, cfg.scorer.score_session, session_data)

        if _has_valid_scores(result):
            queue_save_result(task, result)
            print(f"   ✅ 单通处理成功: 任务 {task_id} ({reason})")
            return result

        error_msg = _result_error(result, default_error)
        fail_task(task_id, error_msg)
        print(f"   ❌ 单通处理失败: 任务 {task_id} ({reason}) {error_msg[:100]}")
        return {'error': error_msg}

    except Exception as e:
        error_msg = f"{reason}异常: {e}"
        fail_task(task_id, error_msg)
        print(f"   ❌ 单通处理异常: 任务 {task_id} ({reason}) {str(e)[:100]}")
        return {'error': error_msg}


async def _batch_score_with_limit_v2(tasks: List[Dict], base_batch_size: int) -> List[Dict]:
    """【v2.6 Phase 2】带限流的批量评分（自适应批量大小）
    
    新增：
    1. 根据会话Token估算动态调整批量大小
    2. 超长会话自动降级，短会话自动扩容
    3. 确保不超过MAX_TOKENS_PER_BATCH安全上限
    """

    if not tasks:
        return []
    
    # 【优化A】拆分超短、超长和正常会话，降低异构批次对本地模型的干扰
    short_msg_threshold = cfg.SHORT_MSG_THRESHOLD
    oversized_msg_threshold = cfg.OVERSIZED_MSG_THRESHOLD
    short_tasks = []
    normal_tasks = []
    oversized_tasks = []
    
    for task in tasks:
        session_data = _extract_session_data(task)
        msg_count = len(session_data.get('messages', []))
        
        if msg_count < short_msg_threshold:
            short_tasks.append(task)
            print(f"   📦 BATCH_SPLIT|sid={task.get('session_id','')[:20]}|msgs={msg_count}|type=short(<{short_msg_threshold})")
        elif msg_count > oversized_msg_threshold:
            oversized_tasks.append(task)
            print(f"   📦 BATCH_SPLIT|sid={task.get('session_id','')[:20]}|msgs={msg_count}|type=oversized(>{oversized_msg_threshold})")
        else:
            normal_tasks.append(task)
    
    if short_tasks or oversized_tasks:
        print(
            f"   📦 BATCH_SPLIT|short={len(short_tasks)}|normal={len(normal_tasks)}|oversized={len(oversized_tasks)}"
        )
    
    all_results = []
    completed_count = 0
    total_batches = 0
    
    # ===== 处理正常会话（批量模式）=====
    if normal_tasks:
        # 【v2.6】自适应批量：计算最优批量大小
        sessions = [t['session_data'] for t in normal_tasks]
        optimal_batch_size = cfg.calculate_adaptive_batch_size(sessions, base_batch_size)
        
        print(f"\n   📊 自适应批量: 基础={base_batch_size}, 优化后={optimal_batch_size}, 正常任务={len(normal_tasks)}")
        
        # 按优化后的batch_size分组
        batches = [normal_tasks[i:i+optimal_batch_size] for i in range(0, len(normal_tasks), optimal_batch_size)]
        total_batches = len(batches)
        
        # 预估总token（用于日志）
        total_tokens = sum(cfg.estimate_session_tokens(s['session_data']) for s in normal_tasks)
        print(f"   💾 预估总Token: {total_tokens:,} (上限: {cfg.MAX_TOKENS_PER_BATCH:,})")
    
        async def score_one_batch(batch_idx: int, batch: List[Dict]) -> List[Dict]:
            """评分单个批次（内部使用信号量限流）"""
            batch_tokens = sum(cfg.estimate_session_tokens(t['session_data']) for t in batch)
            print(f"   🔄 批次 {batch_idx+1}/{total_batches} ({len(batch)}通, ~{batch_tokens:,}tokens) 启动")
            
            async with cfg.kimi_semaphore:
                batch_sessions = [t['session_data'] for t in batch]
                
                # 【v2.5】构建预分析结果（从task的_scene字段）
                pre_analyses = []
                for task in batch:
                    # 【修复】优先使用数据库中的 scene 字段（已持久化）
                    scene = task.get('scene', task.get('_scene', '售前阶段'))
                    task['_scene'] = scene  # 确保内部字段也设置
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
                batch_results = await cfg.scorer.score_sessions_batch_async(batch_sessions, pre_analyses)

            resolved_results = await _resolve_batch_results(
                batch,
                batch_results,
                invalid_default_error='评分结果不完整（缺少dimension_scores或summary）',
                invalid_log_prefix=f"⚠️ 任务批次 {batch_idx+1} 评分无效",
            )

            print(f"   ✅ 批次 {batch_idx+1}/{total_batches} 完成")
            return resolved_results
        
        # 【Opus修复】使用 as_completed 替代 gather，避免木桶效应
        print(f"\n🚀 启动 {total_batches} 个评分批次（并发限制: {cfg.KIMI_MAX_CONCURRENT}，as_completed优化）")
        
        # 【修复】创建Task对象，as_completed会按完成顺序返回
        batch_tasks = [score_one_batch(i, batch) for i, batch in enumerate(batches)]
        
        # as_completed: 先完成的先处理，避免等待最慢批次
        for task in asyncio.as_completed(batch_tasks):
            try:
                batch_results = await task
                all_results.extend(batch_results)
                completed_count += 1
                print(f"   📊 进度: {completed_count}/{total_batches} 批次完成")
            except Exception as e:
                print(f"   ❌ 批次异常: {e}")
                completed_count += 1
    
    # ===== 处理超短会话（降级为单通）=====
    if short_tasks:
        print(f"\n   📦 短会话单通处理: {len(short_tasks)} 个")
        short_results = await asyncio.gather(*[
            _score_task_single(task, reason='短会话单通处理')
            for task in short_tasks
        ])
        all_results.extend(short_results)

    # ===== 处理超长会话（降级为单通串行）=====
    if oversized_tasks:
        print(f"\n   📦 超长会话单通处理: {len(oversized_tasks)} 个")
        oversized_results = await asyncio.gather(*[
            _score_task_single(task, reason='超长会话单通处理', default_error='超长会话评分无效')
            for task in oversized_tasks
        ])
        all_results.extend(oversized_results)
    
    return all_results


async def _batch_score_with_limit(tasks: List[Dict], batch_size: int) -> List[Dict]:
    """【N-6】带限流的批量评分（旧版，已废弃）
    
    ⚠️ Deprecated: 请使用 _batch_score_with_limit_v2()，支持自适应批量大小
    
    保留原因：向后兼容，部分旧代码可能调用此函数
    """
    import warnings
    warnings.warn(
        "_batch_score_with_limit is deprecated, use _batch_score_with_limit_v2 instead",
        DeprecationWarning,
        stacklevel=2
    )

    if not tasks:
        return []
    
    # 按batch_size分组
    batches = [tasks[i:i+batch_size] for i in range(0, len(tasks), batch_size)]
    all_results = []
    
    for batch_idx, batch in enumerate(batches):
        print(f"   🔄 处理批次 {batch_idx+1}/{len(batches)} ({len(batch)}通)")
        
        # 使用信号量控制并发
        async with cfg.kimi_semaphore:
            sessions = [t['session_data'] for t in batch]
            
            # 执行批量评分
            batch_results = await cfg.scorer.score_sessions_batch_async(sessions)
            
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
                    from db_operations import _save_result_sync
                    await loop.run_in_executor(None, _save_result_sync, task, result)
                else:
                    error_msg = result.get('error', '评分结果不完整（缺少dimension_scores或summary）')
                    print(f"   ⚠️ 任务 {str(task['task_id'])[:20]}... 评分无效: {error_msg}")
                    fail_task(task['task_id'], error_msg)
            
            all_results.extend(batch_results)
    
    return all_results


async def _retry_tasks_batch(tasks: List[Dict], batch_size: int = 5) -> List[Dict]:
    """【v2.6.6-fix】批量重试——并行化，消除串行瓶颈
    
    核心变更：
    - 使用 asyncio.gather 并发执行所有重试批次
    - 每个批次独立使用信号量限流
    - 30个失败任务从串行12分钟 → 并行2-3分钟
    
    Args:
        tasks: 失败任务列表
        batch_size: 每批重试任务数（默认5通/批）
        
    Returns:
        重试结果列表
    """

    if not tasks:
        return []
    
    print(f"   🔄 批量重试 {len(tasks)} 个任务，{batch_size}通/批，并发执行")
    
    # 按batch_size分组
    batches = [tasks[i:i+batch_size] for i in range(0, len(tasks), batch_size)]
    all_results = []
    
    async def retry_one_batch(batch_idx: int, batch: List[Dict]) -> List[Dict]:
        """重试单个批次（内部使用信号量限流）"""
        print(f"   🔄 重试批次 {batch_idx+1}/{len(batches)} ({len(batch)}通) 启动")
        
        batch_results = []
        try:
            async with cfg.kimi_semaphore:
                # 构建批量会话
                batch_sessions = [t['session_data'] for t in batch]
                pre_analyses = []
                for task in batch:
                    session_data = task['session_data']
                    if isinstance(session_data, str):
                        session_data = json.loads(session_data)
                    pre_analyses.append({
                        'scene': session_data.get('scene', '售前阶段'),
                        'sub_scene': '其他',
                        'intent': '咨询',
                        'sentiment': 'neutral',
                        'confidence': 0.8,
                        'reasoning': '重试任务批量评分',
                        'source': 'retry_batch'
                    })
                
                # 调用评分
                results = await cfg.scorer.score_sessions_batch_async(batch_sessions, pre_analyses)
                
            resolved_results = await _resolve_batch_results(
                batch,
                results,
                invalid_default_error='评分结果不完整',
                invalid_log_prefix="❌ 重试失败",
            )
            for task, result in zip(batch, resolved_results):
                if _has_valid_scores(result):
                    print(f"   ✅ 重试成功: 任务 {task['task_id']}")
                else:
                    print(f"   ❌ 重试失败: {_result_error(result, '评分结果不完整')[:100]}")
            batch_results.extend(resolved_results)
                        
        except Exception as e:
            print(f"   ❌ 重试批次 {batch_idx+1} 异常: {e}")
            for task in batch:
                fail_task(task['task_id'], str(e))
                batch_results.append({'error': str(e)})
        
        print(f"   ✅ 重试批次 {batch_idx+1}/{len(batches)} 完成")
        return batch_results
    
    # 【核心修复】并发执行所有重试批次
    print(f"\n🚀 启动 {len(batches)} 个重试批次并发（信号量限制: {cfg.KIMI_MAX_CONCURRENT}）")
    
    batch_tasks = [retry_one_batch(i, batch) for i, batch in enumerate(batches)]
    
    # 使用 gather 并发执行，return_exceptions=True 避免一个批次失败影响其他
    batch_results_list = await asyncio.gather(*batch_tasks, return_exceptions=True)
    
    # 合并结果
    for batch_idx, result in enumerate(batch_results_list):
        if isinstance(result, Exception):
            print(f"   ❌ 重试批次 {batch_idx+1} 整体失败: {result}")
            # 标记该批次所有任务失败
            for task in batches[batch_idx]:
                fail_task(task['task_id'], str(result))
                all_results.append({'error': str(result)})
        else:
            all_results.extend(result)
    
    success_count = sum(1 for r in all_results if 'error' not in r)
    fail_count = len(all_results) - success_count
    print(f"\n📊 重试完成: {success_count}成功, {fail_count}失败")
    
    return all_results


async def _retry_single_task(task: Dict) -> Optional[Dict]:
    """【Opus修复】单通重试——避免批次连锁失败
    
    当整批失败时，降级为单通评分，给每个任务更长的超时和独立的错误处理
    
    Args:
        task: 任务字典
        
    Returns:
        评分结果，失败返回 None
    """

    task_id = task['task_id']
    retry_count = task.get('retry_count', 0)
    print(f"   🔄 重试任务 {task_id} (第{retry_count + 1}次重试)")

    result = await _score_task_single(task, reason='重试任务单通评分')
    return result if _has_valid_scores(result) else None
