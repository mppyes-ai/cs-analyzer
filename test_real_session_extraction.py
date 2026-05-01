import sys
sys.path.insert(0, '/Users/jinlu/.openclaw/workspace/skills/cs-analyzer')

from knowledge_graph import KnowledgeGraph, SessionExtractor
import re
import json

def parse_log_file_v3(file_path, max_sessions=10):
    """解析客服聊天记录文件（最终版）"""
    sessions = []
    
    with open(file_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    print(f"总行数: {len(lines)}")
    
    current_session = []
    in_session = False
    
    i = 0
    while i < len(lines):
        line = lines[i]
        
        # 会话开始标记
        if '以下为一通会话' in line:
            if current_session:
                sessions.append(current_session)
                print(f"  完成会话 {len(sessions)}: {len(current_session)} 条消息")
                current_session = []
                if len(sessions) >= max_sessions:
                    break
            in_session = True
            i += 1
            continue
        
        # 会话结束标记
        if '会话结束' in line:
            if current_session:
                sessions.append(current_session)
                print(f"  完成会话 {len(sessions)}: {len(current_session)} 条消息")
                current_session = []
                if len(sessions) >= max_sessions:
                    break
            in_session = False
            i += 1
            continue
        
        # 在会话中，且不是空行
        if in_session and line.strip():
            # 检查是否是用户ID行（以\t结尾）
            stripped = line.rstrip('\t\n')
            
            # 匹配用户ID + 时间戳格式
            user_match = re.match(r'^(\S+)\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})$', stripped)
            
            if user_match:
                user_id = user_match.group(1)
                timestamp = user_match.group(2)
                
                # 下一条是消息内容
                if i + 1 < len(lines):
                    i += 1
                    content_line = lines[i].rstrip('\t\n')
                    
                    # 跳过空行
                    while i < len(lines) and not content_line.strip():
                        i += 1
                        if i < len(lines):
                            content_line = lines[i].rstrip('\t\n')
                    
                    # 判断角色
                    if 'jimi_vender' in user_id or '林内' in user_id:
                        role = 'assistant'
                    else:
                        role = 'user'
                    
                    current_session.append({
                        'role': role,
                        'user_id': user_id,
                        'timestamp': timestamp,
                        'content': content_line
                    })
        
        i += 1
    
    # 添加最后一个会话
    if current_session and len(sessions) < max_sessions:
        sessions.append(current_session)
        print(f"  完成会话 {len(sessions)}: {len(current_session)} 条消息")
    
    return sessions


def extract_knowledge_from_session(session, kg, extractor):
    """从单通会话提取知识"""
    # 构建session_data格式
    messages = []
    for msg in session:
        messages.append({
            'role': msg['role'],
            'content': msg['content']
        })
    
    session_data = {
        'session_id': f"session_{hash(str(messages)) % 10000:04d}",
        'messages': messages
    }
    
    # 使用SessionExtractor提取
    try:
        result = extractor.extract_from_session(session_data)
        return result
    except Exception as e:
        print(f"提取失败: {e}")
        return None


def main():
    print('=== 真实客服会话知识提取测试 ===')
    print()
    
    # 初始化知识图谱
    kg = KnowledgeGraph()
    extractor = SessionExtractor(kg)
    
    # 解析日志文件
    log_file = '/Users/jinlu/Desktop/小虾米专属文档/客服聊天记录/客服聊天记录(5).log'
    print(f'解析文件: {log_file}')
    print()
    
    sessions = parse_log_file_v3(log_file, max_sessions=5)
    print(f'\n成功解析 {len(sessions)} 通会话')
    print()
    
    # 逐通处理
    for idx, session in enumerate(sessions, 1):
        print(f'--- 会话 {idx} ---')
        
        # 显示会话摘要
        user_msgs = [m for m in session if m['role'] == 'user']
        assistant_msgs = [m for m in session if m['role'] == 'assistant']
        
        print(f'用户消息: {len(user_msgs)} 条')
        print(f'客服消息: {len(assistant_msgs)} 条')
        
        # 显示前3条用户消息
        for msg in user_msgs[:3]:
            content = msg['content'][:50] + '...' if len(msg['content']) > 50 else msg['content']
            print(f'  用户: {content}')
        
        # 显示前2条客服消息
        for msg in assistant_msgs[:2]:
            content = msg['content'][:50] + '...' if len(msg['content']) > 50 else msg['content']
            print(f'  客服: {content}')
        
        # 提取知识
        result = extract_knowledge_from_session(session, kg, extractor)
        if result:
            print(f'  提取实体: {len(result.get("entities", []))} 个')
            print(f'  建立关系: {result.get("relations", 0)} 条')
            if result.get('entities'):
                for eid in result['entities']:
                    entity = kg.get_entity(eid)
                    if entity:
                        print(f'    - [{entity["type"]}] {entity["name"]}')
        
        print()
    
    # 统计最终知识图谱
    print('=== 知识图谱统计 ===')
    kg.cursor.execute('SELECT type, COUNT(*) FROM kg_entities GROUP BY type')
    entity_types = kg.cursor.fetchall()
    
    kg.cursor.execute('SELECT type, COUNT(*) FROM kg_relations GROUP BY type')
    relation_types = kg.cursor.fetchall()
    
    print(f'实体总数: {sum(c for _, c in entity_types)}')
    print('实体分布:')
    for t, c in entity_types:
        print(f'  {t}: {c}')
    
    print(f'\n关系总数: {sum(c for _, c in relation_types)}')
    print('关系分布:')
    for t, c in relation_types:
        print(f'  {t}: {c}')
    
    # 显示所有实体
    print('\n=== 提取的实体详情 ===')
    kg.cursor.execute('SELECT id, type, name, attributes FROM kg_entities')
    for row in kg.cursor.fetchall():
        entity_id, entity_type, name, attrs = row
        print(f'\n[{entity_type}] {name} ({entity_id})')
        try:
            attr_dict = json.loads(attrs)
            for k, v in attr_dict.items():
                if not k.startswith('_') and k not in ['来源', '置信度']:
                    print(f'  {k}: {v}')
        except:
            pass
    
    kg.close()
    print('\n=== 测试完成 ===')


if __name__ == '__main__':
    main()
