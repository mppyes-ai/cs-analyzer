#!/usr/bin/env python3
import sqlite3
import json
import re
import os

def clean_html(content):
    if not content:
        return content
    content = content.replace('<br/>', '\n').replace('<br>', '\n')
    content = re.sub(r'<[^>]+>', '', content)
    content = content.replace('&nbsp;', ' ').replace('&lt;', '<').replace('&gt;', '>')
    content = content.replace('&amp;', '&').replace('&quot;', '"')
    content = re.sub(r'\n\s*\n+', '\n\n', content)
    content = '\n'.join(line.strip() for line in content.split('\n'))
    return content.strip()

def fix_cs_analyzer_db():
    db_path = os.path.join(os.path.dirname(__file__), 'data', 'cs_analyzer_new.db')
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # 清理 messages 字段中的HTML
    cursor.execute('SELECT session_id, messages FROM sessions')
    rows = cursor.fetchall()
    
    updated = 0
    for row in rows:
        session_id, messages_json = row
        messages = json.loads(messages_json)
        
        modified = False
        for msg in messages:
            if '<' in msg.get('content', ''):
                msg['content'] = clean_html(msg['content'])
                modified = True
        
        if modified:
            cursor.execute('UPDATE sessions SET messages = ? WHERE session_id = ?',
                          (json.dumps(messages, ensure_ascii=False), session_id))
            updated += 1
            print(f'Updated: {session_id}')
    
    conn.commit()
    conn.close()
    print(f'\n✅ 已更新 {updated} 个会话')

if __name__ == '__main__':
    fix_cs_analyzer_db()
