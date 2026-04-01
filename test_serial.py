#!/usr/bin/env python3
"""串行模式测试5条失败数据"""

import os
import sys
import json
from dotenv import load_dotenv
load_dotenv()

sys.path.insert(0, os.path.dirname(__file__))

from smart_scoring_v2 import SmartScoringEngine

# 5条测试数据
session_ids = [
    "session_0000_c214080e",
    "session_0001_a19d1f2f", 
    "session_0002_91fa042d",
    "session_0003_eb32e1b5",
    "session_0004_c16e40a2"
]

# 手动填入第一条数据（实际从数据库读取）
test_data_0 = {
    "session_id": "session_0000_c214080e",
    "user_id": "凡尘依旧0",
    "staff_name": "林内林小爽",
    "messages": [
        {"role": "user", "sender": "凡尘依旧0", "timestamp": "2026-03-12 09:20:17", "content": "安装需要自己准备哪些东西"},
        {"role": "staff", "sender": "林内林小爽", "timestamp": "2026-03-12 09:20:22", "content": "人工客服【林小爽】将为您竭诚服务。"},
        {"role": "staff", "sender": "林内林小爽", "timestamp": "2026-03-12 09:20:24", "content": "目前林内厂家是会配送部分机器通用辅材的，不过您购买机器后仍需根据家中实际情况准备些辅材。\n=\n具体每款型号配送辅材和需自备辅材说明如图："},
        {"role": "staff", "sender": "林内林小爽", "timestamp": "2026-03-12 09:20:24", "content": "https://dd-static.jd.com/ddimg/jfs/t1/400743/10/19002/273747/69b214d8Fc480a4cd/022f6a64c3d047f4.png"},
        {"role": "staff", "sender": "林内林小爽", "timestamp": "2026-03-12 09:23:24", "content": "亲爱哒，您咨询的这款JSQ31-GD73燃气热水器30分钟内下单购买，安装晒单后可享限量【飞利浦空气炸锅】，赠品库存有限，先到先得哦~"},
        {"role": "user", "sender": "凡尘依旧0", "timestamp": "2026-03-12 09:24:50", "content": "已经买过的送赠品么"},
        {"role": "staff", "sender": "林内林小爽", "timestamp": "2026-03-12 09:25:13", "content": "目前为您服务的是【燃热售前解答专员】，为更专业快速解决您的问题【马上为您转接专业售后专员】请留意窗口变化~"}
    ],
    "start_time": "2026-03-12 09:20:17",
    "end_time": "2026-03-12 09:25:13"
}

api_key = os.getenv('MOONSHOT_API_KEY')
scorer = SmartScoringEngine(api_key=api_key)

print("=" * 60)
print("串行模式测试 - 第 1/5 条")
print(f"Session ID: {test_data_0['session_id']}")
print("=" * 60)

try:
    result = scorer.score_session(test_data_0)
    print("✅ 成功!")
    print(f"总分: {result.get('summary', {}).get('total_score', 'N/A')}")
    print(f"风险: {result.get('summary', {}).get('risk_level', 'N/A')}")
except Exception as e:
    print(f"❌ 失败: {e}")
    import traceback
    traceback.print_exc()
