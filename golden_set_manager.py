"""Golden Set管理 - 人工标注基准数据 + MAE计算

功能：
1. 人工标注会话的4维度分数（作为标准答案）
2. 计算AI评分 vs 人工评分的MAE（平均绝对误差）
3. 统计各维度准确率

用法: python3 golden_set_manager.py [command] [options]
"""

import sys
import os
import json
import argparse
from datetime import datetime
from typing import List, Dict, Tuple

sys.path.insert(0, os.path.dirname(__file__))

from db_utils import get_connection

# ========== 数据模型 ==========

def init_golden_set_table():
    """初始化Golden Set表"""
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS golden_set (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT UNIQUE NOT NULL,
            
            -- 人工标注分数（标准答案）
            human_professionalism INTEGER,
            human_standardization INTEGER,
            human_policy_execution INTEGER,
            human_conversion INTEGER,
            human_total INTEGER,
            
            -- AI评分（v2引擎）
            ai_professionalism INTEGER,
            ai_standardization INTEGER,
            ai_policy_execution INTEGER,
            ai_conversion INTEGER,
            ai_total INTEGER,
            
            -- 标注信息
            annotated_by TEXT DEFAULT 'admin',
            annotated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            
            -- 备注
            notes TEXT
        )
    """)
    
    # 创建索引
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_golden_session ON golden_set(session_id)")
    
    conn.commit()
    conn.close()
    print("✅ Golden Set表初始化完成")

# ========== 标注功能 ==========

def annotate_session(session_id: str, scores: Dict[str, int], 
                     annotated_by: str = 'admin', notes: str = '') -> bool:
    """人工标注单条会话
    
    Args:
        session_id: 会话ID
        scores: 分数字典 {professionalism, standardization, policy_execution, conversion}
        annotated_by: 标注人
        notes: 备注
        
    Returns:
        是否成功
    """
    init_golden_set_table()
    conn = get_connection()
    cursor = conn.cursor()
    
    # 计算总分
    total = sum([
        scores.get('professionalism', 3),
        scores.get('standardization', 3),
        scores.get('policy_execution', 3),
        scores.get('conversion', 3)
    ])
    
    # 获取AI评分（从sessions表）
    cursor.execute("""
        SELECT professionalism_score, standardization_score, 
               policy_execution_score, conversion_score, total_score
        FROM sessions WHERE session_id = ?
    """, (session_id,))
    
    row = cursor.fetchone()
    if not row:
        print(f"⚠️ 会话不存在: {session_id}")
        conn.close()
        return False
    
    ai_prof, ai_stan, ai_pol, ai_conv, ai_total = row
    
    # 插入或更新
    cursor.execute("""
        INSERT OR REPLACE INTO golden_set (
            session_id,
            human_professionalism, human_standardization, 
            human_policy_execution, human_conversion, human_total,
            ai_professionalism, ai_standardization,
            ai_policy_execution, ai_conversion, ai_total,
            annotated_by, annotated_at, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), ?)
    """, (
        session_id,
        scores.get('professionalism'),
        scores.get('standardization'),
        scores.get('policy_execution'),
        scores.get('conversion'),
        total,
        ai_prof, ai_stan, ai_pol, ai_conv, ai_total,
        annotated_by, notes
    ))
    
    conn.commit()
    conn.close()
    
    print(f"✅ 已标注: {session_id}")
    print(f"  人工: {scores}/20 | AI: {ai_total}/20")
    return True

def batch_annotate(session_ids: List[str], default_scores: Dict[str, int] = None):
    """批量标注（交互式）
    
    Args:
        session_ids: 会话ID列表
        default_scores: 默认分数
    """
    init_golden_set_table()
    
    print(f"📋 开始批量标注，共 {len(session_ids)} 条会话")
    print("=" * 60)
    print("输入格式: p,s,pol,c (如: 4,3,4,2)")
    print("跳过输入: s 或 skip")
    print("退出: q 或 quit")
    print("=" * 60)
    
    for idx, session_id in enumerate(session_ids):
        print(f"\n[{idx+1}/{len(session_ids)}] 会话: {session_id}")
        
        # 获取会话摘要
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT summary FROM sessions WHERE session_id = ?", (session_id,))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            print(f"摘要: {row[0]}")
        
        # 获取当前AI评分
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT professionalism_score, standardization_score,
                   policy_execution_score, conversion_score, total_score
            FROM sessions WHERE session_id = ?
        """, (session_id,))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            prof, stan, pol, conv, total = row
            print(f"当前AI评分: 专业{prof}/5 标准{stan}/5 政策{pol}/5 转化{conv}/5 总分{total}/20")
        
        # 交互输入
        while True:
            user_input = input("请输入人工评分 (p,s,pol,c): ").strip().lower()
            
            if user_input in ['q', 'quit']:
                print("退出标注")
                return
            
            if user_input in ['s', 'skip']:
                print("跳过")
                break
            
            try:
                parts = user_input.split(',')
                if len(parts) == 4:
                    scores = {
                        'professionalism': int(parts[0]),
                        'standardization': int(parts[1]),
                        'policy_execution': int(parts[2]),
                        'conversion': int(parts[3])
                    }
                    
                    # 验证分数范围
                    for dim, score in scores.items():
                        if not 1 <= score <= 5:
                            print(f"⚠️ {dim} 分数必须在1-5之间")
                            continue
                    
                    annotate_session(session_id, scores)
                    break
                else:
                    print("⚠️ 格式错误，请输入4个数字用逗号分隔")
            except ValueError:
                print("⚠️ 输入无效，请输入数字")

# ========== MAE计算 ==========

def calculate_mae() -> Dict:
    """计算MAE（平均绝对误差）
    
    Returns:
        MAE统计结果
    """
    init_golden_set_table()
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT 
            human_professionalism, human_standardization,
            human_policy_execution, human_conversion, human_total,
            ai_professionalism, ai_standardization,
            ai_policy_execution, ai_conversion, ai_total
        FROM golden_set
    """)
    
    rows = cursor.fetchall()
    conn.close()
    
    if not rows:
        return {"error": "Golden Set为空，请先标注数据"}
    
    # 计算各维度MAE
    dims = ['professionalism', 'standardization', 'policy_execution', 'conversion', 'total']
    mae_results = {}
    
    for idx, dim in enumerate(dims):
        human_scores = [row[idx] for row in rows if row[idx] is not None]
        ai_scores = [row[idx + 5] for row in rows if row[idx + 5] is not None]
        
        if len(human_scores) != len(ai_scores) or len(human_scores) == 0:
            mae_results[dim] = None
            continue
        
        # 计算绝对误差
        abs_errors = [abs(h - a) for h, a in zip(human_scores, ai_scores)]
        mae = sum(abs_errors) / len(abs_errors)
        
        # 计算准确率（完全一致的比例）
        exact_match = sum(1 for e in abs_errors if e == 0) / len(abs_errors)
        
        # 计算容忍度内的比例（误差<=1）
        tolerance_match = sum(1 for e in abs_errors if e <= 1) / len(abs_errors)
        
        mae_results[dim] = {
            'mae': round(mae, 2),
            'exact_match': round(exact_match, 2),
            'tolerance_match': round(tolerance_match, 2),
            'sample_count': len(human_scores)
        }
    
    return mae_results

def print_mae_report():
    """打印MAE报告"""
    results = calculate_mae()
    
    if 'error' in results:
        print(f"⚠️ {results['error']}")
        return
    
    print("\n" + "=" * 60)
    print("📊 MAE评估报告（AI评分 vs 人工标准）")
    print("=" * 60)
    
    dim_names = {
        'professionalism': '专业性',
        'standardization': '标准化',
        'policy_execution': '政策执行',
        'conversion': '转化能力',
        'total': '总分'
    }
    
    for dim, data in results.items():
        if data:
            print(f"\n【{dim_names.get(dim, dim)}】")
            print(f"  MAE: {data['mae']} 分")
            print(f"  完全一致率: {data['exact_match']*100:.1f}%")
            print(f"  容差内比例(±1): {data['tolerance_match']*100:.1f}%")
            print(f"  样本数: {data['sample_count']}")
    
    print("\n" + "=" * 60)
    print("💡 解读:")
    print("  - MAE < 0.5: 优秀")
    print("  - MAE 0.5-1.0: 良好")
    print("  - MAE 1.0-1.5: 一般")
    print("  - MAE > 1.5: 需要优化")
    print("=" * 60)

def export_golden_set(output_file: str = "golden_set.json"):
    """导出Golden Set到JSON文件
    
    Args:
        output_file: 输出文件路径
    """
    init_golden_set_table()
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM golden_set")
    rows = cursor.fetchall()
    columns = [desc[0] for desc in cursor.description]
    
    data = []
    for row in rows:
        record = dict(zip(columns, row))
        data.append(record)
    
    conn.close()
    
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    print(f"✅ Golden Set已导出: {output_file} ({len(data)}条)")

# ========== 主函数 ==========

def main():
    parser = argparse.ArgumentParser(description='Golden Set管理和MAE计算')
    subparsers = parser.add_subparsers(dest='command', help='子命令')
    
    # annotate 子命令
    annotate_parser = subparsers.add_parser('annotate', help='人工标注')
    annotate_parser.add_argument('--session_id', type=str, help='指定单个会话标注')
    annotate_parser.add_argument('--scores', type=str, help='分数 (格式: p,s,pol,c 如: 4,3,4,2)')
    annotate_parser.add_argument('--batch', action='store_true', help='批量交互式标注')
    annotate_parser.add_argument('--limit', type=int, help='批量标注数量限制')
    
    # mae 子命令
    mae_parser = subparsers.add_parser('mae', help='计算MAE')
    
    # export 子命令
    export_parser = subparsers.add_parser('export', help='导出Golden Set')
    export_parser.add_argument('--output', type=str, default='golden_set.json', help='输出文件')
    
    args = parser.parse_args()
    
    if args.command == 'annotate':
        if args.session_id and args.scores:
            # 单条标注
            parts = args.scores.split(',')
            if len(parts) == 4:
                scores = {
                    'professionalism': int(parts[0]),
                    'standardization': int(parts[1]),
                    'policy_execution': int(parts[2]),
                    'conversion': int(parts[3])
                }
                annotate_session(args.session_id, scores)
            else:
                print("⚠️ 分数格式错误，请使用: p,s,pol,c")
        elif args.batch:
            # 批量标注
            from db_utils import load_sessions
            df = load_sessions()
            if args.limit:
                df = df.head(args.limit)
            batch_annotate(df['session_id'].tolist())
        else:
            annotate_parser.print_help()
    
    elif args.command == 'mae':
        print_mae_report()
    
    elif args.command == 'export':
        export_golden_set(args.output)
    
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
