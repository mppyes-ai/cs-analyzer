#!/usr/bin/env python3
"""
scene_utils.py - 场景分类工具模块

零外部依赖的轻量场景分类函数，用于 worker.py 和 batch_analyzer.py
避免重复定义 _fast_scene_classify 函数。

作者: 小虾米
创建: 2026-04-17
版本: 1.0.0
"""

from typing import List, Dict, Optional


def classify_scene_by_keywords(messages: List[Dict]) -> str:
    """
    基于关键词快速分类客服会话场景
    
    通过分析会话前3条消息内容，识别用户意图所属的业务阶段。
    优先级: 客诉处理 > 售后阶段 > 售中阶段 > 售前阶段
    
    Args:
        messages: 消息列表，每条消息包含 'content' 字段
        
    Returns:
        str: 场景分类结果
            - '客诉处理': 投诉、差评、退货、维权
            - '售后阶段': 安装、维修、故障、售后、保修
            - '售中阶段': 订单、发货、物流、快递、配送
            - '售前阶段': 默认分类，产品咨询、价格询问等
            
    Example:
        >>> messages = [{'content': '我的订单什么时候发货？'}]
        >>> classify_scene_by_keywords(messages)
        '售中阶段'
    """
    # 提取前3条消息内容并合并（通常包含用户开场白）
    text = ' '.join([m.get('content', '') for m in messages[:3]]).lower()
    
    # 客诉关键词（优先级最高 - 用户情绪负面，需要优先处理）
    complaint_keywords = ['投诉', '差评', '退货', '退款', '维权', '举报', 
                          '欺骗', '欺诈', '虚假宣传', '态度差']
    if any(kw in text for kw in complaint_keywords):
        return '客诉处理'
    
    # 售后关键词（产品使用中遇到问题）
    aftersales_keywords = ['安装', '维修', '故障', '坏了', '不出热水', 
                           '售后', '保修', '质保', '维修点', '上门']
    if any(kw in text for kw in aftersales_keywords):
        return '售后阶段'
    
    # 售中关键词（订单履约相关）
    sales_keywords = ['订单', '发货', '物流', '快递', '配送', '到哪了',
                      '多久到', '催单', '改地址', '签收', '揽收']
    if any(kw in text for kw in sales_keywords):
        return '售中阶段'
    
    # 默认售前阶段（产品咨询、购买意向）
    return '售前阶段'


def classify_scene_by_intent(intent_str: str) -> str:
    """
    基于意图字符串分类场景（备用方法）
    
    当有关键词分类结果时，可作为二次确认或备用方案
    
    Args:
        intent_str: 意图描述字符串
        
    Returns:
        str: 场景分类
    """
    intent_lower = intent_str.lower()
    
    if any(k in intent_lower for k in ['投诉', '维权', '差评', '退货']):
        return '客诉处理'
    elif any(k in intent_lower for k in ['安装', '维修', '售后', '故障']):
        return '售后阶段'
    elif any(k in intent_lower for k in ['订单', '物流', '发货', '配送']):
        return '售中阶段'
    else:
        return '售前阶段'


def get_scene_priority(scene: str) -> int:
    """
    获取场景优先级数值
    
    用于任务排序：客诉 > 售后 > 售中 > 售前
    
    Args:
        scene: 场景名称
        
    Returns:
        int: 优先级数值（越小优先级越高）
    """
    priority_map = {
        '客诉处理': 1,
        '售后阶段': 2,
        '售中阶段': 3,
        '售前阶段': 4
    }
    return priority_map.get(scene, 4)


# 向后兼容别名（用于平滑迁移）
_fast_scene_classify = classify_scene_by_keywords


if __name__ == "__main__":
    # 简单测试
    test_cases = [
        [{'content': '我想投诉你们的产品质量问题'}],
        [{'content': '热水器坏了怎么维修'}],
        [{'content': '我的订单什么时候发货'}],
        [{'content': '这个型号有什么功能'}],
    ]
    
    print("场景分类测试:")
    for msgs in test_cases:
        scene = classify_scene_by_keywords(msgs)
        print(f"  输入: {msgs[0]['content'][:20]}... -> {scene}")
