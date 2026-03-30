import os
import sys
from dotenv import load_dotenv
load_dotenv()

api_key = os.getenv('MOONSHOT_API_KEY')

# 逐条处理，带详细输出
import sqlite3
import pandas as pd
from rule_extractor_v2 import process_correction_to_rule

conn = sqlite3.connect('./data/cs_analyzer_new.db')
df = pd.read_sql_query("""
    SELECT c.correction_id, c.status, c.session_id 
    FROM corrections c
    LEFT JOIN rules r ON c.correction_id = r.source_correction_id
    WHERE r.rule_id IS NULL
    ORDER BY c.correction_id
""", conn)
conn.close()

print(f'待处理记录: {len(df)} 条')
print()

for idx, row in df.iterrows():
    cid = row['correction_id']
    status = row['status'] or 'unknown'
    print(f'[{idx+1}/{len(df)}] 处理 #{cid} (status: {status})...', flush=True)
    
    try:
        rule_id = process_correction_to_rule(cid, api_key, skip_similar=True)
        result = rule_id if rule_id else '跳过/失败'
        print(f'  结果: {result}')
    except Exception as e:
        print(f'  错误: {e}')
        import traceback
        traceback.print_exc()
    print()

print('处理完成')
