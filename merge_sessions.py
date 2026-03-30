"""
会话合并工具 - CS-Analyzer v2

合并规则：
1. 同一用户（user_id）
2. 同一客服（staff_name）
3. 时间间隔 < MERGE_WINDOW_MINUTES（默认30分钟）
4. 合并后保留最早的 session_id，session_count 累加

用法:
    python merge_sessions.py              # 执行合并
    python merge_sessions.py --dry-run    # 预览合并结果，不实际执行
    python merge_sessions.py --window 60  # 设置合并窗口为60分钟
"""

import sqlite3
import json
from datetime import datetime, timedelta
from collections import defaultdict
import argparse
import os

# 数据库路径
DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'cs_analyzer_new.db')
MERGE_WINDOW_MINUTES = 30  # 默认合并窗口：30分钟


def get_connection():
    """获取数据库连接"""
    return sqlite3.connect(DB_PATH)


def parse_timestamp(ts_str):
    """解析时间字符串"""
    if not ts_str:
        return None
    try:
        # 尝试多种格式
        formats = [
            '%Y-%m-%d %H:%M:%S',
            '%Y-%m-%d %H:%M',
            '%Y-%m-%dT%H:%M:%S',
            '%Y-%m-%dT%H:%M:%S.%f',
        ]
        for fmt in formats:
            try:
                return datetime.strptime(str(ts_str)[:19], fmt)
            except:
                continue
        return None
    except:
        return None


def get_all_sessions():
    """获取所有会话数据"""
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT session_id, user_id, staff_name, messages, 
               professionalism_score, standardization_score, 
               policy_execution_score, conversion_score, total_score,
               analysis_json, strengths, issues, suggestions, session_count,
               start_time, end_time, summary
        FROM sessions
        ORDER BY user_id, start_time
    ''')
    
    sessions = []
    for row in cursor.fetchall():
        sessions.append({
            'session_id': row[0],
            'user_id': row[1],
            'staff_name': row[2],
            'messages': json.loads(row[3]) if row[3] else [],
            'professionalism_score': row[4],
            'standardization_score': row[5],
            'policy_execution_score': row[6],
            'conversion_score': row[7],
            'total_score': row[8],
            'analysis_json': json.loads(row[9]) if row[9] else {},
            'strengths': json.loads(row[10]) if row[10] else [],
            'issues': json.loads(row[11]) if row[11] else [],
            'suggestions': json.loads(row[12]) if row[12] else [],
            'session_count': row[13] or 1,
            'start_time': row[14],
            'end_time': row[15],
            'summary': row[16]
        })
    
    conn.close()
    return sessions


def find_merge_groups(sessions, window_minutes=MERGE_WINDOW_MINUTES):
    """
    找出可以合并的会话组
    
    返回: [(主session_id, [被合并session_id列表]), ...]
    """
    # 按 user_id + staff_name 分组
    groups = defaultdict(list)
    for s in sessions:
        key = (s['user_id'], s['staff_name'])
        groups[key].append(s)
    
    merge_groups = []
    
    for key, group_sessions in groups.items():
        if len(group_sessions) < 2:
            continue
        
        # 按开始时间排序
        sorted_sessions = sorted(group_sessions, 
                                 key=lambda x: parse_timestamp(x['start_time']) or datetime.min)
        
        # 找出时间间隔 < window_minutes 的连续会话
        current_group = [sorted_sessions[0]]
        
        for i in range(1, len(sorted_sessions)):
            prev_session = current_group[-1]
            curr_session = sorted_sessions[i]
            
            prev_end = parse_timestamp(prev_session['end_time'])
            curr_start = parse_timestamp(curr_session['start_time'])
            
            if prev_end and curr_start:
                gap = (curr_start - prev_end).total_seconds() / 60
                
                if gap <= window_minutes:
                    # 可以合并
                    current_group.append(curr_session)
                else:
                    # 间隔太大，保存当前组，开始新组
                    if len(current_group) > 1:
                        merge_groups.append((current_group[0], current_group[1:]))
                    current_group = [curr_session]
            else:
                # 时间解析失败，视为不能合并
                if len(current_group) > 1:
                    merge_groups.append((current_group[0], current_group[1:]))
                current_group = [curr_session]
        
        # 保存最后一组
        if len(current_group) > 1:
            merge_groups.append((current_group[0], current_group[1:]))
    
    return merge_groups


def merge_session_data(main_session, sub_sessions):
    """
    合并会话数据
    
    策略：
    - 消息列表：按时间顺序合并
    - 分数：取平均值
    - strengths/issues/suggestions：合并去重
    - session_count：累加
    """
    # 合并消息列表
    all_messages = main_session['messages'].copy()
    for sub in sub_sessions:
        all_messages.extend(sub['messages'])
    
    # 按时间排序
    all_messages.sort(key=lambda x: parse_timestamp(x.get('timestamp', '')) or datetime.min)
    
    # 计算平均分数
    total_sessions = main_session['session_count'] + sum(s['session_count'] for s in sub_sessions)
    
    def avg_score(field):
        total = main_session[field] * main_session['session_count']
        for sub in sub_sessions:
            total += sub[field] * sub['session_count']
        return round(total / total_sessions)
    
    # 合并亮点/问题/建议（去重）
    def merge_lists(field):
        result = set(main_session[field])
        for sub in sub_sessions:
            result.update(sub[field])
        return list(result)
    
    # 更新时间
    start_times = [main_session['start_time']] + [s['start_time'] for s in sub_sessions]
    end_times = [main_session['end_time']] + [s['end_time'] for s in sub_sessions]
    
    valid_start = [parse_timestamp(t) for t in start_times if parse_timestamp(t)]
    valid_end = [parse_timestamp(t) for t in end_times if parse_timestamp(t)]
    
    new_start_time = min(valid_start).isoformat()[:19] if valid_start else main_session['start_time']
    new_end_time = max(valid_end).isoformat()[:19] if valid_end else main_session['end_time']
    
    merged = {
        'session_id': main_session['session_id'],
        'messages': all_messages,
        'professionalism_score': avg_score('professionalism_score'),
        'standardization_score': avg_score('standardization_score'),
        'policy_execution_score': avg_score('policy_execution_score'),
        'conversion_score': avg_score('conversion_score'),
        'total_score': avg_score('total_score'),
        'strengths': merge_lists('strengths'),
        'issues': merge_lists('issues'),
        'suggestions': merge_lists('suggestions'),
        'session_count': total_sessions,
        'start_time': new_start_time,
        'end_time': new_end_time,
        'summary': main_session['summary']  # 保留主会话摘要
    }
    
    return merged


def execute_merge(merge_groups, dry_run=False):
    """
    执行合并操作
    
    Args:
        merge_groups: [(主session, [被合并sessions]), ...]
        dry_run: 如果True，只预览不执行
    """
    conn = get_connection()
    cursor = conn.cursor()
    
    merged_count = 0
    deleted_count = 0
    
    for main_session, sub_sessions in merge_groups:
        main_id = main_session['session_id']
        sub_ids = [s['session_id'] for s in sub_sessions]
        
        # 合并数据
        merged_data = merge_session_data(main_session, sub_sessions)
        
        print(f"\n{'='*60}")
        print(f"合并组: {main_id}")
        print(f"  主会话: {main_id} (原有 {main_session['session_count']} 个)")
        for sub in sub_sessions:
            print(f"  合并入: {sub['session_id']} ({sub['session_count']} 个)")
        print(f"  合并后: {merged_data['session_count']} 个会话")
        print(f"  消息数: {len(merged_data['messages'])} 条")
        print(f"  时间范围: {merged_data['start_time']} ~ {merged_data['end_time']}")
        
        if not dry_run:
            # 更新主会话
            cursor.execute('''
                UPDATE sessions SET
                    messages = ?,
                    professionalism_score = ?,
                    standardization_score = ?,
                    policy_execution_score = ?,
                    conversion_score = ?,
                    total_score = ?,
                    strengths = ?,
                    issues = ?,
                    suggestions = ?,
                    session_count = ?,
                    start_time = ?,
                    end_time = ?
                WHERE session_id = ?
            ''', (
                json.dumps(merged_data['messages'], ensure_ascii=False),
                merged_data['professionalism_score'],
                merged_data['standardization_score'],
                merged_data['policy_execution_score'],
                merged_data['conversion_score'],
                merged_data['total_score'],
                json.dumps(merged_data['strengths'], ensure_ascii=False),
                json.dumps(merged_data['issues'], ensure_ascii=False),
                json.dumps(merged_data['suggestions'], ensure_ascii=False),
                merged_data['session_count'],
                merged_data['start_time'],
                merged_data['end_time'],
                main_id
            ))
            
            # 删除被合并的会话
            for sub_id in sub_ids:
                cursor.execute('DELETE FROM sessions WHERE session_id = ?', (sub_id,))
                deleted_count += 1
            
            merged_count += 1
    
    if not dry_run:
        conn.commit()
    conn.close()
    
    return merged_count, deleted_count


def main():
    parser = argparse.ArgumentParser(description='会话合并工具')
    parser.add_argument('--dry-run', action='store_true', 
                        help='预览模式，不实际执行合并')
    parser.add_argument('--window', type=int, default=MERGE_WINDOW_MINUTES,
                        help=f'合并窗口时间（分钟），默认{MERGE_WINDOW_MINUTES}分钟')
    
    args = parser.parse_args()
    
    print("="*60)
    print("🔄 会话合并工具")
    print("="*60)
    print(f"合并窗口: {args.window} 分钟")
    print(f"模式: {'预览' if args.dry_run else '实际执行'}")
    print()
    
    # 获取所有会话
    sessions = get_all_sessions()
    print(f"📊 当前会话总数: {len(sessions)}")
    
    # 找出可合并的组
    merge_groups = find_merge_groups(sessions, args.window)
    
    if not merge_groups:
        print("\n✅ 没有发现需要合并的会话")
        return
    
    print(f"\n🔍 发现 {len(merge_groups)} 个合并组")
    
    # 执行合并
    merged_count, deleted_count = execute_merge(merge_groups, args.dry_run)
    
    print("\n" + "="*60)
    print("📋 合并结果")
    print("="*60)
    print(f"合并组数: {merged_count}")
    print(f"删除会话: {deleted_count}")
    if not args.dry_run:
        remaining = len(sessions) - deleted_count
        print(f"剩余会话: {remaining}")
    
    if args.dry_run:
        print("\n⚠️ 这是预览模式，未实际执行合并")
        print("   执行合并请运行: python merge_sessions.py")


if __name__ == '__main__':
    main()
