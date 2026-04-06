async def _batch_score_with_limit_v2(tasks: List[Dict], batch_size: int) -> List[Dict]:
    """【v2.5修复】带限流的批量评分（真正并行）
    
    修复前：for循环内串行，信号量无用
    修复后：asyncio.gather并行，所有批次同时竞争信号量
    """
    global kimi_semaphore
    
    if not tasks:
        return []
    
    # 按batch_size分组
    batches = [tasks[i:i+batch_size] for i in range(0, len(tasks), batch_size)]
    total_batches = len(batches)
    
    async def score_one_batch(batch_idx: int, batch: List[Dict]) -> List[Dict]:
        """评分单个批次（内部使用信号量限流）"""
        print(f"   🔄 批次 {batch_idx+1}/{total_batches} ({len(batch)}通) 启动")
        
        async with kimi_semaphore:
            sessions = [t['session_data'] for t in batch]
            
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
            batch_results = await scorer.score_sessions_batch_async(sessions, pre_analyses)
            
            # 保存结果到数据库（在线程池中执行）
            loop = asyncio.get_event_loop()
            for task, result in zip(batch, batch_results):
                if 'error' not in result:
                    await loop.run_in_executor(None, _save_result_sync, task, result)
                    complete_task(task['task_id'], result)
                else:
                    fail_task(task['task_id'], result.get('error', '评分失败'))
            
            print(f"   ✅ 批次 {batch_idx+1}/{total_batches} 完成")
            return batch_results
    
    # 【修复核心】所有批次同时启动，真正并行竞争信号量
    print(f"\n🚀 启动 {total_batches} 个评分批次（并发限制: {KIMI_MAX_CONCURRENT}）")
    all_results_nested = await asyncio.gather(*[
        score_one_batch(i, batch) for i, batch in enumerate(batches)
    ])
    
    # 展平结果
    all_results = []
    for batch_results in all_results_nested:
        all_results.extend(batch_results)
    
    return all_results
