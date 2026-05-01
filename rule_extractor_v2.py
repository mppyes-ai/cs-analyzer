"""规则提取器 v2 - 从矫正记录生成结构化规则

基于Prompt设计，调用Kimi API从矫正记录中提取结构化规则。

作者: 小虾米
更新: 2026-03-17
"""

import json
import os
import re
from typing import Dict, Optional

# 加载环境变量
from dotenv import load_dotenv
load_dotenv()

# 导入db_utils获取矫正记录
import sys
sys.path.insert(0, os.path.dirname(__file__))
from db_utils import get_correction_with_session, get_connection
from knowledge_base_v2 import save_rule_draft_v2, generate_combined_text

def format_messages_for_prompt(messages):
    """格式化消息用于Prompt"""
    formatted = []
    for msg in messages:
        role = msg.get('role', 'unknown')
        content = msg.get('content', '')
        formatted.append(f"[{role}] {content}")
    return "\n".join(formatted)


def extract_rule_from_session(session_id, session_data, reason=""):
    """从会话数据提取规则（V2版本 - 支持自动提取）
    
    Args:
        session_id: 会话ID
        session_data: 会话数据（DataFrame或Dict）
        reason: 矫正原因（可选）
    
    Returns:
        规则字典或None
    """
    try:
        # 解析会话数据
        if hasattr(session_data, 'to_dict'):
            session_dict = session_data.to_dict()
        else:
            session_dict = session_data
        
        # 获取消息内容
        messages = session_dict.get('messages', [])
        if isinstance(messages, str):
            messages = json.loads(messages)
        
        # 获取评分结果
        analysis_json = session_dict.get('analysis_json', '{}')
        if isinstance(analysis_json, str):
            analysis = json.loads(analysis_json)
        else:
            analysis = analysis_json
        
        # 构建规则数据
        rule_data = {
            'rule_type': 'scoring',
            'scene': {
                'category': analysis.get('session_analysis', {}).get('scene_category', '其他'),
                'sub_category': analysis.get('session_analysis', {}).get('user_intent', '其他'),
                'description': analysis.get('session_analysis', {}).get('theme', '')
            },
            'trigger': {
                'keywords': extract_keywords_from_messages(messages),
                'intent': analysis.get('session_analysis', {}).get('user_intent', '其他'),
                'mood': analysis.get('session_analysis', {}).get('user_sentiment', 'neutral')
            },
            'criteria': {
                'dimension': '综合',
                'checkpoints': [],
                'score_guide': {
                    'excellent': '回答准确，信息完整',
                    'good': '回答基本正确',
                    'average': '回答有瑕疵',
                    'poor': '回答错误'
                }
            },
            'example': {
                'good': {
                    'dialogue': format_messages_for_prompt(messages[:4]),
                    'reason': '回答准确'
                }
            },
            'source': {
                'type': 'auto_extract',
                'session_id': session_id,
                'reason': reason or '自动提取'
            }
        }
        
        # 生成规则ID
        import time
        rule_id = f"rule_auto_{session_id}_{int(time.time())}"
        rule_data['rule_id'] = rule_id
        
        return rule_data
        
    except Exception as e:
        print(f"   ⚠️ 规则提取失败: {e}")
        return None


def extract_keywords_from_messages(messages):
    """从消息中提取关键词"""
    keywords = []
    for msg in messages:
        content = msg.get('content', '')
        # 提取产品型号（如GD31, GD32）
        models = re.findall(r'[A-Z]{2}\d+', content)
        keywords.extend(models)
        
        # 提取关键名词（简单实现）
        if '价格' in content or '多少钱' in content:
            keywords.append('价格')
        if '安装' in content:
            keywords.append('安装')
        if '维修' in content:
            keywords.append('维修')
    
    return list(set(keywords))


# 原有函数保留...
# 注意：JSON示例中的大括号需要双写转义，避免被format()误解
RULE_EXTRACTION_PROMPT = """你是一位客服质检专家，擅长从人工矫正记录中提炼可复用的评分规则。

## 任务
分析以下矫正记录，提取关键信息，生成结构化的规则JSON。

## 输入数据
```json
{input_data}
```

## 输出格式
必须输出符合以下Schema的JSON：

```json
{{
  "rule_type": "scoring",
  "scene": {{
    "category": "一级场景(售前阶段/售中阶段/售后阶段/客诉处理)",
    "sub_category": "二级场景",
    "description": "场景描述"
  }},
  "trigger": {{
    "keywords": ["关键词1", "关键词2"],
    "intent": "意图标签",
    "mood": "情绪标记(positive/neutral/negative/urgent)",
    "dimension_hint": ["涉及维度"],
    "confidence_threshold": 0.7,
    "valid_from": "2026-03-17T00:00:00Z",
    "valid_to": null
  }},
  "rule": {{
    "dimension": "评分维度(professionalism/standardization/policy_execution/conversion)",
    "priority": "high",
    "criteria": "核心判定标准（一句话说清楚什么情况下扣分）",
    "score_guide": {{
      "5": {{"description": "5分标准", "checkpoints": ["检查点1", "检查点2"]}},
      "3": {{"description": "3分标准", "checkpoints": ["检查点1"]}},
      "1": {{"description": "1分标准", "checkpoints": ["检查点1", "检查点2"]}}
    }}
  }},
  "examples": [{{
    "case_id": "ex_001",
    "type": "negative",
    "dialogue_snippet": "关键对话片段",
    "ai_score_before": 2,
    "human_corrected_score": 1,
    "explanation": "为什么扣分",
    "key_moment": "关键博弈轮次"
  }}],
  "reasoning": {{
    "why_matters": "规则重要性",
    "business_impact": "不遵守的后果",
    "common_mistakes": ["常见错误1"],
    "best_practices": ["最佳实践1"]
  }},
  "tags": ["标签1", "标签2"]
}}
```

## 提取规则

1. **场景识别**：根据会话主题判断一级和二级场景
2. **触发条件**：从用户消息中提取关键词、意图、情绪
3. **评分规则**：提炼核心判定标准，设计5/3/1分checkpoints
4. **案例提取**：从对话中提取关键片段作为反面案例
5. **标签生成**：3-5个关键词标签

## 注意事项
- 规则要可复用，能指导其他类似会话的评分
- Checkpoints要具体可观察（如"3分钟内响应"而非"响应及时"）
- 从矫正原因和对话中提炼，不要照搬

直接输出JSON，不要Markdown代码块。"""


def fix_truncated_json(content: str) -> Optional[str]:
    """修复可能被截断的JSON
    
    尝试补全不完整的JSON字符串
    
    Args:
        content: 可能截断的JSON字符串
        
    Returns:
        修复后的JSON字符串，无法修复返回None
    """
    content = content.strip()
    
    # 统计括号
    open_braces = content.count('{')
    close_braces = content.count('}')
    open_brackets = content.count('[')
    close_brackets = content.count(']')
    
    # 如果括号平衡，可能是其他问题
    if open_braces == close_braces and open_brackets == close_brackets:
        return None
    
    # 尝试补全缺失的括号
    fixed = content
    
    # 如果在字符串中截断，先找到最后一个完整的键值对
    last_valid_pos = len(fixed)
    
    # 从后向前找，找到可以安全截断的位置
    for i in range(len(fixed) - 1, -1, -1):
        if fixed[i] in [',', '}', ']']:
            # 检查这是否是一个可以结束的位置
            test_content = fixed[:i+1]
            # 补全缺失的括号
            while test_content.count('{') > test_content.count('}'):
                test_content += '}'
            while test_content.count('[') > test_content.count(']'):
                test_content += ']'
            
            try:
                json.loads(test_content)
                return test_content
            except:
                continue
    
    # 简单补全策略
    while open_braces > close_braces:
        fixed += '}'
        close_braces += 1
    while open_brackets > close_brackets:
        fixed += ']'
        close_brackets += 1
    
    try:
        json.loads(fixed)
        return fixed
    except:
        return None


def prepare_extraction_input(correction_id: int) -> Optional[Dict]:
    """准备规则提取的输入数据
    
    Args:
        correction_id: 矫正记录ID
        
    Returns:
        输入数据字典，用于填充Prompt
    """
    # 获取矫正记录及关联会话
    data = get_correction_with_session(correction_id)
    if not data:
        return None
    
    # 解析消息
    try:
        messages = json.loads(data.get('messages', '[]'))
    except:
        messages = []
    
    # 解析改分字段
    try:
        changed_fields = json.loads(data.get('changed_fields', '[]'))
    except:
        changed_fields = []
    
    # 构建输入
    input_data = {
        "session_id": data.get('session_id'),
        "session_summary": data.get('summary', ''),
        "messages": messages,
        "correction": {
            "changed_fields": [f["field"] for f in changed_fields],
            "reason": data.get('reason', ''),
            "corrected_by": data.get('corrected_by', 'admin')
        }
    }
    
    return input_data


def extract_rule_with_kimi(input_data: Dict, api_key: str = None) -> Optional[Dict]:
    """调用Kimi API提取规则 (httpx方案，禁用系统代理)
    
    Args:
        input_data: 输入数据
        api_key: Moonshot API Key（如未提供，尝试从环境变量读取）
        
    Returns:
        提取的规则JSON
    """
    if api_key is None:
        api_key = os.getenv("MOONSHOT_API_KEY")
        if not api_key:
            raise ValueError("未提供API Key，请设置MOONSHOT_API_KEY环境变量")
    
    # 填充Prompt
    prompt = RULE_EXTRACTION_PROMPT.format(
        input_data=json.dumps(input_data, ensure_ascii=False, indent=2)
    )
    
    # 构建请求体
    request_body = {
        "model": "kimi-k2.5",
        "messages": [
            {"role": "system", "content": "你是客服质检专家，专门从矫正记录中提取结构化规则。"},
            {"role": "user", "content": prompt}
        ],
        "temperature": 1,
        "max_tokens": 8000  # 增加token限制避免截断
    }
    
    # 使用httpx调用API，禁用系统代理
    import httpx
    
    # 创建不读取系统代理配置的transport
    transport = httpx.HTTPTransport()
    client = httpx.Client(transport=transport, timeout=60)
    
    try:
        response = client.post(
            "https://api.moonshot.cn/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            json=request_body
        )
        response.raise_for_status()
        
        response_data = response.json()
        content = response_data['choices'][0]['message']['content']
        
        # 解析JSON
        try:
            # 去除可能的Markdown代码块
            if content.startswith("```json"):
                content = content[7:]
            if content.startswith("```"):
                content = content[3:]
            if content.endswith("```"):
                content = content[:-3]
            
            content = content.strip()
            
            # 尝试解析JSON
            try:
                rule_data = json.loads(content)
                return rule_data
            except json.JSONDecodeError as e:
                # 尝试修复截断的JSON
                print(f"JSON解析失败，尝试修复截断...")
                fixed_content = fix_truncated_json(content)
                if fixed_content:
                    rule_data = json.loads(fixed_content)
                    print(f"✅ JSON修复成功")
                    return rule_data
                raise e
                
        except json.JSONDecodeError as e:
            print(f"JSON解析失败: {e}")
            print(f"原始内容前500字符: {content[:500]}")
            return None
            
    except httpx.HTTPError as e:
        print(f"HTTP请求失败: {e}")
        return None
    except Exception as e:
        print(f"API调用失败: {e}")
        return None
    finally:
        client.close()


def process_correction_to_rule(correction_id: int, api_key: str = None, skip_similar: bool = True) -> Optional[str]:
    """处理单条矫正记录，提取规则并保存（支持去重）
    
    Args:
        correction_id: 矫正记录ID
        api_key: Moonshot API Key
        skip_similar: 是否跳过相似场景（默认True）
        
    Returns:
        生成的rule_id，失败或重复返回None
    """
    print(f"📝 处理矫正记录 #{correction_id}...")
    
    # 1. 准备输入
    input_data = prepare_extraction_input(correction_id)
    if not input_data:
        print(f"⚠️ 无法获取矫正记录: #{correction_id}")
        return None
    
    # 2. 检查是否已有相似规则（场景去重）
    if skip_similar:
        # 从会话数据构建场景指纹
        session_summary = input_data.get('session_summary', '')
        messages = input_data.get('messages', [])
        
        # 提取场景关键词
        scene_keywords = extract_scene_keywords(session_summary, messages)
        
        # 检查相似场景
        similar_rule = find_similar_scene(scene_keywords, input_data['session_id'])
        if similar_rule:
            print(f"⏭️ 跳过: 发现相似规则 {similar_rule['rule_id']} (相似度: {similar_rule['similarity']:.2f})")
            return None
    
    # 3. 调用Kimi提取规则
    rule_data = extract_rule_with_kimi(input_data, api_key)
    if not rule_data:
        print(f"⚠️ 规则提取失败: #{correction_id}")
        return None
    
    # 4. 再次检查规则级别的相似度（基于提取的场景）
    if skip_similar:
        scene_text = f"{rule_data.get('scene', {}).get('category', '')} {rule_data.get('scene', {}).get('sub_category', '')}"
        similar_rule = find_similar_scene_by_text(scene_text)
        if similar_rule:
            print(f"⏭️ 跳过: 与现有规则 {similar_rule['rule_id']} 场景重复")
            return None
    
    # 5. 补充来源信息
    rule_data['source'] = {
        "type": "correction",
        "session_id": input_data['session_id'],
        "correction_id": str(correction_id),
        "staff_name": input_data['correction']['corrected_by']
    }
    
    # 6. 保存到数据库
    rule_id = save_rule_draft_v2(rule_data, str(correction_id))
    print(f"✅ 规则草案已保存: {rule_id}")
    
    return rule_id


def extract_scene_keywords(summary: str, messages: list) -> set:
    """从会话中提取场景关键词"""
    import re
    
    keywords = set()
    
    # 从摘要提取
    if summary:
        # 提取中文关键词
        chinese_words = re.findall(r'[\u4e00-\u9fa5]{2,}', summary)
        keywords.update(chinese_words)
    
    # 从消息提取
    for msg in messages[:5]:  # 只看前5条
        content = msg.get('content', '')
        if content:
            # 提取产品相关词
            product_keywords = re.findall(r'(安装|维修|售后|预约|退货|价格|活动|优惠|保修|发货)', content)
            keywords.update(product_keywords)
            
            # 提取品牌/型号
            model_keywords = re.findall(r'[A-Z]{1,2}\d{2,4}', content)
            keywords.update(model_keywords)
    
    return keywords


def find_similar_scene(keywords: set, session_id: str, threshold: float = 0.6) -> Optional[Dict]:
    """查找相似场景的规则（基于关键词重叠）
    
    Args:
        keywords: 当前场景关键词集合
        session_id: 当前会话ID（排除自身）
        threshold: 相似度阈值（关键词重叠率）
        
    Returns:
        相似规则信息，无则返回None
    """
    import sqlite3
    import json
    from difflib import SequenceMatcher
    
    if not keywords:
        return None
    
    conn = sqlite3.connect('./data/cs_analyzer_new.db')
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # 获取所有现有规则（排除当前会话来源的）
    cursor.execute('''
        SELECT rule_id, scene_category, scene_sub_category, scene_description, 
               trigger_keywords, rule_dimension, rule_criteria
        FROM rules 
        WHERE status != 'deleted' 
          AND (source_session_id != ? OR source_session_id IS NULL)
    ''', (session_id,))
    
    rules = cursor.fetchall()
    conn.close()
    
    if not rules:
        return None
    
    # 计算相似度
    best_match = None
    best_score = 0
    
    for rule in rules:
        # 构建规则关键词集合
        rule_keywords = set()
        
        # 从场景文本提取
        scene_text = f"{rule['scene_category']} {rule['scene_sub_category']} {rule['scene_description']}"
        rule_keywords.update(re.findall(r'[\u4e00-\u9fa5]{2,}', scene_text))
        
        # 从触发关键词提取
        try:
            trigger_kw = json.loads(rule['trigger_keywords'] or '[]')
            rule_keywords.update(trigger_kw)
        except:
            pass
        
        # 计算Jaccard相似度
        if rule_keywords:
            intersection = keywords & rule_keywords
            union = keywords | rule_keywords
            similarity = len(intersection) / len(union) if union else 0
            
            # 或者使用文本相似度
            text_sim = SequenceMatcher(None, ' '.join(keywords), ' '.join(rule_keywords)).ratio()
            
            # 取两者较高者
            score = max(similarity, text_sim)
            
            if score > best_score and score >= threshold:
                best_score = score
                best_match = {
                    'rule_id': rule['rule_id'],
                    'similarity': score,
                    'scene': f"{rule['scene_category']}/{rule['scene_sub_category']}",
                    'criteria': rule['rule_criteria']
                }
    
    return best_match


def find_similar_scene_by_text(scene_text: str, threshold: float = 0.7) -> Optional[Dict]:
    """基于场景文本查找相似规则"""
    from difflib import SequenceMatcher
    import sqlite3
    
    conn = sqlite3.connect('./data/cs_analyzer_new.db')
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT rule_id, scene_category, scene_sub_category, scene_description
        FROM rules 
        WHERE status != 'deleted'
    ''')
    
    rules = cursor.fetchall()
    conn.close()
    
    best_match = None
    best_score = 0
    
    for rule in rules:
        existing_text = f"{rule['scene_category']} {rule['scene_sub_category']} {rule['scene_description']}"
        score = SequenceMatcher(None, scene_text, existing_text).ratio()
        
        if score > best_score and score >= threshold:
            best_score = score
            best_match = {
                'rule_id': rule['rule_id'],
                'similarity': score,
                'scene': existing_text[:50]
            }
    
    return best_match


def process_all_corrections(api_key: str = None, skip_similar: bool = True) -> Dict:
    """批量处理所有矫正记录（包括确认AI正确的）
    
    Args:
        api_key: Moonshot API Key
        skip_similar: 是否跳过相似场景
        
    Returns:
        处理统计
    """
    import sqlite3
    import pandas as pd
    
    # 获取所有矫正记录（无论是否已矫正）
    conn = sqlite3.connect('./data/cs_analyzer_new.db')
    df = pd.read_sql_query("""
        SELECT c.* FROM corrections c
        LEFT JOIN rules r ON c.correction_id = r.source_correction_id
        WHERE r.rule_id IS NULL
        ORDER BY c.created_at DESC
    """, conn)
    conn.close()
    
    print(f"📋 发现 {len(df)} 条待处理矫正记录（含确认AI正确的）")
    
    stats = {
        "total": len(df),
        "success": 0,
        "failed": 0,
        "skipped_similar": 0,
        "rule_ids": []
    }
    
    for _, row in df.iterrows():
        correction_id = row['correction_id']
        status = row.get('status', '')
        
        # 显示状态标记
        status_mark = "✓" if status == 'no_action' else "✏"
        print(f"\n{status_mark} 处理矫正记录 #{correction_id} (status: {status or 'unknown'})")
        
        rule_id = process_correction_to_rule(correction_id, api_key, skip_similar)
        
        if rule_id:
            stats["success"] += 1
            stats["rule_ids"].append(rule_id)
        else:
            # 判断是失败还是跳过
            import sqlite3
            conn = sqlite3.connect('./data/cs_analyzer_new.db')
            cursor = conn.cursor()
            cursor.execute(
                "SELECT 1 FROM rules WHERE source_correction_id = ?",
                (correction_id,)
            )
            exists = cursor.fetchone()
            conn.close()
            
            if exists:
                stats["skipped_similar"] += 1
            else:
                stats["failed"] += 1
    
    print(f"\n📊 处理完成:")
    print(f"  成功: {stats['success']}")
    print(f"  跳过(相似): {stats['skipped_similar']}")
    print(f"  失败: {stats['failed']}")
    return stats


# 保持向后兼容的别名
def process_all_pending_corrections(api_key: str = None) -> Dict:
    """兼容旧接口，处理所有记录"""
    return process_all_corrections(api_key, skip_similar=True)


def test_extraction(correction_id: int = None):
    """测试规则提取
    
    Args:
        correction_id: 指定矫正记录ID，如未指定则取最新一条
    """
    if correction_id is None:
        # 获取最新一条待处理记录
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id FROM corrections WHERE status = 'pending' ORDER BY created_at DESC LIMIT 1"
        )
        result = cursor.fetchone()
        conn.close()
        
        if not result:
            print("⚠️ 没有待处理的矫正记录")
            return
        
        correction_id = result[0]
    
    print(f"🧪 测试提取规则，矫正记录ID: {correction_id}\n")
    
    # 准备输入
    input_data = prepare_extraction_input(correction_id)
    if not input_data:
        print("⚠️ 无法获取矫正记录")
        return
    
    print("输入数据:")
    print(json.dumps(input_data, ensure_ascii=False, indent=2))
    print("\n" + "="*50 + "\n")
    
    # 显示Prompt
    prompt = RULE_EXTRACTION_PROMPT.format(
        input_data=json.dumps(input_data, ensure_ascii=False, indent=2)
    )
    print("Prompt预览 (前500字符):")
    print(prompt[:500] + "...")
    
    return input_data


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1:
        if sys.argv[1] == "test":
            # 测试模式
            correction_id = int(sys.argv[2]) if len(sys.argv) > 2 else None
            test_extraction(correction_id)
        elif sys.argv[1] == "process":
            # 处理模式
            api_key = os.getenv("MOONSHOT_API_KEY")
            if not api_key:
                print("⚠️ 请设置MOONSHOT_API_KEY环境变量")
                sys.exit(1)
            
            # 检查是否强制重新处理（--force 参数）
            skip_similar = "--force" not in sys.argv
            
            if len(sys.argv) > 2 and sys.argv[2].isdigit():
                # 处理单条
                correction_id = int(sys.argv[2])
                rule_id = process_correction_to_rule(correction_id, api_key, skip_similar)
                if rule_id:
                    print(f"\n✅ 生成规则ID: {rule_id}")
            else:
                # 批量处理所有记录
                stats = process_all_corrections(api_key, skip_similar)
                print(f"\n统计: {stats}")
        else:
            print("用法:")
            print("  python3 rule_extractor_v2.py test [correction_id]     - 测试提取")
            print("  python3 rule_extractor_v2.py process [correction_id]  - 处理单条/所有")
            print("  python3 rule_extractor_v2.py process --force           - 强制处理（跳过去重）")
    else:
        print("用法:")
        print("  python rule_extractor_v2.py test [correction_id]     - 测试提取")
        print("  python rule_extractor_v2.py process [correction_id]  - 处理单条/所有")
        print("  python rule_extractor_v2.py process --force           - 强制处理（跳过去重）")
