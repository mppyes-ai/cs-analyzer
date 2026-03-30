#!/usr/bin/env python3
import sqlite3
import json
import re
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

def clean_html(content):
    if not content:
        return content
    # Replace HTML line breaks
    content = content.replace('<br/>', '\n').replace('<br>', '\n')
    # Remove HTML tags
    content = re.sub(r'<[^>]+>', '', content)
    # Decode HTML entities
    content = content.replace('&nbsp;', ' ').replace('&lt;', '<').replace('&gt;', '>')
    content = content.replace('&amp;', '&').replace('&quot;', '"')
    # Normalize newlines
    content = re.sub(r'\n\s*\n+', '\n\n', content)
    content = '\n'.join(line.strip() for line in content.split('\n'))
    return content.strip()

def update_existing_data():
    db_path = os.path.join(os.path.dirname(__file__), 'data', 'task_queue.db')
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    cursor.execute('SELECT task_id, session_data FROM analysis_tasks')
    rows = cursor.fetchall()
    
    updated = 0
    for row in rows:
        task_id, session_data_json = row
        data = json.loads(session_data_json)
        
        modified = False
        for msg in data.get('messages', []):
            if '<' in msg.get('content', ''):
                msg['content'] = clean_html(msg['content'])
                modified = True
        
        if modified:
            cursor.execute('UPDATE analysis_tasks SET session_data = ? WHERE task_id = ?',
                          (json.dumps(data, ensure_ascii=False), task_id))
            updated += 1
    
    conn.commit()
    conn.close()
    print(f'✅ 已更新 {updated} 个任务')

if __name__ == '__main__':
    update_existing_data()
