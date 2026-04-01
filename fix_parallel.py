#!/usr/bin/env python3
with open('worker.py', 'r') as f:
    content = f.read()

# Fix _save_result_parallel to include transfer fields
old_insert = '''        cursor.execute('''
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
        ))'''

new_insert = '''        # 获取转接字段
        is_transfer = session_data.get('is_transfer', False)
        transfer_from = session_data.get('transfer_from', '')
        transfer_to = session_data.get('transfer_to', '')
        transfer_reason = session_data.get('transfer_reason', '')
        related_sessions = session_data.get('related_sessions', [])
        
        cursor.execute('''
            INSERT OR REPLACE INTO sessions 
            (session_id, user_id, staff_name, messages, summary, 
             professionalism_score, standardization_score, policy_execution_score, conversion_score,
             total_score, analysis_json, strengths, issues, suggestions, session_count, start_time, end_time, created_at,
             is_transfer, transfer_from, transfer_to, transfer_reason, related_sessions)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?)
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
            datetime.now().isoformat(),
            1 if is_transfer else 0,
            transfer_from,
            transfer_to,
            transfer_reason,
            json.dumps(related_sessions, ensure_ascii=False)
        ))'''

content = content.replace(old_insert, new_insert)

with open('worker.py', 'w') as f:
    f.write(content)

print("Fixed _save_result_parallel with transfer fields")