#!/usr/bin/env python3
"""测试单条会话分析，复现错误"""

import os
import sys
import json

# 加载.env文件
from dotenv import load_dotenv
load_dotenv()

sys.path.insert(0, os.path.dirname(__file__))

from smart_scoring_v2 import SmartScoringEngine

# 测试数据
session_data = {
    "session_id": "session_0001_a19d1f2f",
    "user_id": "一切or一切",
    "staff_name": "林内林小永",
    "messages": [
        {"role": "user", "sender": "一切or一切", "timestamp": "2026-03-12 09:18:04", "content": "在不在不"},
        {"role": "staff", "sender": "林内林小永", "timestamp": "2026-03-12 09:18:13", "content": "人工客服【林小期】将为您竭诚服务。"},
        {"role": "user", "sender": "一切or一切", "timestamp": "2026-03-12 09:18:21", "content": "热水器我可以先下单"},
        {"role": "staff", "sender": "林内林小永", "timestamp": "2026-03-12 09:18:30", "content": "请问有什么可以帮到您呢~"},
        {"role": "user", "sender": "一切or一切", "timestamp": "2026-03-12 09:18:39", "content": "一个月后配送吗？"},
        {"role": "staff", "sender": "林内林小永", "timestamp": "2026-03-12 09:18:48", "content": "您看的这款是京东物流配送延迟发货方案：\n\n下单时您自助选择下'期望送达日期'即可成功延迟发货。"},
        {"role": "user", "sender": "一切or一切", "timestamp": "2026-03-12 09:19:16", "content": "那送达时间到时还能再更改吗？"},
        {"role": "user", "sender": "一切or一切", "timestamp": "2026-03-12 09:19:37", "content": "现在房子还没装修好"},
        {"role": "staff", "sender": "林内林小永", "timestamp": "2026-03-12 09:19:40", "content": "到时候联系我们就可以了哦"},
        {"role": "staff", "sender": "林内林小永", "timestamp": "2026-03-12 09:22:40", "content": "亲爱哒，您咨询的这款JSQ31-GD31燃气热水器30分钟内下单购买，安装晒单后可享限量【膳魔师电饭煲】，赠品库存有限，先到先得哦~"},
        {"role": "staff", "sender": "林内林小永", "timestamp": "2026-03-12 09:25:40", "content": "政府补贴立减15%限时开启随时可能会结束-\n-\n请抓紧时间享受政府补贴优惠下单付款啦，结束就没有啦~"},
        {"role": "staff", "sender": "林内林小永", "timestamp": "2026-03-12 09:28:40", "content": "您好，还没有收到您的订单，有什么顾虑吗，可以给我说下的呢，非常乐意帮您解决的哈～"},
        {"role": "staff", "sender": "林内林小永", "timestamp": "2026-03-12 09:30:11", "content": "宝，喜欢就赶紧下单吧！"}
    ],
    "start_time": "2026-03-12 09:18:04",
    "end_time": "2026-03-12 09:30:11"
}

# 获取API Key
api_key = os.getenv('MOONSHOT_API_KEY')
if not api_key:
    print("❌ 未找到MOONSHOT_API_KEY")
    sys.exit(1)

print("🔄 初始化评分引擎...")
scorer = SmartScoringEngine(api_key=api_key)

print("🔄 开始分析会话...")
try:
    result = scorer.score_session(session_data)
    print("✅ 分析成功!")
    print(json.dumps(result, ensure_ascii=False, indent=2))
except Exception as e:
    print(f"❌ 分析失败: {e}")
    import traceback
    traceback.print_exc()
