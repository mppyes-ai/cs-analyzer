#!/usr/bin/env python3
"""
转接会话分析模块 - 识别和分析客服转接场景

功能：
1. 识别转接关键词，标记转接会话
2. 建立同一用户的会话关联链
3. 评估转接质量
4. 合并分析相关会话

作者: 小虾米
更新: 2026-03-22
"""

import re
import json
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass


@dataclass
class TransferInfo:
    """转接信息"""
    is_transfer: bool              # 是否转接会话
    transfer_from: Optional[str]   # 来源会话ID
    transfer_to: Optional[str]     # 目标会话ID
    transfer_reason: str           # 转接原因
    transfer_time: Optional[str]   # 转接时间
    related_sessions: List[str]    # 关联会话列表


# 转接关键词库
TRANSFER_KEYWORDS = {
    "售前转售后": [
        "转接售后", "为您转接", "转给售后", "售后专员", 
        "安排售后", "售后同事", "售后客服"
    ],
    "售后转售前": [
        "转接售前", "销售同事", "售前咨询"
    ],
    "升级处理": [
        "升级处理", "主管", "经理", "高级客服", "专家"
    ],
    "部门转接": [
        "转接", "转给", "转到", "其他部门"
    ]
}


def detect_transfer(messages: List[Dict]) -> Tuple[bool, str, Optional[str]]:
    """
    检测会话是否包含转接
    
    Args:
        messages: 消息列表
        
    Returns:
        (是否转接, 转接原因, 转接时间)
    """
    content = ' '.join([m.get('content', '') for m in messages])
    
    for reason, keywords in TRANSFER_KEYWORDS.items():
        for kw in keywords:
            if kw in content:
                # 找到转接时间（包含转接关键词的消息时间）
                transfer_time = None
                for msg in messages:
                    if kw in msg.get('content', ''):
                        transfer_time = msg.get('timestamp')
                        break
                return True, reason, transfer_time
    
    return False, "", None


def find_related_sessions(sessions: List[Dict], target_session: Dict) -> List[str]:
    """
    查找与目标会话相关的其他会话（同一用户，时间相近）
    
    Args:
        sessions: 所有会话列表
        target_session: 目标会话
        
    Returns:
        相关会话ID列表
    """
    related = []
    target_user = target_session.get('user_id')
    target_time_str = target_session.get('start_time') or target_session.get('messages', [{}])[0].get('timestamp')
    
    if not target_user or not target_time_str:
        return related
    
    try:
        target_time = datetime.strptime(target_time_str, '%Y-%m-%d %H:%M:%S')
    except:
        return related
    
    # 查找同一用户在5分钟内的其他会话
    time_window = timedelta(minutes=5)
    
    for session in sessions:
        if session.get('session_id') == target_session.get('session_id'):
            continue
            
        if session.get('user_id') != target_user:
            continue
        
        session_time_str = session.get('start_time') or session.get('messages', [{}])[0].get('timestamp')
        if not session_time_str:
            continue
            
        try:
            session_time = datetime.strptime(session_time_str, '%Y-%m-%d %H:%M:%S')
            time_diff = abs((session_time - target_time).total_seconds())
            
            # 如果在5分钟窗口内
            if time_diff <= 300:  # 5分钟 = 300秒
                related.append(session.get('session_id'))
        except:
            continue
    
    return related


def analyze_transfer_chain(sessions: List[Dict], user_id: str) -> List[List[str]]:
    """
    分析用户的会话链（可能有多次转接）
    
    Args:
        sessions: 所有会话列表
        user_id: 用户ID
        
    Returns:
        会话链列表，每个链是按时间排序的会话ID列表
    """
    # 过滤出该用户的所有会话
    user_sessions = [s for s in sessions if s.get('user_id') == user_id]
    
    if len(user_sessions) <= 1:
        return [[s.get('session_id')] for s in user_sessions]
    
    # 按时间排序
    def get_time(s):
        time_str = s.get('start_time') or s.get('messages', [{}])[0].get('timestamp')
        try:
            return datetime.strptime(time_str, '%Y-%m-%d %H:%M:%S')
        except:
            return datetime.min
    
    user_sessions.sort(key=get_time)
    
    # 构建会话链（时间差<5分钟的合并为一条链）
    chains = []
    current_chain = [user_sessions[0].get('session_id')]
    
    for i in range(1, len(user_sessions)):
        prev_time = get_time(user_sessions[i-1])
        curr_time = get_time(user_sessions[i])
        
        if curr_time - prev_time <= timedelta(minutes=5):
            current_chain.append(user_sessions[i].get('session_id'))
        else:
            chains.append(current_chain)
            current_chain = [user_sessions[i].get('session_id')]
    
    if current_chain:
        chains.append(current_chain)
    
    return chains


def calculate_transfer_quality(
    source_session: Dict, 
    target_session: Dict
) -> Dict:
    """
    计算转接质量指标
    
    Args:
        source_session: 来源会话（转接前）
        target_session: 目标会话（转接后）
        
    Returns:
        转接质量指标字典
    """
    metrics = {
        "transfer_response_time": None,      # 转接响应时间（秒）
        "info_completeness_score": 3,        # 信息完整性评分（1-5）
        "user_wait_time": None,              # 用户等待时间（秒）
        "repeat_question_count": 0,          # 重复询问次数
        "has_transfer_explanation": 0,       # 是否有转接说明
        "overall_transfer_score": 3,         # 总体评分（1-5）
        "notes": ""
    }
    
    source_messages = source_session.get('messages', [])
    target_messages = target_session.get('messages', [])
    
    if not source_messages or not target_messages:
        return metrics
    
    # 1. 计算转接响应时间（来源会话最后一条消息到目标会话第一条消息）
    try:
        source_end_time = datetime.strptime(
            source_messages[-1].get('timestamp', ''), 
            '%Y-%m-%d %H:%M:%S'
        )
        target_start_time = datetime.strptime(
            target_messages[0].get('timestamp', ''), 
            '%Y-%m-%d %H:%M:%S'
        )
        metrics["transfer_response_time"] = (
            target_start_time - source_end_time
        ).total_seconds()
    except:
        pass
    
    # 2. 检查是否有转接说明（来源会话最后几条是否包含转接解释）
    source_content = ' '.join([m.get('content', '') for m in source_messages[-3:]])
    explanation_keywords = ["转接", "售后", "专员", "同事", "为您安排"]
    if any(kw in source_content for kw in explanation_keywords):
        metrics["has_transfer_explanation"] = 1
    
    # 3. 检查重复询问（目标会话是否重复问来源会话已回答的问题）
    # 提取来源会话已回答的关键信息
    source_info = []
    for msg in source_messages:
        if msg.get('role') == 'user':
            source_info.append(msg.get('content', ''))
    
    # 检查目标会话开头是否重复询问
    repeat_count = 0
    for msg in target_messages[:3]:  # 只看前3条
        if msg.get('role') == 'user':
            user_q = msg.get('content', '')
            # 如果问题在来源会话中已经问过
            if any(q in user_q or user_q in q for q in source_info):
                repeat_count += 1
    
    metrics["repeat_question_count"] = repeat_count
    
    # 4. 计算总体评分
    score = 3  # 基础分
    
    # 转接及时性
    if metrics["transfer_response_time"] is not None:
        if metrics["transfer_response_time"] < 60:  # 1分钟内
            score += 1
        elif metrics["transfer_response_time"] > 300:  # 超过5分钟
            score -= 1
    
    # 信息传递
    if metrics["has_transfer_explanation"]:
        score += 0.5
    
    # 重复询问扣分
    score -= metrics["repeat_question_count"] * 0.5
    
    metrics["overall_transfer_score"] = max(1, min(5, int(score)))
    
    return metrics


def save_transfer_info_to_db(session_id: str, transfer_info: TransferInfo, db_path: str = None):
    """
    保存转接信息到数据库
    
    Args:
        session_id: 会话ID
        transfer_info: 转接信息
        db_path: 数据库路径
    """
    import sqlite3
    import os
    
    if db_path is None:
        db_path = os.path.join(os.path.dirname(__file__), "data", "cs_analyzer_new.db")
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    cursor.execute("""
        UPDATE sessions 
        SET is_transfer = ?,
            transfer_from = ?,
            transfer_to = ?,
            transfer_reason = ?,
            related_sessions = ?,
            transfer_time = ?
        WHERE session_id = ?
    """, (
        1 if transfer_info.is_transfer else 0,
        transfer_info.transfer_from,
        transfer_info.transfer_to,
        transfer_info.transfer_reason,
        json.dumps(transfer_info.related_sessions, ensure_ascii=False),
        transfer_info.transfer_time,
        session_id
    ))
    
    conn.commit()
    conn.close()


def save_transfer_quality_to_db(
    session_id: str, 
    transfer_from_session: str,
    metrics: Dict,
    db_path: str = None
):
    """
    保存转接质量指标
    
    Args:
        session_id: 目标会话ID
        transfer_from_session: 来源会话ID
        metrics: 质量指标
        db_path: 数据库路径
    """
    import sqlite3
    import os
    
    if db_path is None:
        db_path = os.path.join(os.path.dirname(__file__), "data", "cs_analyzer_new.db")
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    cursor.execute("""
        INSERT INTO transfer_quality_metrics 
        (session_id, transfer_from_session, transfer_response_time, 
         info_completeness_score, user_wait_time, repeat_question_count,
         has_transfer_explanation, overall_transfer_score, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        session_id,
        transfer_from_session,
        metrics.get("transfer_response_time"),
        metrics.get("info_completeness_score"),
        metrics.get("user_wait_time"),
        metrics.get("repeat_question_count"),
        metrics.get("has_transfer_explanation"),
        metrics.get("overall_transfer_score"),
        metrics.get("notes", "")
    ))
    
    conn.commit()
    conn.close()


if __name__ == "__main__":
    # 测试
    print("🧪 转接会话分析模块测试\n")
    
    # 测试1：检测转接关键词
    test_messages = [
        {"role": "staff", "content": "您好，我为您转接售后专员处理", "timestamp": "2026-03-22 09:17:00"}
    ]
    is_transfer, reason, time = detect_transfer(test_messages)
    print(f"【测试1】转接检测")
    print(f"  是否转接: {is_transfer}")
    print(f"  转接原因: {reason}")
    print(f"  转接时间: {time}")
    print()
    
    # 测试2：转接质量评估
    source = {
        "messages": [
            {"role": "user", "content": "我的订单有问题", "timestamp": "2026-03-22 09:16:00"},
            {"role": "staff", "content": "我为您转接售后", "timestamp": "2026-03-22 09:16:30"}
        ]
    }
    target = {
        "messages": [
            {"role": "staff", "content": "您好，我是售后专员", "timestamp": "2026-03-22 09:16:45"},
            {"role": "user", "content": "我的订单有问题", "timestamp": "2026-03-22 09:17:00"}
        ]
    }
    quality = calculate_transfer_quality(source, target)
    print(f"【测试2】转接质量评估")
    print(f"  转接响应时间: {quality['transfer_response_time']}秒")
    print(f"  是否有转接说明: {'是' if quality['has_transfer_explanation'] else '否'}")
    print(f"  重复询问次数: {quality['repeat_question_count']}")
    print(f"  总体评分: {quality['overall_transfer_score']}/5")
