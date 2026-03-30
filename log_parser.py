#!/usr/bin/env python3
"""客服聊天记录解析器 - 严格按照rules.md规则实现

角色识别规则（来自rules.md 1.4节）：
- 林内林* → 人工客服 → staff
- jimi_vender* → 机器人客服 → staff  
- 其他 → 用户 → user

转接识别规则（新增）：
- 检测转接关键词，标记转接会话
- 建立同一用户的会话关联链

作者: 小虾米
更新: 2026-03-22（新增转接识别）
"""

import re
import json
import hashlib
from typing import List, Dict, Tuple
from datetime import datetime

# 导入转接分析模块
try:
    from transfer_analyzer import detect_transfer, find_related_sessions
except ImportError:
    detect_transfer = None
    find_related_sessions = None


def clean_html(content: str) -> str:
    """清理HTML标签，保留纯文本和URL链接
    
    Args:
        content: 原始内容（可能包含HTML）
        
    Returns:
        清理后的纯文本（保留URL）
    """
    if not content:
        return content
    
    # 0. 先提取并临时保存URL（防止被HTML标签处理误删）
    url_pattern = r'(https?://[^\s<>"\']+)'
    urls = re.findall(url_pattern, content)
    url_placeholders = {}
    for idx, url in enumerate(urls):
        placeholder = f"___URL_{idx}___"
        url_placeholders[placeholder] = url
        content = content.replace(url, placeholder, 1)
    
    # 1. 替换常见的HTML换行标签为换行符
    content = content.replace('<br/>', '\n').replace('<br>', '\n')
    
    # 2. 移除所有HTML标签
    content = re.sub(r'<[^>]+>', '', content)
    
    # 3. 处理HTML实体
    content = content.replace('&nbsp;', ' ').replace('&lt;', '<').replace('&gt;', '>')
    content = content.replace('&amp;', '&').replace('&quot;', '"')
    
    # 4. 恢复URL
    for placeholder, url in url_placeholders.items():
        content = content.replace(placeholder, url)
    
    # 5. 压缩多余的换行（保留段落结构）
    content = re.sub(r'\n\s*\n+', '\n\n', content)
    
    # 6. 清理行首行尾空白
    content = '\n'.join(line.strip() for line in content.split('\n'))
    
    return content.strip()


def identify_role(sender: str) -> str:
    """根据发送者ID识别角色
    
    Args:
        sender: 发送者ID
        
    Returns:
        'staff' 或 'user'
    """
    if not sender:
        return 'user'
    
    # 人工客服：林内林*
    if sender.startswith('林内林'):
        return 'staff'
    
    # 机器人客服：jimi_vender*
    if sender.startswith('jimi_vender'):
        return 'staff'
    
    # 其他：用户
    return 'user'


def parse_log_file(log_file_path: str) -> List[Dict]:
    """解析客服聊天记录日志文件
    
    Args:
        log_file_path: 日志文件路径
        
    Returns:
        会话列表，每个会话包含session_id和messages
    """
    with open(log_file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # 按会话分割
    session_blocks = re.split(r'/\*{10,}.*?会话结束.*?\*{10,}/', content)
    
    sessions = []
    session_idx = 0
    
    # 匹配发送者+时间的正则
    sender_time_pattern = r'^(\S+)\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\s*$'
    
    for block in session_blocks:
        block = block.strip()
        if not block or '以下为一通会话' not in block:
            continue
        
        # 移除开头的分隔符
        block = re.sub(r'.*?以下为一通会话.*?\n', '', block, count=1)
        
        # 保留原始行（不过滤空行，用于识别多行内容）
        raw_lines = block.split('\n')
        
        messages = []
        i = 0
        while i < len(raw_lines):
            line = raw_lines[i].strip()
            
            # 匹配格式: 发送者ID + 时间行
            match = re.match(sender_time_pattern, line)
            
            if match and i + 1 < len(raw_lines):
                sender = match.group(1)
                timestamp = match.group(2)
                
                # 收集多行内容（直到遇到下一个发送者+时间行或会话结束）
                content_lines = []
                j = i + 1
                while j < len(raw_lines):
                    next_line = raw_lines[j]
                    # 如果下一行是新的发送者+时间，停止收集
                    if re.match(sender_time_pattern, next_line.strip()):
                        break
                    # 如果到达会话结束标记，停止收集
                    if '会话结束' in next_line:
                        break
                    content_lines.append(next_line)
                    j += 1
                
                # 合并内容行
                content = '\n'.join(content_lines).strip()
                
                # 清理HTML标签
                content = clean_html(content)
                
                # 跳过特殊消息（但保留用户发送的链接）
                if '【此消息为' in content:
                    i = j
                    continue
                
                # 识别角色
                role = identify_role(sender)
                
                messages.append({
                    'role': role,
                    'sender': sender,
                    'timestamp': timestamp,
                    'content': content
                })
                i = j
            else:
                i += 1
        
        if messages:
            # 生成session_id
            first_content = messages[0]['content']
            session_hash = hashlib.md5(first_content.encode()).hexdigest()[:8]
            session_id = f'session_{session_idx:04d}_{session_hash}'
            
            # 提取 user_id（第一条用户消息的发送者）
            user_id = ''
            for msg in messages:
                if msg['role'] == 'user':
                    user_id = msg['sender']
                    break
            
            # 提取 staff_name（第一条人工客服消息的发送者）
            staff_name = ''
            for msg in messages:
                if msg['role'] == 'staff' and not msg['sender'].startswith('jimi_vender'):
                    staff_name = msg['sender']
                    break
            
            sessions.append({
                'session_id': session_id,
                'user_id': user_id,
                'staff_name': staff_name,
                'messages': messages,
                'start_time': messages[0]['timestamp'] if messages else None,
                'end_time': messages[-1]['timestamp'] if messages else None
            })
            session_idx += 1
    
    # 第二阶段：分析转接关系
    sessions = analyze_transfer_relationships(sessions)
    
    return sessions


def analyze_transfer_relationships(sessions: List[Dict]) -> List[Dict]:
    """
    分析会话间的转接关系
    
    Args:
        sessions: 解析后的会话列表
        
    Returns:
        添加了转接信息的会话列表
    """
    if not detect_transfer:
        return sessions
    
    # 按用户分组
    user_sessions = {}
    for session in sessions:
        user_id = session.get('user_id')
        if user_id:
            if user_id not in user_sessions:
                user_sessions[user_id] = []
            user_sessions[user_id].append(session)
    
    # 处理每个用户的会话
    for user_id, user_sess_list in user_sessions.items():
        if len(user_sess_list) <= 1:
            continue
        
        # 按时间排序
        user_sess_list.sort(key=lambda x: x.get('start_time') or '9999')
        
        # 查找转接关系
        for i, session in enumerate(user_sess_list):
            # 检测是否是转接会话
            is_transfer, transfer_reason, transfer_time = detect_transfer(
                session.get('messages', [])
            )
            
            # 查找相关会话（5分钟内的其他会话）
            related = []
            session_time_str = session.get('start_time')
            if session_time_str:
                try:
                    session_time = datetime.strptime(session_time_str, '%Y-%m-%d %H:%M:%S')
                    for other in user_sess_list:
                        if other['session_id'] == session['session_id']:
                            continue
                        other_time_str = other.get('start_time')
                        if other_time_str:
                            try:
                                other_time = datetime.strptime(other_time_str, '%Y-%m-%d %H:%M:%S')
                                time_diff = abs((other_time - session_time).total_seconds())
                                if time_diff <= 300:  # 5分钟
                                    related.append(other['session_id'])
                            except:
                                pass
                except:
                    pass
            
            # 确定转接来源/目标
            transfer_from = None
            transfer_to = None
            
            if related:
                session_time = datetime.strptime(session.get('start_time', '9999'), '%Y-%m-%d %H:%M:%S')
                for related_id in related:
                    # 找到相关会话
                    related_sess = next((s for s in user_sess_list if s['session_id'] == related_id), None)
                    if related_sess:
                        related_time = datetime.strptime(
                            related_sess.get('start_time', '9999'), 
                            '%Y-%m-%d %H:%M:%S'
                        )
                        if related_time < session_time:
                            transfer_from = related_id  # 早的是来源
                        else:
                            transfer_to = related_id    # 晚的是目标
            
            # 更新会话信息
            session['is_transfer'] = is_transfer or len(related) > 0
            session['transfer_reason'] = transfer_reason if is_transfer else ("时间关联" if related else "")
            session['transfer_time'] = transfer_time
            session['transfer_from'] = transfer_from
            session['transfer_to'] = transfer_to
            session['related_sessions'] = related
    
    return sessions


def get_session_stats(sessions: List[Dict]) -> Dict:
    """获取会话统计信息（含转接统计）"""
    total = len(sessions)
    total_messages = sum(len(s['messages']) for s in sessions)
    
    user_msgs = 0
    staff_msgs = 0
    robot_msgs = 0
    transfer_sessions = 0
    
    for session in sessions:
        for msg in session['messages']:
            if msg['role'] == 'user':
                user_msgs += 1
            elif msg['role'] == 'staff':
                sender = msg.get('sender', '')
                if sender.startswith('jimi_vender'):
                    robot_msgs += 1
                else:
                    staff_msgs += 1
        
        if session.get('is_transfer') or session.get('transfer_from') or session.get('related_sessions'):
            transfer_sessions += 1
    
    return {
        'total_sessions': total,
        'total_messages': total_messages,
        'user_messages': user_msgs,
        'staff_messages': staff_msgs,
        'robot_messages': robot_msgs,
        'transfer_sessions': transfer_sessions
    }


if __name__ == '__main__':
    import sys
    
    log_file = '/Users/jinlu/Desktop/小虾米专属文档/客服聊天记录/客服聊天记录0312.log'
    
    print('🔄 解析日志文件...')
    sessions = parse_log_file(log_file)
    
    print(f'\n✅ 解析完成！')
    stats = get_session_stats(sessions)
    print(f'   总会话: {stats["total_sessions"]}')
    print(f'   总消息: {stats["total_messages"]}')
    print(f'   用户消息: {stats["user_messages"]}')
    print(f'   人工客服: {stats["staff_messages"]}')
    print(f'   机器人客服: {stats["robot_messages"]}')
    print(f'   🔀 转接会话: {stats["transfer_sessions"]}')
    
    # 保存到文件
    output_file = '/tmp/all_sessions_v2.json'
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(sessions, f, ensure_ascii=False, indent=2)
    
    print(f'\n💾 已保存到: {output_file}')
    
    # 显示转接会话示例
    transfer_examples = [s for s in sessions if s.get('is_transfer') or s.get('transfer_from')]
    if transfer_examples:
        print(f'\n🔀 转接会话示例:')
        for sess in transfer_examples[:3]:
            print(f'   Session ID: {sess["session_id"]}')
            print(f'   用户: {sess["user_id"]}')
            print(f'   客服: {sess["staff_name"]}')
            if sess.get('transfer_from'):
                print(f'   转接来源: {sess["transfer_from"]}')
            if sess.get('related_sessions'):
                print(f'   关联会话: {sess["related_sessions"]}')
            print()
    
    # 显示第一条会话的示例
    if sessions:
        print(f'\n📋 第一条会话示例:')
        print(f'   Session ID: {sessions[0]["session_id"]}')
        print(f'   消息数: {len(sessions[0]["messages"])}')
        for msg in sessions[0]['messages'][:3]:
            role_icon = '👤' if msg['role'] == 'user' else '👨‍💼'
            print(f'   {role_icon} {msg["sender"][:20]}: {msg["content"][:40]}...')
