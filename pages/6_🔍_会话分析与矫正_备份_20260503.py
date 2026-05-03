"""会话分析与矫正中心 - 融合版(矫正中心风格)

整合功能：
1. 矫正中心的侧边栏会话列表
2. 会话明细_v2的AI深度质检报告

风格：采用矫正中心的深色主题、卡片布局
更新: 2026-03-18 - 布局调整：规则统计放右栏下方整行
"""

import streamlit as st
import json
import sys
import os
import importlib
import re
import sqlite3

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# 强制重新加载模块，避免缓存问题
import db_utils
importlib.reload(db_utils)

from db_utils import load_sessions, get_corrected_ids, get_correction_by_session, save_correction_v2, is_session_corrected, init_correction_tables
from knowledge_base_v2 import get_rule_by_id

# ========== AI智能修正函数(支持云端LLM优先) ==========
def generate_ai_correction(entity, user_feedback):
    """AI智能修正实体内容 - 优先使用云端LLM"""
    import json
    import requests
    
    # 构建带强约束的提示词
    prompt = f"""
    【最高优先级指令 - 必须严格遵守】
    质检员的反馈是最终标准，AI必须完全按照质检员的要求执行，不得添加质检员未提及的内容。
    禁止过度解读、禁止补充背景知识、禁止添加合规/安全等未提及的维度。
    
    【角色定位】
    你是一位客服质检助手，只负责格式化输出，不做业务判断。你的任务是理解质检员的修改要求，并精确执行。
    
    【当前实体】
    类型: {entity['type']}
    名称: {entity['name']}
    属性:
    {json.dumps(entity['attributes'], ensure_ascii=False, indent=2)}
    
    【质检员反馈】
    {user_feedback}
    
    【任务要求】
    1. 仔细阅读质检员反馈，提取明确的修改要求
    2. 只修改质检员明确要求修改的点，其他内容保持原样
    3. 不得添加新的维度、属性、解释或背景知识
    4. 必须遵循"如无必要，勿增实体"原则
    5. 如果质检员要求删除某内容，必须完全删除，不得保留或改写
    
    【禁止行为 - 违反将导致错误】
    - ❌ 不得添加质检员未提及的合规性、安全性、法律风险等内容
    - ❌ 不得过度解读用户意图或补充"常识"
    - ❌ 不得将简单问题复杂化
    - ❌ 不得保留质检员明确要求删除的内容
    - ❌ 不得修改质检员未提及的属性
    
    【输出格式 - 严格JSON】
    {{
        "name": "修正后的实体名称(按质检员要求)",
        "attributes": {{
            "属性名": "修正后的值(只修改质检员提到的)"
        }},
        "correction_notes": ["只记录质检员明确要求的修改点"],
        "knowledge_gaps": [
            {{
                "category": "知识类别",
                "description": "需要补充的知识描述"
            }}
        ]
    }}
    
    【重要提醒】
    如果质检员说"删除XX"，则必须完全删除，不得保留。
    如果质检员说"不需要XX"，则不得添加XX。
    如果质检员说"只需XX"，则只保留XX，删除其他。
    """
    
    # 优先使用云端LLM(Kimi)
    try:
        response = requests.post(
            "https://api.moonshot.cn/v1/chat/completions",
            headers={"Authorization": "Bearer sk-lsKZ3OyShSZJjjb91jSmD310M61dLZ4K6hgq1aOu24h8yZYN"},
            json={
                "model": "kimi-k2.5",
                "messages": [
                    {"role": "system", "content": "你是一位专业的客服质检专家"},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.3,
                "max_tokens": 2000
            },
            timeout=60
        )
        
        if response.status_code == 200:
            result = response.json()
            content = result['choices'][0]['message']['content']
            
            try:
                json_start = content.find('{')
                json_end = content.rfind('}') + 1
                if json_start >= 0 and json_end > json_start:
                    json_str = content[json_start:json_end]
                    corrected = json.loads(json_str)
                    
                    return {
                        'id': entity['id'],
                        'type': entity['type'],
                        'name': corrected.get('name', entity['name']),
                        'attributes': corrected.get('attributes', entity['attributes']),
                        'correction_notes': corrected.get('correction_notes', []),
                        'knowledge_gaps': corrected.get('knowledge_gaps', [])
                    }
            except Exception as e:
                print(f"解析AI修正结果失败: {e}")
                return None
        else:
            print(f"云端LLM失败({response.status_code})，回退到本地模型")
            
    except Exception as e:
        print(f"云端LLM请求失败: {e}，回退到本地模型")
    
    # 回退到本地模型
    try:
        response = requests.post(
            "http://localhost:8000/v1/chat/completions",
            headers={"Authorization": "Bearer 1234567890"},
            json={
                "model": "Qwen3.6-35B-A3B-4bit",
                "messages": [
                    {"role": "system", "content": "你是一位专业的客服质检专家"},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.3,
                "max_tokens": 2000
            },
            timeout=60
        )
        
        if response.status_code == 200:
            result = response.json()
            content = result['choices'][0]['message']['content']
            
            try:
                json_start = content.find('{')
                json_end = content.rfind('}') + 1
                if json_start >= 0 and json_end > json_start:
                    json_str = content[json_start:json_end]
                    corrected = json.loads(json_str)
                    
                    return {
                        'id': entity['id'],
                        'type': entity['type'],
                        'name': corrected.get('name', entity['name']),
                        'attributes': corrected.get('attributes', entity['attributes']),
                        'correction_notes': corrected.get('correction_notes', []),
                        'knowledge_gaps': corrected.get('knowledge_gaps', [])
                    }
            except Exception as e:
                print(f"解析本地模型修正结果失败: {e}")
                return None
        
        return None
    except Exception as e:
        print(f"本地模型请求失败: {e}")
        return None


def generate_ai_correction_with_context(entity, user_feedback, context, session_messages=None, session_analysis=None):
    """AI智能修正实体内容(支持多轮对话上下文)- 优先使用云端LLM"""
    
    import json
    import requests
    
    # 构建完整的会话上下文
    session_context = """【系统任务说明】
我们正在对客服会话进行质量评估。你的任务是帮助质检员修正AI自动提取的"客服应答模式"实体。

【重要背景】
1. 原始AI提取可能错误地将"正确应答"标记为"问题模式"
2. 质检员发现提取错误后，需要你修正实体内容
3. 修正后的实体应该准确反映客服的实际表现，而非AI的初始判断
4. 如果客服处理正确，实体名称应改为正面标签(如"合规型应答")
5. 如果客服确实有问题，才保留负面标签(如"问题模式")

【修正方向判断】
请根据会话原文判断客服处理是否正确：
- 正确：名称改为"合规型应答"或"正确应答"，问题维度清空
- 错误：保留"问题模式"类名称，详细说明问题

【示例 - 正确应答被误判(重点学习)】
会话原文：
用户：我家是人工煤气
客服：本店只支持12T天然气，煤气暂时不支持

原始提取(错误)：
名称：否定型应答(无替代方案)
问题维度：["conversion"]
具体表现：未挖掘其他需求，错失转化机会

正确修正：
名称：合规型应答(安全优先)
问题维度：[]
具体表现：客服准确告知不支持，处理正确
原因：客服正确拒绝不合理需求，不涉及转化问题

【示例 - 错误应答(无需修正)】
会话原文：
用户：产品质量有问题
客服：那你退货吧，我们不管

原始提取(正确)：
名称：推诿型应答
问题维度：["standardization"]
具体表现：未安抚用户，直接推诿责任

无需修正，原始提取正确

【失败案例 - 必须避免】
质检员反馈：删除"品牌政策"相关内容，只保留客服实际说的话

AI错误修正：
具体表现：客服准确告知不支持，林内无人工煤气型号，处理正确
原因：客服遵循品牌政策，正确拒绝不合理需求

问题分析：
1. 未执行删除指令——仍保留"林内无人工煤气型号"（背景知识）
2. 未执行删除指令——仍保留"遵循品牌政策"（背景知识）
3. 正确做法：只写"客服准确告知不支持，处理正确"

【修改判定流程】
1. 质检员说"XX不对" → 修改XX
2. 质检员说"不要XX" → 删除XX
3. 质检员说"只需XX" → 只保留XX，删除其他
4. 质检员未提及的内容 → 保持原样

【输出前自检】
- 是否添加了质检员未提及的内容？
- 是否删除了质检员要求保留的内容？
- 是否使用了会话原文外的背景知识？
- 如果任一答案为"是"，必须重新修正

"""
    if session_messages:
        session_context += "【会话原文】\n"
        for msg in session_messages[:20]:  # 限制长度
            role = "用户" if msg.get('role') == 'user' else "客服"
            content = msg.get('content', '')[:200]
            session_context += f"{role}：{content}\n"
    
    if session_analysis:
        session_context += "\n【原始评分分析】\n"
        session_context += json.dumps(session_analysis, ensure_ascii=False, indent=2)[:1000]
    
    # 构建带完整上下文的提示词
    prompt = f"""
    【最高优先级指令 - 必须严格遵守】
    质检员的反馈是最终标准，AI必须完全按照质检员的要求执行，不得添加质检员未提及的内容。
    
    【核心原则 - 违反将导致错误】
    1. **只记录客服实际说了什么、做了什么**——不得将质检员补充的背景知识、行业常识、品牌政策写入客服行为特征
    2. **行为特征必须直接引用会话原文**——客服原话是什么就写什么，不得概括、升华、补充
    3. **具体表现必须基于事实陈述**——禁止加入"品牌政策""安全规范""国家规定"等客服未提及的内容
    4. **改进建议必须针对客服可执行的动作**——不得建议客服"告知用户非法改装"等客服实际未执行的操作
    5. **历史对话仅供理解质检员意图**——不得复述或保留历史轮次中已被否定的内容
    
    【角色定位】
    你是一位客服质检助手，只负责格式化输出，不做业务判断。你的任务是理解质检员的修改要求，并精确执行。
    
    {session_context}
    
    【当前提取的实体】
    类型: {entity['type']}
    名称: {entity['name']}
    属性:
    {json.dumps(entity['attributes'], ensure_ascii=False, indent=2)}
    
    【质检员反馈】
    {user_feedback}
    
    【任务要求】
    1. 仔细阅读完整的会话原文
    2. 理解质检员反馈的核心问题
    3. 基于会话事实，修正实体内容
    4. 只修改质检员明确要求修改的点
    5. 不得添加新的维度或背景知识
    
    【输出格式 - 严格JSON】
    {{
        "name": "修正后的实体名称",
        "attributes": {{
            "属性名": "修正后的值"
        }},
        "correction_notes": ["修改说明"],
        "knowledge_gaps": []
    }}
    """
    
    # 优先使用云端LLM(Kimi)
    try:
        response = requests.post(
            "https://api.moonshot.cn/v1/chat/completions",
            headers={"Authorization": "Bearer sk-lsKZ3OyShSZJjjb91jSmD310M61dLZ4K6hgq1aOu24h8yZYN"},
            json={
                "model": "kimi-k2.5",
                "messages": [
                    {"role": "system", "content": "你是一位专业的客服质检专家"},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.3,
                "max_tokens": 2000
            },
            timeout=60
        )
        
        if response.status_code == 200:
            result = response.json()
            content = result['choices'][0]['message']['content']
            
            try:
                json_start = content.find('{')
                json_end = content.rfind('}') + 1
                if json_start >= 0 and json_end > json_start:
                    json_str = content[json_start:json_end]
                    corrected = json.loads(json_str)
                    
                    return {
                        'id': entity['id'],
                        'type': entity['type'],
                        'name': corrected.get('name', entity['name']),
                        'attributes': corrected.get('attributes', entity['attributes']),
                        'correction_notes': corrected.get('correction_notes', []),
                        'knowledge_gaps': corrected.get('knowledge_gaps', [])
                    }
            except Exception as e:
                print(f"解析云端AI修正结果失败: {e}")
                return None
        else:
            print(f"云端LLM失败({response.status_code})，回退到本地模型")
            
    except Exception as e:
        print(f"云端LLM请求失败: {e}，回退到本地模型")
    
    # 回退到本地模型
    try:
        response = requests.post(
            "http://localhost:8000/v1/chat/completions",
            headers={"Authorization": "Bearer 1234567890"},
            json={
                "model": "Qwen3.6-35B-A3B-4bit",
                "messages": [
                    {"role": "system", "content": "你是一位专业的客服质检专家"},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.3,
                "max_tokens": 2000
            },
            timeout=60
        )
        
        if response.status_code == 200:
            result = response.json()
            content = result['choices'][0]['message']['content']
            
            try:
                json_start = content.find('{')
                json_end = content.rfind('}') + 1
                if json_start >= 0 and json_end > json_start:
                    json_str = content[json_start:json_end]
                    corrected = json.loads(json_str)
                    
                    return {
                        'id': entity['id'],
                        'type': entity['type'],
                        'name': corrected.get('name', entity['name']),
                        'attributes': corrected.get('attributes', entity['attributes']),
                        'correction_notes': corrected.get('correction_notes', []),
                        'knowledge_gaps': corrected.get('knowledge_gaps', [])
                    }
            except Exception as e:
                print(f"解析本地模型修正结果失败: {e}")
                return None
        
        return None
    except Exception as e:
        print(f"本地模型请求失败: {e}")
        return None


def build_correction_context(dialog, entity, current_feedback):
    """构建对话上下文"""
    context_parts = []
    
    for msg in dialog['messages']:
        if msg['role'] == 'user':
            # 质检员消息 - 右侧绿色气泡（微信风格）
            context_parts.append(f"""
            <div style="display: flex; justify-content: flex-end; margin: 12px 0; align-items: flex-start;">
                <div style="max-width: 70%; background-color: #95ec69; padding: 12px 16px; border-radius: 18px 4px 18px 18px; 
                            box-shadow: 0 1px 2px rgba(0,0,0,0.1); position: relative; margin-right: 8px;">
                    <div style="font-size: 13px; color: #333; line-height: 1.5; word-wrap: break-word;">
                        <b>👤 质检员（第{msg['round']}轮）</b><br/>
                        {msg['content']}
                    </div>
                    <div style="position: absolute; right: -6px; top: 14px; width: 0; height: 0; 
                                border-top: 6px solid transparent; border-bottom: 6px solid transparent; 
                                border-left: 6px solid #95ec69;"></div>
                </div>
                <div style="width: 36px; height: 36px; border-radius: 50%; background: linear-gradient(135deg, #07c160, #05a050); 
                            display: flex; align-items: center; justify-content: center; flex-shrink: 0; 
                            box-shadow: 0 2px 4px rgba(0,0,0,0.15);">
                    <span style="color: white; font-size: 18px;">👤</span>
                </div>
            </div>
            """)
        else:
            # AI修正消息 - 左侧白色气泡
            context_parts.append(f"""
            <div style="display: flex; justify-content: flex-start; margin: 12px 0; align-items: flex-start;">
                <div style="width: 36px; height: 36px; border-radius: 50%; background: linear-gradient(135deg, #1890ff, #096dd9); 
                            display: flex; align-items: center; justify-content: center; flex-shrink: 0; 
                            box-shadow: 0 2px 4px rgba(0,0,0,0.15);">
                    <span style="color: white; font-size: 18px;">🤖</span>
                </div>
                <div style="max-width: 70%; background-color: #ffffff; padding: 12px 16px; border-radius: 4px 18px 18px 18px; 
                            box-shadow: 0 1px 2px rgba(0,0,0,0.1); position: relative; margin-left: 8px; border: 1px solid #e8e8e8;">
                    <div style="font-size: 13px; color: #333; line-height: 1.5; word-wrap: break-word;">
                        <b>🤖 AI修正（第{msg['round']}轮）</b><br/>
                        {msg['content'][:300]}...
                    </div>
                    <div style="position: absolute; left: -6px; top: 14px; width: 0; height: 0; 
                                border-top: 6px solid transparent; border-bottom: 6px solid transparent; 
                                border-right: 6px solid #ffffff;"></div>
                </div>
            </div>
            """)
    
    return '<div style="background-color: #f5f5f5; padding: 16px; border-radius: 8px; max-height: 500px; overflow-y: auto;">' + "\n".join(context_parts) + '</div>'


def format_correction_result(corrected_entity):
    """格式化修正结果用于显示"""
    lines = []
    
    lines.append(f"实体名称: {corrected_entity['name']}")
    lines.append("")
    lines.append("修正后的属性:")
    for key, value in corrected_entity['attributes'].items():
        if value and str(value).strip():
            lines.append(f"  • {key}: {value}")
    
    if corrected_entity.get('correction_notes'):
        lines.append("")
        lines.append("修正说明:")
        for note in corrected_entity['correction_notes']:
            lines.append(f"  • {note}")
    
    if corrected_entity.get('knowledge_gaps'):
        lines.append("")
        lines.append("发现知识缺口:")
        for gap in corrected_entity['knowledge_gaps']:
            if isinstance(gap, dict):
                lines.append(f"  • [{gap.get('category', '未分类')}] {gap.get('description', '')}")
            else:
                lines.append(f"  • {str(gap)}")
    
    return "\n".join(lines)


# ========== 知识缺口表初始化 ==========
def init_knowledge_gaps_table():
    """初始化知识缺口表"""
    conn = sqlite3.connect('data/cs_analyzer_new.db')
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_gaps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            description TEXT NOT NULL,
            source TEXT,
            status TEXT DEFAULT '待补充',
            priority TEXT DEFAULT '中',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            resolved_at TIMESTAMP,
            resolved_by TEXT
        )
    """)
    
    conn.commit()
    conn.close()


# 初始化表
init_knowledge_gaps_table()


# 初始化矫正系统表
init_correction_tables()

st.set_page_config(page_title="会话分析与矫正", page_icon="🔍", layout="wide")

# 全局样式
st.markdown('''
<style>
.st-emotion-cache-1s8qyds { margin-bottom: -0.5rem !important; }
.st-emotion-cache-r7ut5z { margin-bottom: -0.5rem !important; }
</style>
''', unsafe_allow_html=True)

# 全局样式覆盖
st.markdown('''
<style>
.st-emotion-cache-1s8qyds {
    margin-bottom: -0.5rem !important;
}

/* ===== 侧边栏会话列表按钮居左对齐 ===== */
[data-testid="stSidebar"] button[kind="secondary"] p,
[data-testid="stSidebar"] button[kind="primary"] p {
    text-align: left !important;
}
[data-testid="stSidebar"] button[kind="secondary"],
[data-testid="stSidebar"] button[kind="primary"] {
    justify-content: flex-start !important;
}
</style>
''', unsafe_allow_html=True)

# ========== 加载数据 ==========
df = load_sessions()
if df.empty:
    st.warning("暂无会话数据")
    st.stop()

# 从URL参数读取session_id(支持从其他页面跳转)
query_params = st.query_params
if 'session_id' in query_params:
    target_session = query_params.get('session_id')
    if target_session and target_session in df['session_id'].values:
        st.session_state.selected_session = target_session
        # 清除query参数避免刷新时重复跳转
        del st.query_params['session_id']

# 从session_state读取跳转目标(支持从其他页面通过switch_page跳转)
if 'jump_to_session' in st.session_state:
    target_session = st.session_state.jump_to_session
    if target_session and target_session in df['session_id'].values:
        st.session_state.selected_session = target_session
    # 清除跳转标记
    del st.session_state.jump_to_session

# 计算矫正状态
corrected_ids = get_corrected_ids()
df['is_corrected'] = df['session_id'].isin(corrected_ids)

# ========== 主区域顶部：筛选器 ==========
filter_cols = st.columns([1, 1, 1, 1])
with filter_cols[0]:
    staff_names = [str(s) for s in df['staff_name'].dropna().unique() if s and str(s).strip() and s != 'None']
    staff_options = ["全部"] + sorted(staff_names)
    staff_filter = st.selectbox("客服", options=staff_options, key="staff_select")
    
with filter_cols[1]:
    # 简化的矫正状态列表(移除未使用的复杂状态)
    status_options = [
        "待矫正",
        "全部",
        "已矫正"  # 包含所有已提交矫正的记录
    ]
    status_filter = st.selectbox("矫正状态", status_options, key="status_select")

with filter_cols[2]:
    score_min, score_max = st.slider("总分范围", 4, 20, (4, 20), key="score_slider")

with filter_cols[3]:
    search_keyword = st.text_input("搜索", placeholder="会话ID或关键词", key="search_input")

# 应用筛选
filtered_df = df.copy()
if staff_filter != "全部":
    filtered_df = filtered_df[filtered_df['staff_name'] == staff_filter]

# 简化的矫正状态筛选
if status_filter != "全部":
    # 获取所有会话的矫正记录存在性
    has_correction_map = {}
    for sid in df['session_id'].unique():
        has_correction_map[sid] = is_session_corrected(sid)
    
    # 根据筛选条件过滤
    if status_filter == "待矫正":
        filtered_df = filtered_df[filtered_df['session_id'].apply(lambda x: not has_correction_map.get(x, False))]
    elif status_filter == "已矫正":
        filtered_df = filtered_df[filtered_df['session_id'].apply(lambda x: has_correction_map.get(x, False))]

filtered_df = filtered_df[(filtered_df['total_score'] >= score_min) & (filtered_df['total_score'] <= score_max)]
if search_keyword:
    filtered_df = filtered_df[filtered_df['session_id'].str.contains(search_keyword, na=False) | filtered_df['summary'].str.contains(search_keyword, na=False)]

st.divider()

# ========== 左侧边栏：会话列表 ==========
with st.sidebar:
    st.markdown(f"##### 📋 会话列表({len(filtered_df)}条)")
    st.markdown('<style>.stButton>button {padding: 0.15rem 0.3rem !important; font-size: 11px !important; margin: 1px 0 !important; min-height: 28px !important;}</style>', unsafe_allow_html=True)
    
    # ===== 分页功能 =====
    ITEMS_PER_PAGE = 20  # 每页显示20条
    total_items = len(filtered_df)
    total_pages = (total_items + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE  # 向上取整
    
    # 分页显示逻辑(超过20条才显示分页)
    if total_items > ITEMS_PER_PAGE:
        # 初始化当前页码
        if 'session_list_page' not in st.session_state:
            st.session_state.session_list_page = 0
        
        # 确保页码在有效范围内
        current_page = min(st.session_state.session_list_page, max(0, total_pages - 1))
        st.session_state.session_list_page = current_page
        
        # 计算当前页的数据范围
        start_idx = current_page * ITEMS_PER_PAGE
        end_idx = min(start_idx + ITEMS_PER_PAGE, total_items)
        page_df = filtered_df.iloc[start_idx:end_idx]
        
        # 分页栏：单行三列布局 [上一页] [页码] [下一页]
        prev_col, page_col, next_col = st.columns([1, 1, 1])
        
        with prev_col:
            if st.button("◀", key="prev_page", use_container_width=True, disabled=(current_page <= 0)):
                st.session_state.session_list_page = max(0, current_page - 1)
                st.rerun()
        
        with page_col:
            st.markdown(f"<div style='text-align:center;font-size:14px;font-weight:bold;padding:8px 0;'>{current_page + 1}/{total_pages}</div>", unsafe_allow_html=True)
        
        with next_col:
            if st.button("▶", key="next_page", use_container_width=True, disabled=(current_page >= total_pages - 1)):
                st.session_state.session_list_page = min(total_pages - 1, current_page + 1)
                st.rerun()
        
        # 分隔线
        st.markdown("<hr style='margin: 6px 0; border: none; border-top: 1px solid rgba(128,128,128,0.2);'>", unsafe_allow_html=True)
    else:
        # 不足20条，显示全部
        page_df = filtered_df
    
    # 渲染当前页的会话按钮
    for _, row in page_df.iterrows():
        sid = row['session_id']
        score = row['total_score']
        risk_tag = "🔴" if score <= 8 else "🟡" if score <= 12 else "🟢"

        # 转接标记
        is_transfer = bool(row.get('is_transfer')) or bool(row.get('transfer_from'))
        transfer_tag = "🔀" if is_transfer else ""

        # 链位置指示
        related = row.get('related_sessions', '[]')
        try:
            related_list = json.loads(related) if isinstance(related, str) else []
            chain_count = len(related_list) + 1 if related_list else 1
            if is_transfer and chain_count > 1:
                chain_indicator = f"({chain_count})"
            else:
                chain_indicator = ""
        except:
            chain_indicator = ""

        # ===== 会话列表项(简洁版，居左显示)=====
        is_selected = st.session_state.get('selected_session') == sid
        
        # 按钮标签：状态球 + ID + 会话链(全部居左)
        btn_label = f"{risk_tag}  {sid[-10:]}  {chain_indicator}"
        
        # 根据选中状态设置按钮类型
        btn_type = "primary" if is_selected else "secondary"
        
        # 使用 st.button 实现点击(简洁单元素，居左对齐)
        if st.button(btn_label, key=f"btn_{sid}", use_container_width=True, type=btn_type):
            st.session_state.selected_session = sid
            st.rerun()

# ========== 主区域：会话详情 ==========
if 'selected_session' not in st.session_state:
    st.info("👈 请从左侧边栏选择一个会话查看详情")
    st.stop()

session_id = st.session_state['selected_session']
# 使用原始df而非filtered_df，避免筛选条件变化导致找不到数据
session_match = df[df['session_id'] == session_id]
if session_match.empty:
    st.error(f"❌ 找不到会话 {session_id}，可能已被删除")
    st.stop()
session_data = session_match.iloc[0].to_dict()

# 提前加载矫正记录，供状态显示和按钮控制使用
corrections_df = get_correction_by_session(session_id)

# 解析消息
try:
    messages = json.loads(session_data.get('messages', '[]'))
except:
    messages = []

# 当前分数
prof_score = int(session_data.get('professionalism_score', 0)) or 3
std_score = int(session_data.get('standardization_score', 0)) or 3
pol_score = int(session_data.get('policy_execution_score', 0)) or 3
con_score = int(session_data.get('conversion_score', 0)) or 3

# ========== 会话头部信息 ==========
st.markdown('# 💬 会话明细(带规则命中)')
summary = session_data.get('summary', '会话详情')
st.markdown(f'### 📋 会话摘要\n{summary}')

# 会话时间
try:
    if messages and len(messages) > 0:
        start_time = messages[0].get('timestamp', '')
        end_time = messages[-1].get('timestamp', '')
        if start_time and end_time:
            from datetime import datetime
            try:
                start_dt = datetime.strptime(str(start_time)[:19], '%Y-%m-%d %H:%M:%S')
                end_dt = datetime.strptime(str(end_time)[:19], '%Y-%m-%d %H:%M:%S')
                duration_min = round((end_dt - start_dt).total_seconds() / 60)
                st.markdown(f'<div style="font-size: 15px; margin-top: 3px;"><b>会话时间：</b>{str(start_time)[:19]} - {str(end_time)[:19]} <span style="font-size: 13px; color: #888;">(会话约{duration_min}分钟)</span></div>', unsafe_allow_html=True)
            except:
                st.markdown(f'<div style="font-size: 15px; margin-top: 3px;"><b>会话时间：</b>{str(start_time)[:19]} - {str(end_time)[:19]} <span style="font-size: 13px; color: #888;">(会话约1分钟)</span></div>', unsafe_allow_html=True)
        else:
            st.markdown('<div style="font-size: 15px; margin-top: 3px;"><b>会话时间：</b>未记录</div>', unsafe_allow_html=True)
    else:
        st.markdown('<div style="font-size: 15px; margin-top: 3px;"><b>会话时间：</b>未记录</div>', unsafe_allow_html=True)
except:
    st.markdown('<div style="font-size: 15px; margin-top: 3px;"><b>会话时间：</b>解析错误</div>', unsafe_allow_html=True)

# 🔍 会话预分析详情(移到会话时间下面)
try:
    analysis_json = session_data.get('analysis_json', '{}')
    if analysis_json and analysis_json != '{}':
        analysis_data_head = json.loads(analysis_json) if isinstance(analysis_json, str) else analysis_json
    else:
        analysis_data_head = {}
except:
    analysis_data_head = {}

pre = analysis_data_head.get('_metadata', {}).get('pre_analysis', {})
scene = pre.get('scene', 'N/A')
intent = pre.get('intent', 'N/A')
sentiment = pre.get('sentiment', 'N/A')
confidence = pre.get('confidence', 0)

# 带tooltip的预分析显示
st.markdown(
    f'<div style="font-size: 15px; margin-top: 6px;">'
    f'<b>🔍 预分析:</b> '
    f'场景: <code>{scene}</code> │ '
    f'意图: <code>{intent}</code> │ '
    f'情绪: <code>{sentiment}</code>'
    f'<span class="tooltip">'
    f'<span class="tooltip-icon">?</span>'
    f'<span class="tooltiptext">情绪分析结果：<br>• 积极 (positive)：用户态度友好，满意度高<br>• 中性 (neutral)：用户态度平和，正常沟通<br>• 消极 (negative)：用户有抱怨、不满或投诉倾向</span>'
    f'</span> │ '
    f'置信度: <code>{confidence}</code>'
    f'<span class="tooltip">'
    f'<span class="tooltip-icon">?</span>'
    f'<span class="tooltiptext">置信度表示AI分析的可靠程度：<br>• 0.9-1.0：高置信度，结果非常可靠<br>• 0.7-0.9：中等置信度，结果较为可靠<br>• 0.5-0.7：低置信度，建议人工复核<br>• 低于0.5：置信度不足，需谨慎参考</span>'
    f'</span>'
    f'</div>',
    unsafe_allow_html=True
)

# 元信息 - 根据矫正状态显示
uid = session_data.get('user_id', '未知')

# 获取矫正状态(简化版 - 只区分"已矫正"和"待矫正")
has_correction = not corrections_df.empty

# 简化的状态显示
if has_correction:
    status_text = "✅ 已矫正"
    status_color = "#52c41a"  # 绿色
else:
    status_text = "🔧 待矫正"
    status_color = "#888888"  # 灰色

# 获取合并会话数
session_count = session_data.get('session_count', 1)
if session_count and session_count > 1:
    merge_badge = f' │ <span style="background: #1890ff; color: white; padding: 2px 8px; border-radius: 10px; font-size: 12px;">合并 {session_count} 个会话</span>'
else:
    merge_badge = ''

# 获取转接信息
is_transfer = bool(session_data.get('is_transfer')) or bool(session_data.get('transfer_from'))
transfer_reason = session_data.get('transfer_reason', '')
transfer_from = session_data.get('transfer_from', '')
transfer_to = session_data.get('transfer_to', '')

if is_transfer:
    transfer_badge = f' │ <span style="background: #faad14; color: white; padding: 2px 8px; border-radius: 10px; font-size: 12px;">🔀 {transfer_reason or "转接会话"}</span>'
else:
    transfer_badge = ''

st.markdown(f'<div style="font-size: 15px; margin-top: 3px;"><b>会话ID:</b> <code>{session_id}</code> │ <b>客服:</b> {session_data.get("staff_name", "未知")} │ <b>用户:</b> {uid[:20]} │ <b>状态:</b> <span style="color: {status_color}; font-weight: bold;">{status_text}</span>{merge_badge}{transfer_badge}</div>', unsafe_allow_html=True)

# 显示转接链信息(如果有)
# 先解析 related_list
related = session_data.get('related_sessions', '[]')
try:
    related_list = json.loads(related) if isinstance(related, str) else related
except:
    related_list = []

if is_transfer and (transfer_from or transfer_to or related_list):
    # 构建完整会话链列表
    chain_sessions = []
    
    # 添加来源会话
    if transfer_from:
        chain_sessions.append({'id': transfer_from, 'type': '来源', 'color': '#52c41a'})
    
    # 添加当前会话
    chain_sessions.append({'id': session_id, 'type': '当前', 'color': '#1890ff'})
    
    # 添加后续会话
    if transfer_to:
        chain_sessions.append({'id': transfer_to, 'type': '后续', 'color': '#faad14'})
    
    # 如果没有transfer_from/to，但有related_list，则从related_list构建
    if not transfer_from and not transfer_to and related_list:
        for rid in related_list:
            if rid != session_id:
                chain_sessions.append({'id': rid, 'type': '关联', 'color': '#888888'})
    
    # 显示会话链 - 上下布局
    if chain_sessions:
        # 标题行
        st.markdown('**🔀 会话链**')
        
        # 按钮行 - 紧凑列布局
        btn_cols = st.columns([1] * len(chain_sessions))
        for i, sess in enumerate(chain_sessions):
            with btn_cols[i]:
                sess_id_full = sess['id']
                is_current = (sess['id'] == session_id)
                
                if is_current:
                    st.markdown(
                        f'<div style="background: {sess["color"]}; color: white; padding: 6px 6px; '
                        f'border-radius: 4px; font-size: 13px; font-weight: bold; text-align: center; '
                        f'border: 1px solid #fff; box-shadow: 0 1px 2px rgba(0,0,0,0.2); line-height: 1.5;">'
                        f'{sess["type"]}: {sess_id_full}</div>',
                        unsafe_allow_html=True
                    )
                else:
                    btn_key = f"chain_nav_{sess['id']}"
                    if st.button(f"{sess['type']}: {sess_id_full}", key=btn_key, use_container_width=True):
                        st.session_state.selected_session = sess['id']
                        st.rerun()

# 🔍 会话预分析详情(移到头部信息区)
# 添加tooltip样式
st.markdown('''
<style>
.tooltip {
    position: relative;
    display: inline-block;
    cursor: help;
}
.tooltip .tooltiptext {
    visibility: hidden;
    width: 280px;
    background-color: rgba(0, 0, 0, 0.9);
    color: #fff;
    text-align: left;
    padding: 10px 14px;
    border-radius: 8px;
    font-size: 13px;
    line-height: 1.6;
    position: absolute;
    z-index: 1000;
    top: 125%;
    left: 50%;
    margin-left: -140px;
    opacity: 0;
    transition: opacity 0.3s;
    box-shadow: 0 4px 12px rgba(0,0,0,0.4);
}
.tooltip:hover .tooltiptext {
    visibility: visible;
    opacity: 1;
}
.tooltip-icon {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 14px;
    height: 14px;
    background-color: #666;
    color: white;
    border-radius: 50%;
    font-size: 10px;
    margin-left: 4px;
    vertical-align: middle;
}
</style>
''', unsafe_allow_html=True)

# 使用紧凑分隔线替代 st.divider()
st.markdown('<hr style="margin: 10px 0; border: none; border-top: 1px solid rgba(128,128,128,0.3);">', unsafe_allow_html=True)

# 知识提取融合区域(基于LLM分析结果 - 接入 knowledge_store.py 统一审核)
st.markdown('<hr style="margin: 10px 0; border: none; border-top: 1px solid rgba(128,128,128,0.3);">', unsafe_allow_html=True)
st.markdown('## 🧠 知识提取与审核(AI自动提取 + 人工审核)')

# 导入 knowledge_store 模块（新位置：skills/cs-analyzer/graphiti/）
# 当前文件位置: skills/cs-analyzer/pages/xxx.py
# 目标位置: skills/cs-analyzer/graphiti/knowledge_store.py
# 相对路径: ../graphiti
ks_module_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'graphiti')
sys.path.insert(0, ks_module_path)

try:
    from knowledge_store import KnowledgeStore, ReviewWorkflow, ExtractedEntity
    KNOWLEDGE_STORE_AVAILABLE = True
    print(f"✅ knowledge_store 模块导入成功 (路径: {ks_module_path})")
except ImportError as e:
    KNOWLEDGE_STORE_AVAILABLE = False
    print(f"⚠️ knowledge_store 模块导入失败: {e}")

# 初始化知识存储
if KNOWLEDGE_STORE_AVAILABLE:
    ks_db_path = os.path.join(ks_module_path, 'knowledge_store.db')
    ks_store = KnowledgeStore(ks_db_path)
    ks_workflow = ReviewWorkflow(ks_store)
    print(f"✅ 知识库初始化完成: {ks_db_path}")
    print(f"📊 实体统计: {ks_store.get_entity_stats()}")
else:
    ks_store = None
    ks_workflow = None

# 从 knowledge_store 获取该会话的实体（优先）
ks_entities = []
if ks_workflow:
    try:
        ks_entities = ks_workflow.get_entities_by_session(session_id, status='pending')
    except Exception as e:
        pass  # 降级到本地提取

# 如果 knowledge_store 中没有，使用本地提取逻辑
if not ks_entities:
    # 从analysis_json获取LLM分析结果
    analysis_json = session_data.get('analysis_json', '{}')
    try:
        analysis_data = json.loads(analysis_json) if isinstance(analysis_json, str) else analysis_json
    except:
        analysis_data = {}
    
    # 获取LLM分析的核心信息
    session_analysis = analysis_data.get('session_analysis', {})
    dimension_scores = analysis_data.get('dimension_scores', {})
    summary = analysis_data.get('summary', {})
    pre = analysis_data.get('_metadata', {}).get('pre_analysis', {})
    
    # 提取所有消息内容
    all_messages = messages
    user_messages = [m for m in all_messages if m.get('role') == 'user' or m.get('sender', '').startswith('1')]
    staff_messages = [m for m in all_messages if m.get('role') == 'staff' or '林内' in m.get('sender', '')]
    
    user_combined = ' '.join([m.get('content', '') for m in user_messages])
    staff_combined = ' '.join([m.get('content', '') for m in staff_messages])
    all_combined = user_combined + ' ' + staff_combined
    
    # ========== 构建完整的知识实体 ==========
    extracted_entities = []
    
    # 【实体1】产品信息
    products = list(set(re.findall(r'(?:林内|燃气热水器|热水器|壁挂炉|灶具|油烟机|GD\d+|RUS-\d+)', all_combined)))
    if products or '热水器' in all_combined:
        product_name = products[0] if products else '燃气热水器'
        extracted_entities.append({
            'id': f'product_{session_id}_{product_name}',
            'type': '产品',
            'name': product_name,
            'attributes': {
                '产品名称': product_name,
                '品牌': '林内',
                '识别方式': '关键词匹配' if products else '上下文推断',
                '来源会话': session_id
            },
            'source': '规则提取'
        })
    
    # 【实体2】气源适配规则(整合所有气源相关信息)
    gas_support = None
    gas_types = []
    
    if '天然气' in staff_combined or '12T' in staff_combined:
        gas_types.append('天然气(12T)')
        if '只支持' in staff_combined or '支持' in staff_combined:
            gas_support = True
    
    if '人工煤气' in all_combined:
        gas_types.append('人工煤气')
        if '不支持' in staff_combined or '暂时不支持' in staff_combined:
            gas_support = False
    
    if gas_types:
        # 构建规则内容
        rule_parts = []
        if '只支持12T天然气' in staff_combined:
            rule_parts.append('本店只支持12T天然气')
        elif '支持' in staff_combined and '天然气' in staff_combined:
            rule_parts.append('支持天然气(12T)')
        
        if '不支持' in staff_combined and '煤气' in staff_combined:
            rule_parts.append('不支持人工煤气')
        elif '暂时不支持' in staff_combined:
            rule_parts.append('暂时不支持人工煤气')
        
        rule_content = '；'.join(rule_parts) if rule_parts else '气源适配政策'
        
        extracted_entities.append({
            'id': f'rule_gas_{session_id}',
            'type': '服务规则',
            'name': '气源适配政策',
            'attributes': {
                '规则名称': '林内燃气热水器气源适配政策',
                '规则内容': rule_content,
                '支持气源': [g for g in gas_types if '天然气' in g],
                '不支持气源': [g for g in gas_types if '煤气' in g or '液化' in g],
                '风险等级': '高(涉及安全)',
                '规则来源': '客服明确表述',
                '来源会话': session_id
            },
            'source': '规则提取+LLM分析'
        })
    
    # 【实体3】客服应答模式(整合问题诊断)
    problem_dims = []
    for dim_name, dim_data in dimension_scores.items():
        score = dim_data.get('score', 0)
        if score <= 2:
            problem_dims.append({
                '维度': dim_name,
                '评分': score,
                '问题': dim_data.get('reasoning', '')[:150]
            })
    
    if problem_dims:
        # 构建模式描述
        pattern_desc = []
        for p in problem_dims:
            pattern_desc.append(f"{p['维度']}({p['评分']}分): {p['问题']}")
        
        # 获取改进建议
        suggestions = summary.get('suggestions', [])
        improvement = suggestions[0] if suggestions else '需改进客服应答策略'
        
        extracted_entities.append({
            'id': f'pattern_{session_id}_negative',
            'type': '应答模式',
            'name': '否定型应答模式(无替代方案)',
            'attributes': {
                '模式名称': '否定型应答(无替代方案)',
                '适用场景': '气源不匹配咨询',
                '行为特征': '直接告知不支持，未提供替代型号或解决方案',
                '问题维度': [p['维度'] for p in problem_dims],
                '具体表现': '\n'.join(pattern_desc),
                '转化影响': '客户流失风险高',
                '改进建议': improvement,
                '来源会话': session_id
            },
            'source': 'LLM问题诊断'
        })
    
    # 【实体4】场景分类(从LLM预分析)
    scene_category = pre.get('scene', '其他')
    scene_sub = pre.get('sub_scene', '其他')
    intent = pre.get('intent', '咨询')
    sentiment = pre.get('sentiment', 'neutral')
    
    # 根据内容优化场景分类
    if '煤气' in all_combined or '天然气' in all_combined or '气源' in all_combined:
        scene_category = '售前咨询'
        scene_sub = '气源适配'
    elif '安装' in all_combined:
        scene_category = '售前咨询'
        scene_sub = '安装咨询'
    elif '价格' in all_combined or '多少钱' in all_combined:
        scene_category = '售前咨询'
        scene_sub = '价格决策'
    
    extracted_entities.append({
        'id': f'scene_{session_id}',
        'type': '场景',
        'name': f'{scene_category}-{scene_sub}',
        'attributes': {
            '场景大类': scene_category,
            '场景细分': scene_sub,
            '用户意图': intent,
            '用户情绪': sentiment,
            '主题': session_analysis.get('theme', ''),
            '置信度': pre.get('confidence', 0)
        },
        'source': 'LLM场景识别+规则优化'
    })
    
    # 保存到 knowledge_store（如果可用）
    if ks_store and extracted_entities:
        for entity_data in extracted_entities:
            entity = ExtractedEntity(
                id=entity_data['id'],
                entity_type=entity_data['type'],
                name=entity_data['name'],
                attributes=entity_data['attributes'],
                confidence=0.8,  # 默认置信度
                source_quote='',
                source_session=session_id
            )
            ks_store.save_entity(entity)
            ks_store.save_entity_source(entity.id, session_id, entity.attributes)
        
        # 重新获取（现在已在数据库中）
        ks_entities = ks_workflow.get_entities_by_session(session_id, status='pending')

# 统一使用 ks_entities 进行后续展示
extracted_entities = ks_entities if ks_entities else []

# 显示提取结果 + 审核操作(合并为一栏)
st.markdown("### 📦 自动提取结果")
if extracted_entities:
    st.markdown(f"<div style='font-size: 12px; color: #888;'>共 {len(extracted_entities)} 个实体待审核</div>", unsafe_allow_html=True)
else:
    st.info("📭 本次会话未提取到知识实体")

# 批量操作按钮（当有多条时显示）
if len(extracted_entities) > 1:
    st.markdown("**批量操作：**")
    batch_cols = st.columns(3)
    with batch_cols[0]:
        if st.button("✅ 全部通过", key=f"kg_batch_pass_{session_id}", type="primary"):
            if ks_workflow:
                entity_ids = [e['id'] for e in extracted_entities]
                result = ks_workflow.approve_entities_batch(entity_ids, reviewer="质检员", notes="批量通过")
                st.success(f"✅ 已通过 {result['approved_count']}/{result['total_requested']} 个实体")
                st.rerun()
            else:
                st.error("知识存储模块未初始化")
    with batch_cols[1]:
        if st.button("❌ 全部拒绝", key=f"kg_batch_rej_{session_id}"):
            if ks_workflow:
                entity_ids = [e['id'] for e in extracted_entities]
                result = ks_workflow.reject_entities_batch(entity_ids, reviewer="质检员", notes="批量拒绝")
                st.warning(f"❌ 已拒绝 {result['rejected_count']}/{result['total_requested']} 个实体")
                st.rerun()
            else:
                st.error("知识存储模块未初始化")
    with batch_cols[2]:
        st.markdown(f"<div style='font-size: 12px; color: #888; padding-top: 8px;'>已选 {len(extracted_entities)} 个实体</div>", unsafe_allow_html=True)

if extracted_entities:
    for i, entity in enumerate(extracted_entities):
        with st.container():
            # 卡片样式
            type_colors = {
                'ProductEntity': '#1890ff',
                'PolicyRuleEntity': '#52c41a',
                'Scene': '#faad14',
                'FaultProblemEntity': '#ff4d4f',
                '产品': '#1890ff',
                '服务规则': '#52c41a',
                '场景': '#faad14',
                '应答模式': '#ff4d4f'
            }
            color = type_colors.get(entity.get('entity_type', entity.get('type', '')), '#888')
            entity_name = entity.get('name', 'Unknown')
            entity_type = entity.get('entity_type', entity.get('type', 'Unknown'))
            entity_id = entity.get('id', f'{session_id}_{i}')
            
            # 实体标题卡片（可点击展开审核历史）
            title_col1, title_col2 = st.columns([6, 1])
            with title_col1:
                st.markdown(f"""
                <div style="border: 1px solid rgba(128,128,128,0.3); border-radius: 8px; padding: 12px; margin: 8px 0; background: rgba(255,255,255,0.05);">
                    <div style="border-left: 4px solid {color}; padding-left: 10px; margin-bottom: 8px;">
                        <b style="color: {color};">[{entity_type}]</b> <b>{entity_name}</b>
                        <span style="font-size: 11px; color: #888;">(置信度: {entity.get('confidence', 0):.2f})</span>
                    </div>
                </div>
                """, unsafe_allow_html=True)
            with title_col2:
                if st.button("📋", key=f"toggle_hist_{session_id}_{i}_{entity_id}", help="查看审核历史"):
                    history_key = f"history_{session_id}_{i}_{entity_id}"
                    st.session_state[history_key] = not st.session_state.get(history_key, False)
                    st.rerun()
            
            # 显示属性
            attrs = entity.get('attributes', {})
            for key, value in attrs.items():
                if value and str(value).strip():
                    if isinstance(value, list):
                        value = ', '.join(value)
                    display_value = str(value)[:120] + '...' if len(str(value)) > 120 else str(value)
                    st.markdown(f"""
                    <div style="margin-left: 14px; font-size: 13px; line-height: 1.5;">
                        ├─ <b>{key}:</b> {display_value}
                    </div>
                    """, unsafe_allow_html=True)
            
            # 检查冲突标记
            has_conflict = False
            conflict_attrs = []
            for key, value in attrs.items():
                if isinstance(value, list) and len(value) > 1:
                    has_conflict = True
                    conflict_attrs.append(key)
            
            # 如果有冲突，显示警告
            if has_conflict:
                st.markdown(f"""
                <div style="margin-left: 14px; padding: 6px 10px; background: rgba(255,77,79,0.1); 
                            border: 1px solid #ff4d4f; border-radius: 4px; 
                            font-size: 12px; color: #ff4d4f; margin-top: 6px;"
                >
                    ⚠️ <b>属性冲突：</b>{', '.join(conflict_attrs)} 存在多个值，需人工确认
                </div>
                """, unsafe_allow_html=True)
            
            # 审核操作按钮（使用 knowledge_store 状态）
            entity_status = entity.get('status', 'pending')
            
            # 审核历史展开（点击实体名称）
            history_key = f"history_{session_id}_{i}_{entity_id}"
            if st.session_state.get(history_key):
                if ks_workflow:
                    history = ks_workflow.get_review_history(entity_id)
                    if history:
                        st.markdown("<div style='margin-left: 14px; margin-top: 8px;'><b>📋 审核历史：</b></div>", unsafe_allow_html=True)
                        for h in history[:3]:  # 最多显示3条
                            action_color = "#52c41a" if h['action'] == 'approve' else "#ff4d4f" if h['action'] == 'reject' else "#faad14"
                            action_text = "通过" if h['action'] == 'approve' else "拒绝" if h['action'] == 'reject' else "修改"
                            st.markdown(f"""
                            <div style="margin-left: 14px; font-size: 12px; color: #888; padding: 4px 0; border-bottom: 1px solid rgba(128,128,128,0.2);">
                                <span style="color: {action_color};">● {action_text}</span> 
                                | {h.get('reviewer', '未知')} 
                                | {h.get('created_at', 'N/A')[:19]}
                                {f"| 💬 {h.get('notes', '')}" if h.get('notes') else ""}
                            </div>
                            """, unsafe_allow_html=True)
                    else:
                        st.markdown("<div style='margin-left: 14px; font-size: 12px; color: #888;'>暂无审核记录</div>", unsafe_allow_html=True)
                else:
                    st.markdown("<div style='margin-left: 14px; font-size: 12px; color: #888;'>知识存储模块未初始化</div>", unsafe_allow_html=True)
            
            if entity_status == 'approved':
                st.markdown(f"""
                <div style="padding: 8px 12px; background: rgba(82,196,26,0.1); 
                            border: 1px solid #52c41a; border-radius: 6px; 
                            font-size: 14px; color: #52c41a; text-align: center;"
                >
                    ✅ 此实体已审核通过
                </div>
                """, unsafe_allow_html=True)
            elif entity_status == 'rejected':
                st.markdown(f"""
                <div style="padding: 8px 12px; background: rgba(255,77,79,0.1); 
                            border: 1px solid #ff4d4f; border-radius: 6px; 
                            font-size: 14px; color: #ff4d4f; text-align: center;"
                >
                    ❌ 此实体已拒绝
                </div>
                """, unsafe_allow_html=True)
            else:
                # 待审核状态：显示操作按钮
                c1, c2, c3, c4 = st.columns(4)
                
                with c1:
                    if st.button("✅ 通过", key=f"kg_pass_{session_id}_{i}", type="primary"):
                        if ks_workflow:
                            ks_workflow.approve_entity(entity_id, reviewer="质检员", notes="审核通过")
                            st.success(f"✅ [{entity_name}] 已通过！")
                            st.rerun()
                        else:
                            st.error("知识存储模块未初始化")
                
                with c2:
                    if st.button("✏️ 修改", key=f"kg_mod_{session_id}_{i}"):
                        st.session_state[f"modifying_{session_id}_{i}"] = True
                
                with c3:
                    if st.button("❌ 拒绝", key=f"kg_rej_{session_id}_{i}"):
                        if ks_workflow:
                            ks_workflow.reject_entity(entity_id, reviewer="质检员", notes="审核拒绝")
                            st.warning(f"❌ [{entity_name}] 已拒绝")
                            st.rerun()
                        else:
                            st.error("知识存储模块未初始化")
                
                with c4:
                    if st.button("🤖 AI修正", key=f"kg_ai_{session_id}_{i}_{entity_id}"):
                        st.session_state[f"ai_correcting_{session_id}_{i}_{entity_id}"] = True
                
                # AI修正界面(多轮对话模式)
                if st.session_state.get(f"ai_correcting_{session_id}_{i}_{entity['id']}"):
                    st.markdown("""
                    <div style="margin-left: 14px; padding: 12px; background: rgba(24,144,255,0.05); border: 1px solid #1890ff; border-radius: 8px;">
                        <b>🤖 AI智能修正(多轮对话模式)</b>
                        <div style="font-size: 12px; color: #888; margin-top: 4px;">
                            可与AI反复沟通，直到结果完全正确再入库
                        </div>
                    </div>
                    """, unsafe_allow_html=True)
                    
                    # 初始化对话历史
                    dialog_key = f"ai_dialog_{session_id}_{i}_{entity['id']}"
                    if dialog_key not in st.session_state:
                        st.session_state[dialog_key] = {
                            'round': 0,
                            'messages': [],
                            'current_entity': entity.copy()
                        }
                    
                    dialog = st.session_state[dialog_key]
                    
                    # 显示对话历史
                    for idx, msg in enumerate(dialog['messages']):
                        if msg['role'] == 'user':
                            st.markdown(f"""
                            <div style="margin-left: 14px; margin-top: 8px; padding: 8px; 
                                        background: rgba(255,255,255,0.05); border-radius: 8px;
                                        border-left: 3px solid #1890ff;">
                                <b>👤 质检员(第{msg['round']}轮):</b><br/>
                                {msg['content']}
                            </div>
                            """, unsafe_allow_html=True)
                        else:
                            st.markdown(f"""
                            <div style="margin-left: 14px; margin-top: 8px; padding: 8px; 
                                        background: rgba(82,196,26,0.05); border-radius: 8px;
                                        border-left: 3px solid #52c41a;">
                                <b>🤖 AI(第{msg['round']}轮修正):</b><br/>
                                <pre style="white-space: pre-wrap; font-size: 13px;">{msg['content']}</pre>
                            </div>
                            """, unsafe_allow_html=True)
                    
                    # 当前轮次输入
                    current_round = dialog['round'] + 1
                    
                    # 如果是第一轮，显示原始实体
                    if dialog['round'] == 0:
                        st.markdown("""
                        <div style="margin-left: 14px; margin-top: 8px; padding: 8px; 
                                    background: rgba(255,77,79,0.05); border-radius: 8px;
                                    border-left: 3px solid #ff4d4f;">
                            <b>📋 原始提取结果(待修正):</b><br/>
                        </div>
                        """, unsafe_allow_html=True)
                        for key, value in entity['attributes'].items():
                            if value and str(value).strip():
                                st.markdown(f"  • **{key}:** {value}")
                    
                    # 输入框
                    user_feedback = st.text_area(
                        f"第{current_round}轮反馈(描述问题或提出修改意见)",
                        key=f"ai_feedback_{session_id}_{i}_{entity['id']}_round_{current_round}",
                        height=80,
                        placeholder="例如：改进建议还不对，不应该推荐其他型号，因为林内所有产品都不支持人工煤气"
                    )
                    
                    # 操作按钮
                    c_generate, c_confirm, c_cancel = st.columns([2, 1, 1])
                    
                    with c_generate:
                        if st.button(f"🔄 生成第{current_round}轮修正", key=f"ai_generate_{session_id}_{i}_{entity['id']}_round_{current_round}"):
                            if user_feedback.strip():
                                with st.spinner(f"AI正在生成第{current_round}轮修正方案..."):
                                    # 构建对话上下文
                                    context = build_correction_context(dialog, entity, user_feedback)
                                    
                                    # 准备会话上下文
                                    session_messages = messages
                                    session_analysis = analysis_data
                                    
                                    # 调用AI(传入完整上下文)
                                    corrected_entity = generate_ai_correction_with_context(
                                        entity, 
                                        user_feedback, 
                                        context,
                                        session_messages=session_messages,
                                        session_analysis=session_analysis
                                    )
                                    
                                    if corrected_entity:
                                        # 记录对话
                                        dialog['messages'].append({
                                            'role': 'user',
                                            'content': user_feedback,
                                            'round': current_round
                                        })
                                        dialog['messages'].append({
                                            'role': 'assistant',
                                            'content': format_correction_result(corrected_entity),
                                            'round': current_round
                                        })
                                        dialog['round'] = current_round
                                        dialog['current_entity'] = corrected_entity
                                        
                                        st.session_state[dialog_key] = dialog
                                        st.success(f"✅ 第{current_round}轮修正已生成！")
                                        st.rerun()
                                    else:
                                        st.error("❌ AI修正失败，请重试")
                            else:
                                st.warning("请先描述问题")
                    
                    with c_confirm:
                        # 只有生成过修正后才能确认
                        if dialog['round'] > 0:
                            if st.button("✅ 确认入库", key=f"ai_confirm_{session_id}_{i}_{entity['id']}", type="primary"):
                                conn = sqlite3.connect('data/cs_analyzer_new.db')
                                cursor = conn.cursor()
                                
                                corrected = dialog['current_entity']
                                updated_attrs = corrected['attributes'].copy()
                                updated_attrs['_ai_corrected'] = True
                                updated_attrs['_ai_correction_rounds'] = dialog['round']
                                updated_attrs['_ai_correction_history'] = json.dumps([
                                    {'round': m['round'], 'role': m['role'], 'content': m['content'][:200]} 
                                    for m in dialog['messages']
                                ], ensure_ascii=False)
                                updated_attrs['_ai_corrected_at'] = datetime.now().isoformat()
                                
                                cursor.execute("""
                                    INSERT OR REPLACE INTO kg_entities (id, type, name, attributes, status, _source_session_id)
                                    VALUES (?, ?, ?, ?, '已通过', ?)
                                """, (
                                    entity['id'], entity['type'], corrected['name'],
                                    json.dumps(updated_attrs, ensure_ascii=False),
                                    session_id
                                ))
                                
                                # 标记知识缺口
                                for gap in corrected.get('knowledge_gaps', []):
                                    cursor.execute("""
                                        INSERT OR IGNORE INTO knowledge_gaps (category, description, source, status, created_at)
                                        VALUES (?, ?, ?, '待补充', ?)
                                    """, (
                                        gap['category'], gap['description'], 
                                        '质检员AI多轮修正', datetime.now().isoformat()
                                    ))
                                
                                conn.commit()
                                conn.close()
                                
                                # 清理对话状态
                                st.session_state.pop(dialog_key, None)
                                
                                st.success(f"✅ [{corrected['name']}] AI多轮修正已入库！")
                                st.rerun()
                        else:
                            st.markdown("<div style='text-align: center; color: #888; font-size: 12px;'>先生成修正方案</div>", unsafe_allow_html=True)
                    
                    with c_cancel:
                        if st.button("❌ 放弃修正", key=f"ai_cancel_{session_id}_{i}_{entity['id']}"):
                            # 清理对话状态
                            st.session_state.pop(dialog_key, None)
                            st.session_state.pop(f"ai_correcting_{session_id}_{i}_{entity['id']}", None)
                            st.rerun()
                
                # 修改界面
                if st.session_state.get(f"modifying_{session_id}_{i}"):
                    st.markdown("""
                    <div style="margin-left: 14px; padding: 8px; background: rgba(255,255,255,0.05); border-radius: 4px;">
                        <b>修改此实体:</b>
                    </div>
                    """, unsafe_allow_html=True)
                    
                    new_name = st.text_input("名称", value=entity['name'], key=f"mod_name_{session_id}_{i}")
                    
                    new_attrs = {}
                    for key, value in entity['attributes'].items():
                        new_val = st.text_input(f"{key}", value=str(value), key=f"mod_attr_{session_id}_{i}_{key}")
                        if new_val != str(value):
                            new_attrs[key] = new_val
                    
                    if st.button("保存修改", key=f"save_mod_{session_id}_{i}"):
                        conn = sqlite3.connect('data/cs_analyzer_new.db')
                        cursor = conn.cursor()
                        
                        updated_attrs = entity['attributes'].copy()
                        updated_attrs.update(new_attrs)
                        updated_attrs['_modified_by'] = '质检员'
                        updated_attrs['_modified_at'] = datetime.now().isoformat()
                        
                        cursor.execute("""
                            INSERT OR REPLACE INTO kg_entities (id, type, name, attributes, status, _source_session_id)
                            VALUES (?, ?, ?, ?, '已通过', ?)
                        """, (
                            entity['id'], entity['type'], new_name or entity['name'],
                            json.dumps(updated_attrs, ensure_ascii=False),
                            session_id
                        ))
                        
                        conn.commit()
                        conn.close()
                        st.session_state[f"kg_approved_{session_id}_{i}"] = True
                        st.success(f"✅ [{entity['name']}] 已修改并保存！")
                        st.rerun()
                
                # 拒绝界面
                if st.session_state.get(f"rejecting_{session_id}_{i}"):
                    st.markdown("""
                    <div style="margin-left: 14px; padding: 8px; background: rgba(255,255,255,0.05); border-radius: 4px;">
                        <b>拒绝此实体:</b>
                    </div>
                    """, unsafe_allow_html=True)
                    
                    reason = st.text_input("拒绝原因", key=f"rej_reason_{session_id}_{i}")
                    if st.button("确认拒绝", key=f"confirm_rej_{session_id}_{i}"):
                        if reason:
                            conn = sqlite3.connect('data/cs_analyzer_new.db')
                            cursor = conn.cursor()
                            cursor.execute("""
                                INSERT OR REPLACE INTO kg_entities (id, type, name, attributes, status, reject_reason, _source_session_id)
                                VALUES (?, ?, ?, ?, '已拒绝', ?, ?)
                            """, (
                                entity['id'], entity['type'], entity['name'],
                                json.dumps(entity['attributes'], ensure_ascii=False),
                                reason, session_id
                            ))
                            conn.commit()
                            conn.close()
                            st.session_state[f"kg_rejected_{session_id}_{i}"] = True
                            st.error(f"❌ [{entity['name']}] 已拒绝")
                            st.rerun()
                        else:
                            st.warning("请填写拒绝原因")
            
            st.markdown("---")
else:
    st.info("未提取到知识实体")

# 底部统计（使用 knowledge_store 统一查询）
st.markdown('<hr style="margin: 10px 0; border: none; border-top: 1px solid rgba(128,128,128,0.3);">', unsafe_allow_html=True)
cols = st.columns(4)

# 统一从 knowledge_store 查询
if ks_store:
    stats = ks_store.get_entity_stats()
    total = sum(stats.get('by_status', {}).values())
    pending = stats.get('by_status', {}).get('pending', 0)
    approved = stats.get('by_status', {}).get('approved', 0)
    rejected = stats.get('by_status', {}).get('rejected', 0)
else:
    total = pending = approved = rejected = 0

with cols[0]:
    st.metric("知识图谱实体", total)

with cols[1]:
    st.metric("🔴 待审核", pending)

with cols[2]:
    st.metric("🟢 已通过", approved)

with cols[3]:
    st.metric("🔴 已拒绝", rejected)

# ========== 方案D：浮窗矫正功能 ==========
@st.dialog("✅ 无需矫正", width="large")
def show_no_correction_dialog():
    """显示无需矫正确认浮窗"""
    st.markdown(f"**当前会话:** `{session_id}`")
    st.success("✅ AI评分结果已确认准确，无需人工矫正")
    
    st.markdown("---")
    st.markdown("**当前AI评分结果：**")
    cols = st.columns(4)
    with cols[0]:
        st.metric("专业性", f"{prof_score}/5")
    with cols[1]:
        st.metric("标准化", f"{std_score}/5")
    with cols[2]:
        st.metric("政策执行", f"{pol_score}/5")
    with cols[3]:
        st.metric("转化能力", f"{con_score}/5")
    
    st.markdown(f"**总分：{session_data.get('total_score', 10)}/20**")
    
    st.markdown("---")
    st.markdown("**确认备注(可选)**")
    reason = st.text_area("如有需要，可填写备注信息...", placeholder="例如：评分准确，符合实际情况", height=80, key="no_correction_reason")
    
    col1, col2 = st.columns(2)
    with col1:
        if st.button("❌ 取消", use_container_width=True):
            st.rerun()
    with col2:
        if st.button("✅ 确认无误", type="primary", use_container_width=True):
            # 保存"无需矫正"记录，changed_fields为空列表表示确认无需修改，status=no_action
            save_correction_v2(
                session_id=session_id,
                changed_fields=[],
                reason=reason if reason else "AI评分准确，无需矫正",
                other_reason="",
                corrected_by="admin",
                status="no_action"
            )
            st.toast(f"✅ 已确认无需矫正！", icon="✅")
            st.success("确认成功！")
            st.balloons()
            st.rerun()

@st.dialog("✏️ 评分矫正", width="large")
def show_correction_dialog():
    """显示评分矫正浮窗对话框 - 动态结构化输入(上下布局)"""
    session_key_prefix = session_id.replace("-", "_").replace(".", "_")
    
    # 内容有效性检查函数
    def is_valid_content(text):
        """检查内容是否有效(必须包含中文或有效文字)"""
        if not text or not text.strip():
            return False, "内容不能为空"
        
        # 去除空格和换行后检查
        import re
        cleaned = text.strip()
        
        # 检查是否纯空格/换行
        if not cleaned or re.match(r'^[\s]+$', cleaned):
            return False, "内容不能仅为空格或换行"
        
        # 移除所有空格和换行
        no_spaces = re.sub(r'\s', '', cleaned)
        
        if not no_spaces:
            return False, "内容不能为空"
        
        # 检查是否包含中文(必须包含至少一个中文字符)
        if re.search(r'[\u4e00-\u9fa5]', no_spaces):
            return True, ""
        
        # 如果没有中文，检查是否仅由字母、数字、符号组成
        # 字母、数字、符号、英文标点
        invalid_pattern = r'^[a-zA-Z0-9\W_]+$'
        if re.match(invalid_pattern, no_spaces):
            return False, "内容必须包含中文或有效文字，不能仅为字母、数字、符号的组合"
        
        return True, ""
    
    # 清除保持打开的标志
    if f"dialog_keep_open_{session_key_prefix}" in st.session_state:
        st.session_state[f"dialog_keep_open_{session_key_prefix}"] = False
    
    # 添加固定头部区域的CSS样式
    st.markdown("""
    <style>
    .fixed-header {
        position: sticky;
        top: 0;
        background: inherit;
        z-index: 100;
        padding-bottom: 10px;
        border-bottom: 1px solid rgba(128,128,128,0.2);
    }
    .scrollable-content {
        max-height: 60vh;
        overflow-y: auto;
        padding-top: 10px;
    }
    </style>
    """, unsafe_allow_html=True)
    
    # 固定头部区域开始
    st.markdown("<div class='fixed-header'>", unsafe_allow_html=True)
    
    st.markdown(f"**当前会话:** `{session_id}`")
    st.caption(f"状态: {'✅ 已矫正' if session_data['is_corrected'] else '🔧 待矫正'}")
    
    # 显示拒绝理由(如果有)
    if st.session_state.get('show_reject_reason'):
        reject_corr = st.session_state['show_reject_reason']
        st.error(f"❌ **上次矫正被拒绝**\n\n**拒绝理由：** {reject_corr.get('reject_reason', '未提供具体理由')}\n\n请根据拒绝理由修改后重新提交。")
        st.markdown("---")
        # 清除标记，避免重复显示
        del st.session_state['show_reject_reason']
    
    session_key_prefix = session_id.replace("-", "_").replace(".", "_")
    
    # 获取AI分析数据(用于显示AI判定过程)
    analysis_data = {}
    try:
        analysis_json = session_data.get('analysis_json', '{}')
        if analysis_json and analysis_json != '{}':
            analysis_data = json.loads(analysis_json) if isinstance(analysis_json, str) else analysis_json
    except:
        analysis_data = {}
    ds = analysis_data.get('dimension_scores', {})
    
    # 维度配置
    dims = [
        ('professionalism', '专业性', prof_score),
        ('standardization', '标准化', std_score),
        ('policy_execution', '政策执行', pol_score),
        ('conversion', '转化能力', con_score)
    ]
    
    # 初始化用户修改存储(独立存储，不受勾选状态影响)
    for key, name, score in dims:
        user_slider_key = f"user_slider_{key}_{session_key_prefix}"
        user_reason_key = f"user_reason_{key}_{session_key_prefix}"
        check_key = f"check_{key}_{session_key_prefix}"
        
        # 只在首次初始化
        if user_slider_key not in st.session_state:
            st.session_state[user_slider_key] = score
        if user_reason_key not in st.session_state:
            st.session_state[user_reason_key] = ""
        if check_key not in st.session_state:
            st.session_state[check_key] = False
    
    # ===== 上部：维度选择(单行4个checkbox)=====
    st.markdown("**维度选择**")
    
    dim_icons = {'professionalism': '📚', 'standardization': '📝', 'policy_execution': '📋', 'conversion': '🎯'}
    
    # 使用4列布局
    dim_cols = st.columns(4)
    for i, (key, name, score) in enumerate(dims):
        with dim_cols[i]:
            check_key = f"check_{key}_{session_key_prefix}"
            icon = dim_icons.get(key, '')
            st.checkbox(
                f"{icon} {name}: {score}分",
                key=check_key
            )
    
    # 关闭固定头部区域，开始可滚动区域(合并为一行避免空div)
    st.markdown("</div><div class='scrollable-content'>", unsafe_allow_html=True)
    
    # ===== 下部：矫正详情(动态生成)=====
    st.markdown(
        '**矫正详情**'
        '<span class="tooltip">'
        '<span class="tooltip-icon">?</span>'
        '<span class="tooltiptext">勾选维度后，在此调整分值并填写矫正说明。<br>取消勾选后重新勾选，之前的修改会自动恢复。</span>'
        '</span>',
        unsafe_allow_html=True
    )
    
    # 检查是否有勾选任何维度
    has_checked = any(st.session_state.get(f"check_{key}_{session_key_prefix}", False) for key, _, _ in dims)
    
    if has_checked:
        # 动态生成已勾选维度的输入卡片
        for key, name, score in dims:
            check_key = f"check_{key}_{session_key_prefix}"
            if st.session_state.get(check_key, False):
                with st.container(border=True):
                    st.markdown(f"**{name}**")
                    # 添加紧凑的分割线(无行间距)
                    st.markdown('<hr style="margin: 4px 0; border: none; border-top: 1px solid rgba(200,200,200,0.3);">', unsafe_allow_html=True)
                    
                    # 获取AI判定过程
                    dim_info = ds.get(key, {})
                    ai_reasoning = dim_info.get('reasoning', '无判定过程')
                    is_generic = "知识库未覆盖" in ai_reasoning or "通用标准评判" in ai_reasoning
                    reasoning_bg = "rgba(250,173,20,0.15)" if is_generic else "rgba(240,240,240,0.1)"
                    
                    # 使用2列布局：左侧AI判定过程，右侧上下两部分(高度1:3)
                    main_cols = st.columns([1, 4])
                    
                    # 左侧：AI判定过程(无标题，占满整个高度)
                    with main_cols[0]:
                        warning_icon = "⚠️ " if is_generic else ""
                        # 使用CSS确保填满高度
                        ai_html = f"""
                        <style>
                        .ai-reasoning-container {{
                            display: flex;
                            flex-direction: column;
                        }}
                        .ai-reasoning-content {{
                            flex: 1;
                            overflow-y: auto;
                            padding: 12px;
                            border: 1px solid rgba(200,200,200,0.3);
                            border-radius: 6px;
                            font-size: 14px;
                            line-height: 1.6;
                            background-color: {reasoning_bg};
                            max-height: 300px;
                        }}
                        </style>
                        <div class="ai-reasoning-container">
                            <div class="ai-reasoning-content">{warning_icon}{ai_reasoning}</div>
                        </div>
                        """
                        st.markdown(ai_html, unsafe_allow_html=True)
                    
                    # 右侧：上下两部分(高度1:3)
                    with main_cols[1]:
                        # 右上：调整分值(占1/3高度)
                        st.markdown('<p style="margin-bottom: 4px; font-weight: bold;">调整分值</p>', unsafe_allow_html=True)
                        
                        # 使用双key机制：独立存储用户修改
                        user_slider_key = f"user_slider_{key}_{session_key_prefix}"
                        slider_key = f"slider_{key}_{session_key_prefix}"
                        
                        # 只在首次初始化时设置默认值，不强制覆盖(避免重置用户拖动)
                        if slider_key not in st.session_state:
                            st.session_state[slider_key] = st.session_state[user_slider_key]
                        
                        # 使用3:1比例
                        slider_cols = st.columns([3, 1])
                        
                        # 左侧滑块
                        with slider_cols[0]:
                            new_score = st.slider(
                                "", 1, 5,
                                key=slider_key
                            )
                            # 用户修改后，更新独立存储
                            st.session_state[user_slider_key] = new_score
                        
                        # 右侧大色块显示分值
                        with slider_cols[1]:
                            # 根据分值变化情况设置颜色
                            original_score = score
                            if new_score < original_score:
                                score_color = "#f5222d"  # 红色-低于原分值
                                change_text = f"✓ 分值调整: {original_score}→{new_score}"
                            elif new_score > original_score:
                                score_color = "#52c41a"  # 绿色-高于原分值
                                change_text = f"✓ 分值调整: {original_score}→{new_score}"
                            else:
                                score_color = "#888888"  # 灰色-保持不变
                                change_text = "分值保持"
                            
                            # 大色块长方形样式
                            score_html = (
                                '<div style="background-color:' + score_color + ';color:white;text-align:center;'
                                'padding:12px 10px;border-radius:8px;font-size:20px;font-weight:bold;line-height:1.4;">'
                                + str(new_score) + '<span style="font-size:12px;">分</span><br/>'
                                '<span style="font-size:11px;font-weight:normal;">' + change_text + '</span></div>'
                            )
                            st.markdown(score_html, unsafe_allow_html=True)
                        
                        st.markdown("<div style='margin-top:12px;'></div>", unsafe_allow_html=True)
                        
                        # 右下：矫正说明(占2/3高度)
                        st.markdown('<p style="margin-bottom: 4px; font-weight: bold;">矫正说明</p>', unsafe_allow_html=True)
                        
                        # 使用双key机制
                        user_reason_key = f"user_reason_{key}_{session_key_prefix}"
                        reason_key = f"reason_{key}_{session_key_prefix}"
                        
                        # 从独立存储恢复值
                        reason_value = st.session_state[user_reason_key]
                        
                        user_reason = st.text_area(
                            "",
                            value=reason_value,
                            placeholder=f"如有需要，可说明{name}的矫正原因...",
                            key=reason_key,
                            height=140,
                            label_visibility="collapsed"
                        )
                        # 用户修改后，更新独立存储
                        st.session_state[user_reason_key] = user_reason
    else:
        # 没有勾选任何维度时的提示
        st.info("👆 请在上方勾选需要矫正的维度，或直接在下方填写其他说明")
    
    # 其他说明(不针对具体维度)
    st.markdown(
        '**其他说明**'
        '<span class="tooltip">'
        '<span class="tooltip-icon">?</span>'
        '<span class="tooltiptext">如有维度外的补充说明或整体评价，可在此填写。<br>不必勾选任何维度也可提交。</span>'
        '</span>',
        unsafe_allow_html=True
    )
    with st.container(border=True):
        other_reason_key = f"other_reason_{session_key_prefix}"
        other_reason = st.text_area(
            "补充说明",
            placeholder="如有其他补充说明或维度外的反馈，请在此填写...",
            key=other_reason_key,
            height=80,
            label_visibility="collapsed"
        )
    
    has_other = bool(st.session_state.get(other_reason_key, "").strip())
    
    # 错误提示
    error_placeholder = st.empty()
    
    # 底部按钮
    st.markdown("---")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("❌ 取消", use_container_width=True, key=f"cancel_correction_{session_key_prefix}"):
            st.rerun()
    with col2:
        if st.button("✅ 提交矫正", type="primary", use_container_width=True, key=f"submit_correction_{session_key_prefix}"):
            # 先收集数据并验证
            from db_utils import save_correction_v2
            
            selected_fields = []
            changed_fields = []
            dim_reasons = []  # 各维度说明
            
            for key, name, score in dims:
                check_key = f"check_{key}_{session_key_prefix}"
                if st.session_state.get(check_key, False):
                    user_slider_key = f"user_slider_{key}_{session_key_prefix}"
                    user_reason_key = f"user_reason_{key}_{session_key_prefix}"
                    
                    new_score = st.session_state.get(user_slider_key, score)
                    dim_reason = st.session_state.get(user_reason_key, "").strip()
                    
                    field_data = {
                        "field": f"{key}_score",
                        "name": name,
                        "old": score,
                        "new": new_score
                    }
                    if dim_reason:
                        field_data["reason"] = dim_reason
                        dim_reasons.append(f"{name}: {dim_reason}")
                    
                    selected_fields.append({
                        "key": key,
                        "name": name,
                        "old": score,
                        "new": new_score,
                        "reason": dim_reason
                    })
                    
                    if new_score != score:
                        changed_fields.append(field_data)
            
            other_reason_val = st.session_state.get(other_reason_key, "").strip()
            
            # ========== 新的验证规则 ==========
            # 规则1: 未勾选任何维度 + 其他说明为空 → 报错
            if not selected_fields and not other_reason_val:
                error_placeholder.error("❌ 请至少选择一个维度或填写其他说明")
            # 规则2: 勾选了维度 + (未调整分值 且 未填说明) → 报错
            elif selected_fields:
                # 检查每个勾选的维度是否有实际调整或说明
                empty_dims = []
                invalid_content_dims = []
                
                for dim in selected_fields:
                    has_score_change = dim['new'] != dim['old']
                    has_reason = bool(dim['reason'])
                    
                    if not has_score_change and not has_reason:
                        empty_dims.append(dim['name'])
                    elif dim['reason']:
                        # 检查说明内容有效性
                        is_valid, error_msg = is_valid_content(dim['reason'])
                        if not is_valid:
                            invalid_content_dims.append(f"{dim['name']}({error_msg})")
                
                if empty_dims:
                    error_placeholder.error(f"❌ 维度「{', '.join(empty_dims)}」未调整分值且未填写矫正说明，请录入需要调整的分值或矫正说明")
                elif invalid_content_dims:
                    error_placeholder.error(f"❌ 维度「{', '.join(invalid_content_dims)}」的矫正说明无效，请输入有效的说明文字")
                else:
                    # 检查其他说明的内容有效性(如果有)
                    if other_reason_val:
                        is_valid, error_msg = is_valid_content(other_reason_val)
                        if not is_valid:
                            error_placeholder.error(f"❌ 其他说明无效：{error_msg}")
                        else:
                            # 通过验证，打开二次确认对话框
                            st.session_state.correction_preview = {
                                "session_id": session_id,
                                "selected_fields": selected_fields,
                                "changed_fields": changed_fields,
                                "dim_reasons": dim_reasons,
                                "other_reason": other_reason_val
                            }
                            st.rerun()
                    else:
                        # 通过验证，打开二次确认对话框
                        st.session_state.correction_preview = {
                            "session_id": session_id,
                            "selected_fields": selected_fields,
                            "changed_fields": changed_fields,
                            "dim_reasons": dim_reasons,
                            "other_reason": other_reason_val
                        }
                        st.rerun()
            else:
                # 未勾选维度但有其他说明，检查内容有效性
                if other_reason_val:
                    is_valid, error_msg = is_valid_content(other_reason_val)
                    if not is_valid:
                        error_placeholder.error(f"❌ 其他说明无效：{error_msg}")
                    else:
                        # 打开二次确认
                        st.session_state.correction_preview = {
                            "session_id": session_id,
                            "selected_fields": [],
                            "changed_fields": [],
                            "dim_reasons": [],
                            "other_reason": other_reason_val
                        }
                        st.rerun()

    # 关闭可滚动区域
    st.markdown("</div>", unsafe_allow_html=True)

@st.dialog("✅ 确认提交矫正", width="large")
def show_correction_confirm_dialog():
    """二次确认对话框 - 显示矫正内容摘要"""
    preview = st.session_state.correction_preview
    
    st.markdown(f"**会话ID:** `{preview['session_id']}`")
    st.markdown("---")
    
    # 显示勾选的维度
    if preview['selected_fields']:
        st.markdown("**📋 已勾选的维度:**")
        for dim in preview['selected_fields']:
            has_change = dim['new'] != dim['old']
            has_reason = bool(dim['reason'])
            
            change_text = f"{dim['old']} → {dim['new']}" if has_change else f"{dim['old']}分(保持)"
            reason_text = f" | 说明: {dim['reason']}" if has_reason else ""
            
            st.markdown(f"- **{dim['name']}**: {change_text}{reason_text}")
    
    # 显示其他说明
    if preview['other_reason']:
        st.markdown("**📝 其他说明:**")
        st.info(preview['other_reason'])
    
    st.markdown("---")
    
    # 底部按钮
    col1, col2 = st.columns(2)
    with col1:
        if st.button("❌ 取消", use_container_width=True, key="confirm_cancel"):
            # 关闭二次确认对话框，但保持评分矫正弹窗打开
            del st.session_state.correction_preview
            # 设置标志让评分矫正弹窗重新打开
            st.session_state.reopen_correction_dialog = True
            st.rerun()
    with col2:
        if st.button("✅ 确认提交", type="primary", use_container_width=True, key="confirm_submit"):
            from db_utils import save_correction_v2
            
            # 构建保存数据
            selected_fields = []
            dim_reasons = []
            
            for dim in preview['selected_fields']:
                field_data = {
                    "field": f"{dim['key']}_score",
                    "name": dim['name'],
                    "old": dim['old'],
                    "new": dim['new']
                }
                if dim['reason']:
                    field_data["reason"] = dim['reason']
                    dim_reasons.append(f"{dim['name']}: {dim['reason']}")
                selected_fields.append(field_data)
            
            full_reason = "\n".join(dim_reasons) if dim_reasons else ""
            other_reason = preview['other_reason']
            
            # 保存到数据库 - 使用关键字参数
            save_correction_v2(
                session_id=preview['session_id'],
                changed_fields=selected_fields,
                reason=full_reason,
                other_reason=other_reason,
                corrected_by="admin"
            )
            
            # 清除预览状态
            del st.session_state.correction_preview
            
            # 显示成功反馈
            st.success("✅ 矫正提交成功！")
            st.toast("矫正记录已保存", icon="✅")
            st.balloons()
            st.rerun()

# ========== 二次确认对话框 ==========
if st.session_state.get("correction_preview"):
    show_correction_confirm_dialog()

left_col, right_col = st.columns([1, 2])

with left_col:
    st.markdown("##### 💬 对话内容")
    
    # 构建微信风格聊天HTML
    if not messages:
        chat_html = '<div style="padding:20px;text-align:center;color:#888;">暂无消息数据</div>'
    else:
        chat_html = '<div style="max-height: 500px; overflow-y: auto; background-color: transparent; padding: 10px;">'
        
        # 显示会话开始时间
        if messages and len(messages) > 0:
            first_msg_time = messages[0].get('timestamp', '')
            if first_msg_time:
                chat_html += f'<div style="text-align: center; font-size: 12px; color: #888; margin-bottom: 15px; padding: 5px;">{str(first_msg_time)[:19]}</div>'
        
        import re
        import html as html_module
        
        for msg in messages:
            sender = msg.get('sender', '')
            content = msg.get('content', '')
            msg_time = msg.get('timestamp', '')
            role = msg.get('role', '')
            is_staff = role == 'staff' or '林内' in sender
            is_bot = role == 'bot' or 'jimi_vender' in sender
            
            # 格式化时间(显示时分秒)
            time_str = str(msg_time)[11:19] if msg_time and len(str(msg_time)) >= 19 else ''
            
            # 清理HTML并转义
            content_plain = re.sub(r'<[^>]+>', '', content)
            content_escaped = html_module.escape(content_plain)
            
            # URL转链接
            url_pattern = r'(https?://[^\s<>"\']{10,})'
            def replace_url(match):
                url = match.group(1)
                display_url = url[:50] + '...' if len(url) > 50 else url
                return f'<a href="{url}" target="_blank" style="color: #1890ff; text-decoration: underline; word-break: break-all;">{display_url}</a>'
            content = re.sub(url_pattern, replace_url, content_escaped)
            
            # 换行符转HTML换行
            content = content.replace('\n', '<br/>')
            
            if len(content) > 1000:
                content = content[:1000] + '...'
            if not content.strip():
                continue
            
            # 判断顺序：先判断机器人，再判断人工客服
            if is_bot:
                # 🔵 蓝色 = 机器人
                robot_svg = '<svg viewBox="0 0 1024 1024" width="20" height="20" style="fill:white;"><path d="M928 384h96v384h-96zM576 512a64 64 0 1 0 128 0 64 64 0 1 0-128 0zM0 384h96v384H0z m384 268.8h256v96H384zM563.2 64H460.8v140.8h-320v742.4h742.4V204.8h-320V64z m217.6 243.2v537.6H243.2V307.2h537.6zM320 512a64 64 0 1 0 128 0 64 64 0 1 0-128 0z"/></svg>'
                chat_html += f'<div style="display: flex; align-items: flex-start; margin: 8px 0; flex-direction: row-reverse;">'
                chat_html += f'<div style="display: flex; flex-direction: column; align-items: center; margin-left: 10px;">'
                chat_html += f'<div style="width: 36px; height: 36px; border-radius: 4px; background-color: #1890ff; flex-shrink: 0; display: flex; align-items: center; justify-content: center;">{robot_svg}</div>'
                chat_html += f'<div style="font-size: 10px; color: #888; margin-top: 2px;">{time_str}</div>'
                chat_html += f'</div>'
                chat_html += f'<div style="padding: 10px 14px; font-size: 14px; line-height: 1.5; word-wrap: break-word; background-color: #e6f7ff; border-radius: 4px; color: #000; border: 1px solid #1890ff;">{content}</div>'
                chat_html += f'</div>'
            elif is_staff:
                # 🟢 绿色 = 人工客服
                staff_svg = '<svg viewBox="0 0 1024 1024" width="20" height="20" style="fill:white;"><path d="M414.866 996.381c19.911 8.693 44.744 18.287 68.461 22.893l36.794 4.502c4.54 0.232 20.314 0.275 23.646 0.145 0 0 18.786-0.086 36.721-4.111 17.935-4.027 47.19-12.381 69.423-23.109 0 0 37.7-21.762 79.459-53.764 41.759-32.004 102.539-119.213 133.36-194.886 43.178-9.57 82.752-45.509 89.268-124.671 6.169-74.586-16.623-107.818-47.205-122.297-0.324-8.876-0.752-17.593-1.281-26.078 39.336-217.806-116.266-293.913-128.436-297.604-20.272-30.801-157.642-218.141-406.87-66.06-36.49 22.256-94.402 66.087-125.323 110.25-37.185 46.697-64.212 114.261-75.281 211.927-7.667 0.825-15.335 2.027-22.893 3.736-2.013-47.928 0.622-189.179 90.137-289.598 66.21-74.252 166.7-111.915 298.611-111.915 132.158 0 234.697 37.952 304.874 112.784 115.23 122.92 104.833 306.365 104.719 308.189-0.639 9.832 6.805 18.347 16.651 18.983 0.405 0.029 0.81 0.058 1.187 0.058 9.34 0 17.202-7.254 17.812-16.681 0.55-8.124 11.756-200.315-114.073-334.746C787.427 41.838 676.011 0.006 533.457 0.006c-142.691 0-252.203 41.76-325.486 124.121-107.157 120.4-101.395 287.049-98.398 325.84-41.701 22.285-71.854 70.821-63.291 174.7 11.288 136.893 90.76 184.604 165.933 184.604 14.604 0 27.52-3.156 38.887-8.949 43.032 27.859 112.096 51.982 222.742 56.501 10.854 16.319 33.601 27.556 59.947 27.556 36.801 0 66.665-21.98 66.665-49.059 0-27.077-29.864-49.043-66.665-49.043-30.009 0-55.385 14.566-63.748 34.621-72.588-3.461-124.585-15.798-161.726-31.334-50.687-68.156-74.803-183.895-74.803-250.01 0-24.456 0.767-47.204 2.215-68.388 234.438 7.862 373.763-86.923 449.977-169.255 98.058 97.753 132.976 230.012 144.575 293.231-22.705 173.455-146.385 357.523-296.49 357.523-64.572 0-124.267-34.057-173.122-86.678-49.897-10.453-91.491-26.758-124.52-48.854 26.97 48.233 65.487 98.022 99.122 125.628s59.684 44.927 79.595 53.62z"/></svg>'
                chat_html += f'<div style="display: flex; align-items: flex-start; margin: 8px 0; flex-direction: row-reverse;">'
                chat_html += f'<div style="display: flex; flex-direction: column; align-items: center; margin-left: 10px;">'
                chat_html += f'<div style="width: 36px; height: 36px; border-radius: 4px; background-color: #07c160; flex-shrink: 0; display: flex; align-items: center; justify-content: center;">{staff_svg}</div>'
                chat_html += f'<div style="font-size: 10px; color: #888; margin-top: 2px;">{time_str}</div>'
                chat_html += f'</div>'
                chat_html += f'<div style="padding: 10px 14px; font-size: 14px; line-height: 1.5; word-wrap: break-word; background-color: #95ec69; border-radius: 4px; color: #000; border: 1px solid #4CAF50;">{content}</div>'
                chat_html += f'</div>'
            else:
                # ⚪ 灰色 = 用户
                user_svg = '<svg viewBox="0 0 1024 1024" width="20" height="20" style="fill:white;"><path d="M511.913993 941.605241c-255.612968 0-385.311608-57.452713-385.311608-170.810012 0-80.846632 133.654964-133.998992 266.621871-151.88846L393.224257 602.049387c-79.986561-55.904586-118.86175-153.436587-118.86175-297.240383 0-139.33143 87.211154-222.586259 233.423148-222.586259l7.912649 0c146.211994 0 233.423148 83.254829 233.423148 222.586259 0 54.184445 0 214.67361-117.829666 297.412397l-0.344028 16.685369c132.966907 18.061482 266.105829 71.041828 266.105829 151.716445C897.225601 884.152528 767.526961 941.605241 511.913993 941.605241zM507.957668 141.567613c-79.470519 0-174.250294 28.382328-174.250294 163.241391 0 129.698639 34.230808 213.469511 104.584579 255.784982 8.944734 5.332437 14.277171 14.965228 14.277171 25.286074l0 59.344868c0 15.309256-11.524945 28.0383-26.662187 29.414413-144.319839 14.449185-239.959684 67.429531-239.959684 95.983874 0 92.199563 177.346548 111.637158 325.966739 111.637158 148.792206 0 325.966739-19.26558 325.966739-111.637158 0-28.726356-95.639845-81.534688-239.959684-95.983874-15.48127-1.548127-27.006215-14.621199-26.662187-30.102469l1.376113-59.344868c0.172014-10.148833 5.676466-19.437594 14.277171-24.770032 70.525785-42.487485 103.208466-123.678145 103.208466-255.784982 0-135.031077-94.779775-163.241391-174.250294-163.241391L507.957668 141.567613 507.957668 141.567613z"/></svg>'
                chat_html += f'<div style="display: flex; align-items: flex-start; margin: 8px 0; flex-direction: row;">'
                chat_html += f'<div style="display: flex; flex-direction: column; align-items: center; margin-right: 10px;">'
                chat_html += f'<div style="width: 36px; height: 36px; border-radius: 4px; background-color: #888; flex-shrink: 0; display: flex; align-items: center; justify-content: center;">{user_svg}</div>'
                chat_html += f'<div style="font-size: 10px; color: #888; margin-top: 2px;">{time_str}</div>'
                chat_html += f'</div>'
                chat_html += f'<div style="padding: 10px 14px; font-size: 14px; line-height: 1.5; word-wrap: break-word; background-color: rgba(200,200,200,0.3); border-radius: 4px; color: inherit; border: 1px solid #888;">{content}</div>'
                chat_html += f'</div>'
        
        chat_html += f'<div style="text-align: center; font-size: 12px; color: #888; margin-top: 15px; padding: 10px;">- 共 {len(messages)} 条消息 -</div>'
        chat_html += '</div>'
    
    with st.container(border=True):
        st.markdown(chat_html, unsafe_allow_html=True)

with right_col:
    # 先定义所需变量
    analysis_data = {}
    try:
        analysis_json = session_data.get('analysis_json', '{}')
        if analysis_json and analysis_json != '{}':
            analysis_data = json.loads(analysis_json) if isinstance(analysis_json, str) else analysis_json
    except:
        analysis_data = {}
    
    ds = analysis_data.get('dimension_scores', {})
    total = session_data.get('total_score', 10)
    risk = "高风险" if total <= 8 else ("中风险" if total <= 12 else "正常")
    risk_color = "#f5222d" if total <= 8 else ("#faad14" if total <= 12 else "#52c41a")
    
    # 主标题(H5)+ 总分/风险(同层，右对齐div)
    st.markdown(
        f'<div style="position:relative;">'
        f'<h5 style="margin:0;">📊 AI深度质检报告</h5>'
        f'<div style="position:absolute;right:0;top:50%;transform:translateY(-50%);font-size:14px;">'
        f'总分: <b>{total}</b>/20 | 风险等级: <b style="color:{risk_color};">{risk}</b>'
        f'</div>'
        f'</div>',
        unsafe_allow_html=True
    )
    
    # === 四维度评分卡片(2x2，星级同行，固定高度100px，判定标题固定，内容带边框)===
    # 维度详细说明数据
    dimension_tooltips = {
        'professionalism': '专业性评分维度：评估客服对产品参数、功能、使用方法的掌握程度。5分标准：参数准确、解释清晰、能举一反三；3分标准：基本正确但不完整；1分标准：错误或无法回答。',
        'standardization': '标准化评分维度：评估服务流程规范性和礼貌用语。5分标准：礼貌用语完整、响应及时、结束规范；3分标准：基本规范但有瑕疵；1分标准：无礼貌、响应慢。',
        'policy_execution': '政策执行评分维度：评估促销和售后政策的传达准确性。5分标准：政策传达准确完整；3分标准：部分传达或有小错；1分标准：政策错误或遗漏。',
        'conversion': '转化能力评分维度：评估销售引导和需求挖掘能力。5分标准：主动挖掘需求、成功引导；3分标准：被动回答、引导弱；1分标准：无引导、用户流失。'
    }
    
    dim_data = [
        ('professionalism', '📚', '专业性', '产品知识准确性'),
        ('standardization', '📝', '标准化', '服务规范'),
        ('policy_execution', '📋', '政策执行', '促销/售后政策传达'),
        ('conversion', '🎯', '转化能力', '销售引导能力')
    ]
    
    for i in range(0, 4, 2):
        cols = st.columns(2)
        for j, (key, icon, name, desc) in enumerate(dim_data[i:i+2]):
            with cols[j]:
                dim_info = ds.get(key, {})
                score = dim_info.get('score', 2)
                reasoning = dim_info.get('reasoning', '无判定过程')
                stars = '⭐' * score + '☆' * (5 - score)
                tooltip_text = dimension_tooltips.get(key, '')
                
                with st.container(border=True):
                    # 标题行：图标+名称+问号tooltip  分数+星级(同一行flex布局)
                    st.markdown(
                        f'<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:4px;">'
                        f'<span style="font-size:16px;font-weight:bold;">'
                        f'{icon} {name}'
                        f'<span class="tooltip">'
                        f'<span class="tooltip-icon">?</span>'
                        f'<span class="tooltiptext">{tooltip_text}</span>'
                        f'</span>'
                        f'</span>'
                        f'<span><span style="font-size:14px;color:#888;margin-right:8px;">{score}/5</span><span style="font-size:16px;">{stars}</span></span>'
                        f'</div>',
                        unsafe_allow_html=True
                    )
                    # 副标题已删除
                    
                    # 【修改】判定过程和证据引用使用标签页展示
                    evidence = dim_info.get('evidence', [])
                    
                    # 检查是否为知识库未覆盖的情况
                    referenced_rules = dim_info.get('referenced_rules', [])
                    # 【修复】确保 referenced_rules 是列表且为空时才标记为通用标准
                    is_generic_reasoning = isinstance(referenced_rules, list) and len(referenced_rules) == 0
                    warning_icon = "⚠️ " if is_generic_reasoning else ""
                    
                    if evidence:
                        # 有证据引用时，使用标签页(自定义样式让证据引用靠右)
                        st.markdown('<style>.stTabs [data-baseweb="tab-list"] { justify-content: space-between; }</style>', unsafe_allow_html=True)
                        tab1, tab2 = st.tabs([f"{warning_icon}判定过程", "📎 证据引用"])
                        with tab1:
                            reasoning_style = (
                                'height:100px;overflow-y:auto;padding:0.5rem;border:1px solid rgba(255,255,255,0.2);background-color:rgba(250,173,20,0.15);border-radius:4px;font-size:13px;line-height:1.5;'
                                if is_generic_reasoning else
                                'height:100px;overflow-y:auto;padding:0.5rem;border:1px solid rgba(255,255,255,0.2);border-radius:4px;font-size:13px;line-height:1.5;'
                            )
                            st.markdown(f'<div style="{reasoning_style}">{reasoning}</div>', unsafe_allow_html=True)
                        with tab2:
                            evidence_style = 'height:100px;overflow-y:auto;padding:0.5rem;border:1px solid rgba(255,255,255,0.2);border-radius:4px;font-size:13px;line-height:1.5;color:#666;'
                            evidence_html = ''.join([f'<div style="margin-bottom:4px;">• {e}</div>' for e in evidence[:3]])
                            st.markdown(f'<div style="{evidence_style}">{evidence_html}</div>', unsafe_allow_html=True)
                    else:
                        # 无证据引用时，只显示判定过程
                        reasoning_style = (
                            'height:100px;overflow-y:auto;padding:0.5rem;border:1px solid rgba(255,255,255,0.2);background-color:rgba(250,173,20,0.15);border-radius:4px;font-size:13px;line-height:1.5;'
                            if is_generic_reasoning else
                            'height:100px;overflow-y:auto;padding:0.5rem;border:1px solid rgba(255,255,255,0.2);border-radius:4px;font-size:13px;line-height:1.5;'
                        )
                        st.markdown(
                            f'<div style="margin-top:6px;">'
                            f'<div style="font-size:13px;font-weight:bold;margin-bottom:4px;">{warning_icon}判定过程：</div>'
                            f'<div style="{reasoning_style}">{reasoning}</div>'
                            f'</div>',
                            unsafe_allow_html=True
                        )
    
    # 获取当前会话的最新矫正状态(从顶部移下来的逻辑)
    latest_correction = None
    correction_status = None
    if not corrections_df.empty:
        latest_correction = corrections_df.iloc[0]
        correction_status = latest_correction['status']
    
    # 判断按钮是否可用(简化版)
    # 只要有矫正记录 → 按钮禁用(不可重复矫正)
    # 无矫正记录 → 按钮可用
    can_correct = not has_correction
    
    # 矫正评分按钮(放在四维度卡片下方)
    session_key_prefix = session_id.replace("-", "_").replace(".", "_")
    btn_cols = st.columns([1, 1])
    
    with btn_cols[0]:
        if not can_correct:
            st.button("✅ 无需矫正", type="secondary", use_container_width=True, disabled=True, key=f"no_corr_disabled_{session_id}")
        else:
            if st.button("✅ 无需矫正", type="secondary", use_container_width=True, key=f"no_corr_{session_id}"):
                show_no_correction_dialog()
    
    with btn_cols[1]:
        if not can_correct:
            st.button("✏️ 矫正评分", type="primary", use_container_width=True, disabled=True, key=f"corr_disabled_{session_id}")
        else:
            # 检查是否需要重新打开评分矫正弹窗(从二次确认取消返回)
            if st.session_state.get('reopen_correction_dialog'):
                st.session_state.reopen_correction_dialog = False
                show_correction_dialog()
            elif st.button("✏️ 矫正评分", type="primary", use_container_width=True, key=f"open_correction_{session_id}"):
                show_correction_dialog()
    
    # 注意：由于checkbox直接操作session_state，不需要额外的对话框保持逻辑

# ========== 右栏下方：规则命中总览 ==========
with st.expander("📚 规则命中总览", expanded=False):
    # 从 analysis_data 中提取真实的规则命中数据
    referenced_rules = []
    rule_hit_count = {"hit": 0, "miss": 0, "partial": 0}
    
    # 遍历所有维度的 referenced_rules
    dim_names = {
        'professionalism': '专业性',
        'standardization': '标准化', 
        'policy_execution': '政策执行',
        'conversion': '转化能力'
    }
    
    for dim_key, dim_info in ds.items():
        rules = dim_info.get('referenced_rules', [])
        dim_score = dim_info.get('score', 0)
        for rule in rules:
            # 根据分值判断命中状态
            if dim_score >= 4:
                status = "已命中"
                status_color = "#52c41a"
                emoji = "✅"
                rule_hit_count["hit"] += 1
            elif dim_score >= 2:
                status = "部分命中"
                status_color = "#faad14"
                emoji = "⚠️"
                rule_hit_count["partial"] += 1
            else:
                status = "未命中"
                status_color = "#f5222d"
                emoji = "❌"
                rule_hit_count["miss"] += 1
            
            referenced_rules.append({
                "rule_name": rule,
                "dimension": dim_names.get(dim_key, dim_key),
                "score": dim_score,
                "status": status,
                "emoji": emoji,
                "color": status_color
            })
    
    # 命中统计(真实数据)
    st.markdown("**📊 命中统计**")
    with st.container(border=True):
        stats_cols = st.columns(4)
        with stats_cols[0]:
            st.metric("总规则", len(referenced_rules))
        with stats_cols[1]:
            st.metric("已命中", rule_hit_count["hit"], "✅" if rule_hit_count["hit"] > 0 else None)
        with stats_cols[2]:
            st.metric("未命中", rule_hit_count["miss"], "❌" if rule_hit_count["miss"] > 0 else None)
        with stats_cols[3]:
            st.metric("部分命中", rule_hit_count["partial"], "⚠️" if rule_hit_count["partial"] > 0 else None)
    
    # 具体规则明细(真实数据)
    st.markdown("**📋 具体规则明细**")
    
    if not referenced_rules:
        st.info("📭 本次分析未命中具体规则(使用通用标准评判)")
    else:
        # 2×2网格展示
        for i in range(0, len(referenced_rules), 2):
            cols = st.columns(2)
            for j in range(2):
                idx = i + j
                if idx < len(referenced_rules):
                    rule = referenced_rules[idx]
                    with cols[j]:
                        with st.container(border=True):
                            st.markdown(f"📋 **{rule['rule_name']}**")
                            st.caption(f"来源维度: {rule['dimension']} | 该维度得分: {rule['score']}/5")
                            st.markdown(f"<div style='text-align:right;color:{rule['color']};font-size:13px;margin-top:4px;'><b>{rule['emoji']} {rule['status']}</b></div>", unsafe_allow_html=True)

# ========== 矫正记录显示(可折叠，默认折叠) ==========
if not corrections_df.empty:
    with st.expander(f"📝 矫正记录 ({len(corrections_df)}条)", expanded=False):
        for _, corr in corrections_df.iterrows():
            with st.container(border=True):
                # 头部：时间 + 状态(使用与顶部元信息相同的样式)
                col1, col2 = st.columns([3, 1])
                with col1:
                    st.caption(f"🕐 {corr['created_at']}")
                with col2:
                    # 使用与顶部元信息相同的状态颜色和格式
                    if corr['status'] == 'approved':
                        status_text = "✅ 已确认"
                        status_color = "#52c41a"
                    elif corr['status'] == 'rejected':
                        status_text = "❌ 已拒绝，可重新提交"
                        status_color = "#f5222d"
                    elif corr['status'] == 'synced':
                        status_text = "🔄 已同步到知识库"
                        status_color = "#1890ff"
                    else:  # pending
                        status_text = "⏳ 待审核中，暂不可修改"
                        status_color = "#faad14"
                    
                    # 使用与顶部元信息相同的样式
                    st.markdown(f"<div style='text-align:right;'><span style='color: {status_color}; font-weight: bold; font-size: 14px;'>{status_text}</span></div>", unsafe_allow_html=True)
                
                # 显示修改的维度(2×2网格布局)
                try:
                    changed_fields = json.loads(corr['changed_fields']) if corr['changed_fields'] else []
                    if changed_fields:
                        st.markdown("**修改内容：**")
                        # 2×2网格展示
                        for i in range(0, len(changed_fields), 2):
                            cols = st.columns(2)
                            for j in range(2):
                                idx = i + j
                                if idx < len(changed_fields):
                                    field = changed_fields[idx]
                                    field_name = field.get('name', field.get('field', '未知'))
                                    old_val = field.get('old', '-')
                                    new_val = field.get('new', '-')
                                    has_change = old_val != new_val
                                    change_icon = "🔄" if has_change else "✓"
                                    change_text = f"{old_val} → {new_val}" if has_change else f"{old_val}分(确认)"
                                    
                                    with cols[j]:
                                        with st.container(border=True):
                                            st.markdown(f"{change_icon} **{field_name}**")
                                            st.markdown(f"<div style='font-size: 13px; color: #666;'>{change_text}</div>", unsafe_allow_html=True)
                                            if field.get('reason'):
                                                st.markdown(f"<div style='color: #888; font-size: 11px; margin-top: 4px;'>💬 {field['reason']}</div>", unsafe_allow_html=True)
                except:
                    pass
                
                # 显示其他说明
                if corr.get('other_reason'):
                    st.markdown(f"**其他说明：** 💬 {corr['other_reason']}")
                
                # 显示备注(reason字段中的非维度说明)
                if corr.get('reason') and not corr.get('other_reason'):
                    reason_lines = corr['reason'].split('\n')
                    non_dim_lines = [l for l in reason_lines if ':' not in l and l.strip()]
                    if non_dim_lines:
                        st.markdown(f"**备注：** {' '.join(non_dim_lines)}")
                
                # 显示拒绝理由(如果被拒绝)
                if corr['status'] == 'rejected' and corr.get('reject_reason'):
                    st.error(f"❌ 拒绝理由：{corr['reject_reason']}")
