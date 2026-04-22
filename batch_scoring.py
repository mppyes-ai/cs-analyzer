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


async def _batch_score_with_limit_v2(tasks: List[Dict], base_batch_size: int) -> List[Dict]:
    """【v2.6 Phase 2】带限流的批量评分（自适应批量大小）
    
    新增：
    1. 根据会话Token估算动态调整批量大小
    2. 超长会话自动降级，短会话自动扩容
    3. 确保不超过MAX_TOKENS_PER_BATCH安全上限
    """

    if not tasks:
        return []
    
    # 【优化A】拆分超长会话和正常会话
    OVERSIZED_MSG_THRESHOLD = 100  # 超过100条消息视为超长会话
    normal_tasks = []
    oversized_tasks = []
    
    for task in tasks:
        session_data = task.get('session_data', {})
        if isinstance(session_data, str):
            session_data = json.loads(session_data)
        msg_count = len(session_data.get('messages', []))
        
        if msg_count > OVERSIZED_MSG_THRESHOLD:
            oversized_tasks.append(task)
            print(f"   📦 BATCH_SPLIT|sid={task.get('session_id','')[:20]}|msgs={msg_count}|type=oversized(>{OVERSIZED_MSG_THRESHOLD})")
        else:
            normal_tasks.append(task)
    
    if oversized_tasks:
        print(f"   📦 BATCH_SPLIT|normal={len(normal_tasks)}|oversized={len(oversized_tasks)}")
    
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
                
                # 【v2.6.5】异步写入队列：非阻塞入队，由后台线程处理数据库写入
                # 局部导入避免循环导入
                from db_writer import queue_save_result
                
                for task, result in zip(batch, batch_results):
                    # 【Bug修复】检查result是否包含有效的评分字段
                    has_valid_scores = (
                        'error' not in result and
                        result.get('dimension_scores') is not None and
                        result.get('summary') is not None
                    )
                    if has_valid_scores:
                        queue_save_result(task, result)  # 非阻塞入队
                    else:
                        error_msg = result.get('error', '评分结果不完整（缺少dimension_scores或summary）')
                        print(f"   ⚠️ 任务 {str(task['task_id'])[:20]}... 评分无效: {error_msg}")
                        fail_task(task['task_id'], error_msg)
                
                print(f"   ✅ 批次 {batch_idx+1}/{total_batches} 完成")
                return batch_results
        
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
    
    # ===== 处理超长会话（降级为单通串行）=====
    for task in oversized_tasks:
        print(f"   📦 OVERSIZED|sid={task.get('session_id','')[:20]}|降级为单通处理")
        try:
            session_data = task.get('session_data', {})
            if isinstance(session_data, str):
                session_data = json.loads(session_data)
            
            async with cfg.kimi_semaphore:
                pre_analysis = {
                    'scene': session_data.get('scene', '售前阶段'),
                    'sub_scene': '其他',
                    'intent': '咨询',
                    'sentiment': 'neutral',
                    'confidence': 0.8,
                    'reasoning': '超长会话降级为单通处理',
                    'source': 'oversized_fallback'
                }
                
                results = await cfg.scorer.score_sessions_batch_async([session_data], [pre_analysis])
                result = results[0] if results else {'error': '空结果'}
                
                has_valid_scores = (
                    'error' not in result and
                    result.get('dimension_scores') is not None and
                    result.get('summary') is not None
                )
                
                if has_valid_scores:
                    from db_writer import queue_save_result
                    queue_save_result(task, result)
                    all_results.append(result)
                else:
                    error_msg = result.get('error', '超长会话评分无效')
                    fail_task(task['task_id'], error_msg)
                    all_results.append({'error': error_msg})
                    
        except Exception as e:
            error_msg = f"超长会话处理异常: {str(e)}"
            fail_task(task['task_id'], error_msg)
            all_results.append({'error': error_msg})
    
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
                
                # 保存结果
                loop = asyncio.get_event_loop()
                for task, result in zip(batch, results):
                    has_valid_scores = (
                        'error' not in result and
                        result.get('dimension_scores') is not None and
                        result.get('summary') is not None
                    )
                    if has_valid_scores:
                        from db_writer import queue_save_result
                        queue_save_result(task, result)  # 【Bug修复】使用异步写入队列，与主路径一致
                        print(f"   ✅ 重试成功: 任务 {task['task_id']}")
                        batch_results.append(result)
                    else:
                        error_msg = result.get('error', '评分结果不完整')
                        fail_task(task['task_id'], error_msg)
                        print(f"   ❌ 重试失败: {error_msg[:100]}")
                        batch_results.append({'error': error_msg})
                        
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
    session_data = task['session_data']
    retry_count = task.get('retry_count', 0)
    
    print(f"   🔄 重试任务 {task_id} (第{retry_count + 1}次重试)")
    
    try:
        async with cfg.kimi_semaphore:
            # 【Opus修复】单通评分，超时设置更宽松（600秒）
            batch_sessions = [session_data]
            
            # 构建预分析
            pre_analysis = {
                'scene': session_data.get('scene', '售前阶段'),
                'sub_scene': '其他',
                'intent': '咨询',
                'sentiment': 'neutral',
                'confidence': 0.8,
                'reasoning': '重试任务单通评分',
                'source': 'retry_single'
            }
            
            # 调用评分（单通）
            results = await cfg.scorer.score_sessions_batch_async(
                batch_sessions, 
                [pre_analysis]
            )
            result = results[0] if results else {'error': '空结果'}
            
            # 检查结果有效性
            has_valid_scores = (
                'error' not in result and
                result.get('dimension_scores') is not None and
                result.get('summary') is not None
            )
            
            if has_valid_scores:
                # 【Bug修复】使用异步写入队列，与主路径一致
                from db_writer import queue_save_result
                queue_save_result(task, result)
                print(f"   ✅ 重试成功: 任务 {task_id}")
                return result
            else:
                error_msg = result.get('error', '评分结果不完整')
                fail_task(task_id, error_msg)
                print(f"   ❌ 重试失败: {error_msg[:100]}")
                return None
                
    except Exception as e:
        error_msg = str(e)
        fail_task(task_id, error_msg)
        print(f"   ❌ 重试异常: {error_msg[:100]}")
        return None
