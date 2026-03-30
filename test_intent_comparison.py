#!/usr/bin/env python3
"""意图分类对比测试 - 关键词规则 vs Qwen3:4b

对比两种分类方式的效果差异。

用法: python test_intent_comparison.py
"""

import sys
import json

sys.path.insert(0, '.')

from smart_scoring_v2 import SmartScoringEngine
from intent_classifier import LocalIntentClassifier

def print_comparison(messages, title):
    """打印对比结果"""
    print("\n" + "=" * 70)
    print(f"📝 测试用例: {title}")
    print("=" * 70)
    
    # 显示会话内容
    print("\n【会话内容】")
    for msg in messages[:4]:  # 最多显示4条
        role = "👤" if msg.get('role') == 'user' else "👨‍💼"
        print(f"{role} {msg.get('content', '')[:50]}...")
    
    # 1. 关键词规则分类
    print("\n【关键词规则分类】")
    engine = SmartScoringEngine(use_local_intent=False)  # 禁用本地模型
    keyword_result = engine._analyze_session_keyword_fallback(messages)
    print(f"  场景: {keyword_result['scene']}")
    print(f"  意图: {keyword_result['intent']}")
    print(f"  情绪: {keyword_result['sentiment']}")
    print(f"  置信度: {keyword_result['confidence']}")
    print(f"  来源: {keyword_result.get('source', 'unknown')}")
    
    # 2. Qwen3:4b分类
    print("\n【Qwen3:4b分类】")
    classifier = LocalIntentClassifier(model="qwen2.5:7b")
    qwen_result = classifier.classify(messages)
    
    if qwen_result:
        print(f"  场景: {qwen_result.scene} / {qwen_result.sub_scene}")
        print(f"  意图: {qwen_result.intent}")
        print(f"  情绪: {qwen_result.sentiment}")
        print(f"  置信度: {qwen_result.confidence}")
        print(f"  来源: qwen3:4b")
        print(f"  理由: {qwen_result.reasoning}")
    else:
        print("  ❌ 分类失败")
    
    print("\n【差异对比】")
    if qwen_result:
        scene_match = keyword_result['scene'] == qwen_result.scene
        intent_match = keyword_result['intent'] == qwen_result.intent
        sentiment_match = keyword_result['sentiment'] == qwen_result.sentiment
        
        print(f"  场景一致: {'✅' if scene_match else '❌'} (关键词:{keyword_result['scene']} vs Qwen:{qwen_result.scene})")
        print(f"  意图一致: {'✅' if intent_match else '❌'} (关键词:{keyword_result['intent']} vs Qwen:{qwen_result.intent})")
        print(f"  情绪一致: {'✅' if sentiment_match else '❌'} (关键词:{keyword_result['sentiment']} vs Qwen:{qwen_result.sentiment})")

def main():
    """主函数"""
    print("🧪 意图分类对比测试")
    print("对比: 关键词规则 vs Qwen3:4b本地模型")
    
    # 测试用例1: 客诉场景
    test1 = [
        {"role": "user", "content": "你们不是骗子吗？主播说的和客服说的不一样"},
        {"role": "staff", "content": "小主您可以以直播间主播的规则为准呢"},
        {"role": "user", "content": "那就是你们客服说的不算？"},
        {"role": "user", "content": "你只会重复这句话吗？"}
    ]
    print_comparison(test1, "客诉-质疑欺诈")
    
    # 测试用例2: 售前咨询
    test2 = [
        {"role": "user", "content": "这款热水器多少钱？有什么功能？"},
        {"role": "staff", "content": "亲，这款16升零冷水热水器目前售价2999元，支持水量伺服和智能恒温"},
        {"role": "user", "content": "有没有优惠活动？国补能用吗？"},
        {"role": "staff", "content": "现在购买可以享受政府补贴15%，到手价2549元"}
    ]
    print_comparison(test2, "售前-产品咨询")
    
    # 测试用例3: 安装咨询
    test3 = [
        {"role": "user", "content": "安装需要提前准备什么？尺寸有什么要求？"},
        {"role": "staff", "content": "需要预留排烟孔直径60mm，冷热水管距离..."},
        {"role": "user", "content": "辅材要自己买吗？大概多少钱？"},
        {"role": "staff", "content": "辅材可以用我们的，角阀一对50元，烟管..."}
    ]
    print_comparison(test3, "安装-预埋咨询")
    
    # 测试用例4: 售后维修
    test4 = [
        {"role": "user", "content": "热水器显示E1错误，不出热水了"},
        {"role": "staff", "content": "E1通常是点火失败，请问气源正常吗？"},
        {"role": "user", "content": "气有的，就是点不着火"},
        {"role": "staff", "content": "可能是点火器故障，建议安排上门检修"}
    ]
    print_comparison(test4, "售后-故障报修")
    
    print("\n" + "=" * 70)
    print("✅ 对比测试完成")
    print("=" * 70)
    print("\n💡 观察要点:")
    print("  1. Qwen3:4b是否能识别更细微的语义差异？")
    print("  2. 二级场景分类是否更准确？")
    print("  3. 情绪识别是否更精准？")
    print("  4. 分类理由是否合理可解释？")

if __name__ == "__main__":
    main()
