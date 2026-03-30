#!/usr/bin/env python3
"""五层漏斗架构统计分析工具

用于查看意图分类来源分布，评估各层拦截效果。

用法:
    python3 funnel_stats.py          # 查看当前统计
    python3 funnel_stats.py --reset  # 重置统计（新测试周期）

作者: 小虾米
更新: 2026-03-21
"""

import sqlite3
import os
import json
import argparse
from collections import defaultdict

# 队列数据库路径
QUEUE_DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'task_queue.db')


def get_funnel_stats():
    """获取五层漏斗统计"""
    
    if not os.path.exists(QUEUE_DB_PATH):
        print(f"❌ 队列数据库不存在: {QUEUE_DB_PATH}")
        return None
    
    conn = sqlite3.connect(QUEUE_DB_PATH)
    cursor = conn.cursor()
    
    # 获取所有已完成任务的result字段
    cursor.execute('''
        SELECT result, session_id 
        FROM analysis_tasks 
        WHERE status = 'completed' 
        AND result IS NOT NULL
    ''')
    
    results = cursor.fetchall()
    conn.close()
    
    if not results:
        print("⚠️ 暂无完成的任务数据")
        return None
    
    # 统计各来源
    source_stats = defaultdict(lambda: {'count': 0, 'sessions': []})
    total = 0
    
    for result_json, session_id in results:
        try:
            result = json.loads(result_json)
            intent = result.get('intent', {})
            source = intent.get('source', 'unknown')
            
            source_stats[source]['count'] += 1
            source_stats[source]['sessions'].append(session_id[:20])
            total += 1
        except:
            source_stats['parse_error']['count'] += 1
    
    return source_stats, total


def print_funnel_report(source_stats, total):
    """打印漏斗报告"""
    
    # 来源映射（层级名称）
    source_names = {
        'rule': '第一层: 规则匹配 (毫秒级)',
        'extended_keyword': '第二层: 扩展关键词',
        'sentiment_analyzer': '第三层: 情绪分析 (Qwen2.5:7b)',
        'qwen2.5': '第四层: Qwen2.5:7b语义分类',
        'keyword_fallback': '第五层: 关键词回退 (保底)',
        'unknown': '未知来源'
    }
    
    # 按预期漏斗顺序排序
    funnel_order = ['rule', 'extended_keyword', 'sentiment_analyzer', 'qwen2.5', 'keyword_fallback', 'unknown']
    
    print("\n" + "=" * 60)
    print("📊 五层漏斗架构统计分析")
    print("=" * 60)
    print(f"\n总任务数: {total}")
    print()
    
    print(f"{'层级':<35} {'数量':<8} {'占比':<10} {'说明'}")
    print("-" * 80)
    
    for source in funnel_order:
        if source in source_stats:
            data = source_stats[source]
            count = data['count']
            pct = count / total * 100 if total > 0 else 0
            name = source_names.get(source, source)
            
            # 标记是否使用模型
            uses_model = '✅ 用模型' if source in ['sentiment_analyzer', 'qwen2.5'] else ''
            
            print(f"{name:<35} {count:<8} {pct:>5.1f}%    {uses_model}")
    
    print("-" * 80)
    
    # 计算模型使用率
    model_usage = sum(source_stats[s]['count'] for s in ['sentiment_analyzer', 'qwen2.5'] if s in source_stats)
    model_pct = model_usage / total * 100 if total > 0 else 0
    
    print(f"\n🤖 Qwen2.5:7b模型实际使用率: {model_usage}/{total} ({model_pct:.1f}%)")
    print(f"⚡ 规则/关键词拦截率: {100-model_pct:.1f}%")
    
    # 设计目标对比
    print("\n📈 与设计目标对比:")
    print(f"   规则拦截目标: ~60%  实际: {source_stats.get('rule', {}).get('count', 0)/total*100:.1f}%")
    print(f"   扩展关键词目标: ~15%  实际: {source_stats.get('extended_keyword', {}).get('count', 0)/total*100:.1f}%")
    print(f"   Qwen2.5:7b兜底目标: ~25%  实际: {model_pct:.1f}%")
    
    print("\n" + "=" * 60)


def reset_stats():
    """重置统计（清空队列）"""
    import shutil
    if os.path.exists(QUEUE_DB_PATH):
        backup_path = QUEUE_DB_PATH + '.backup'
        shutil.copy2(QUEUE_DB_PATH, backup_path)
        print(f"✅ 已备份队列数据库: {backup_path}")
        
        # 清空任务表但保留结构
        conn = sqlite3.connect(QUEUE_DB_PATH)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM analysis_tasks')
        conn.commit()
        conn.close()
        print("✅ 已清空任务表，开始新的统计周期")
    else:
        print("❌ 队列数据库不存在")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='五层漏斗架构统计工具')
    parser.add_argument('--reset', action='store_true', help='重置统计（开始新测试周期）')
    args = parser.parse_args()
    
    if args.reset:
        reset_stats()
    else:
        result = get_funnel_stats()
        if result:
            source_stats, total = result
            print_funnel_report(source_stats, total)
