"""智能评分引擎 v2.6.1 - 基于规则知识库的4维度评分

核心功能：
1. 会话预分析（scene/intent/sentiment）
2. 规则检索（SQLite + LanceDB混合）
3. CoT评分输出（命中规则 + 判定过程）
4. 结果可解释化
5. 【v2.4】批量评分支持
6. 【v2.6.1】跨场景合并评分（不再按场景分组）

作者: 小虾米
更新: 2026-04-04（v2.6.1: 跨场景合并优化）
"""

import json
import os
import asyncio
import re
from typing import Dict, List, Optional, Tuple
from datetime import datetime

# 导入知识库模块
import sys
sys.path.insert(0, os.path.dirname(__file__))

from knowledge_base_v2 import (
    get_approved_rules, search_rules_by_vector, generate_combined_text
)

# 导入本地意图分类器
try:
    from intent_classifier_v3 import FunnelIntentClassifier, IntentClassificationResult
    INTENT_CLASSIFIER_AVAILABLE = True
except ImportError:
    INTENT_CLASSIFIER_AVAILABLE = False
    print("⚠️ 漏斗式意图分类器未导入，将使用关键词规则匹配")

# 导入混合检索
try:
    from hybrid_retriever import HybridRuleRetriever
    HYBRID_RETRIEVER_AVAILABLE = True
except ImportError:
    HYBRID_RETRIEVER_AVAILABLE = False
    print("⚠️ 混合检索模块未导入，将使用基础向量检索")


# ========== 使用统一Embedding单例 ==========
from embedding_utils import get_embedding_model

# ========== 自定义异常类 ==========

class ScoringError(Exception):
    """AI评分失败异常"""
    def __init__(self, message: str, error_type: str = "unknown", details: Dict = None):
        self.message = message
        self.error_type = error_type
        self.details = details or {}
        super().__init__(self.message)
    
    def __str__(self):
        return f"[{self.error_type}] {self.message}"


# ========== 评分Prompt模板 ==========

SCORING_PROMPT_TEMPLATE = """你是一位专业的客服质检专家，负责对客服会话进行4维度质量评分。

## 评分维度
1. **专业性 (Professionalism)** - 产品知识准确性
2. **标准化 (Standardization)** - 服务规范（礼貌用语、响应速度）
3. **政策执行 (Policy Execution)** - 促销/售后政策传达
4. **转化能力 (Conversion)** - 销售引导能力

## 本次评分参考规则

{retrieved_rules}

## 会话内容

```json
{session_data}
```

## 评分要求

1. **先分析会话**：提取主题、用户意图、情绪、关键博弈轮次
2. **逐维度评分**：1-5分，基于checkpoints逐项检查
3. **引用规则**：明确说明参考了哪条规则（rule_id）
4. **输出推理过程**：用自然语言描述为什么给这个分数，直接陈述事实和依据，不要加"判定过程："等前缀
5. **总分计算**：4-20分，风险分级（🔴高风险≤8 🟡中风险9-12 🟢正常≥13）

## 输出格式（必须严格遵循）

```json
{{
  "session_analysis": {{
    "theme": "会话主题（20-30字简介，如：用户咨询厨房装蜂窝大板如何留孔，客服提供预埋烟管方案）",
    "user_intent": "用户意图",
    "user_sentiment": "用户情绪",
    "key_moments": ["关键轮次1", "关键轮次2"]
  }},
  "dimension_scores": {{
    "professionalism": {{
      "score": 3,
      "reasoning": "用户询问产品参数，客服回答准确但缺少对比说明，符合3分标准",
      "evidence": ["证据片段1", "证据片段2"],
      "referenced_rules": ["rule_id"]  // 知识库规则ID列表，空数组表示使用通用标准
    }},
    "standardization": {{
      "score": 2,
      "reasoning": "响应及时但礼貌用语不够规范，存在服务瑕疵",
      "evidence": ["证据片段"],
      "referenced_rules": []  // 空数组表示知识库未覆盖，基于通用标准评判
    }},
    "policy_execution": {{
      "score": 3,
      "reasoning": "政策传达准确但时机把握不当",
      "evidence": [],
      "referenced_rules": ["rule_id"]
    }},
    "conversion": {{
      "score": 2,
      "reasoning": "未主动挖掘用户需求，错失转化机会",
      "evidence": [],
      "referenced_rules": []  // 空数组表示知识库未覆盖
    }}
  }},
  "summary": {{
    "total_score": 10,
    "risk_level": "中风险",
    "strengths": ["亮点1", "亮点2"],
    "issues": ["问题1", "问题2"],
    "suggestions": ["建议1", "建议2"]
  }}
}}
```

## 评分标准参考

### 5分标准（优秀）
- 完全符合checkpoints所有要求
- 超出预期的表现

### 3分标准（及格）
- 基本符合但存在瑕疵
- 有改进空间

### 1分标准（不合格）
- 触碰底线行为
- 严重违规

### 引用规则说明
1. **使用知识库规则时**：`referenced_rules`填写具体的规则ID（如：`["rule_xxx"]`）
2. **知识库未覆盖时**：`referenced_rules`必须填写空数组（`[]`），严禁自行编造规则名称或描述
3. **检查方式**：如果本次评分参考的规则列表为空，则所有维度的`referenced_rules`都必须是`[]`

### 通用标准定义
- **电商行业通用标准**：响应时效、服务态度、问题闭环等电商客服基本规范
- **客服服务通用标准**：首响及时、礼貌用语、主动解决、结束规范等服务行业基本要求
- **消费者认可标准**：信息准确、不推诿、有效解决用户问题等消费者普遍期望

直接输出JSON，不要Markdown代码块。"""


BATCH_SCORING_PROMPT_TEMPLATE = """你是一位专业的客服质检专家，请对以下{count}个客服会话分别进行4维度质量评分。

## 评分维度（每个会话单独评分）
1. **专业性** - 产品知识准确性
2. **标准化** - 服务规范
3. **政策执行** - 政策传达
4. **转化能力** - 销售引导

## 评分规则（适用于所有会话）

{retrieved_rules}

## 会话内容

{sessions_content}

## 输出格式

请严格返回JSON数组，数组长度必须为{count}，每个元素对应一个会话的评分结果：

```json
[
  {{
    "session_analysis": {{
      "theme": "会话主题（20-30字）",
      "user_intent": "用户意图",
      "user_sentiment": "用户情绪",
      "key_moments": ["关键轮次1"]
    }},
    "dimension_scores": {{
      "professionalism": {{"score": 3, "reasoning": "...", "evidence": [], "referenced_rules": []}},
      "standardization": {{"score": 3, "reasoning": "...", "evidence": [], "referenced_rules": []}},
      "policy_execution": {{"score": 3, "reasoning": "...", "evidence": [], "referenced_rules": []}},
      "conversion": {{"score": 3, "reasoning": "...", "evidence": [], "referenced_rules": []}}
    }},
    "summary": {{
      "total_score": 12,
      "risk_level": "中风险",
      "strengths": ["亮点1"],
      "issues": ["问题1"],
      "suggestions": ["建议1"]
    }}
  }},
  ... // 共{count}个元素
]
```

注意：
1. 必须返回JSON数组，数组长度严格等于{count}
2. 数组元素顺序与输入会话顺序一致
3. 每个会话独立评分，互不影响
4. 直接输出JSON数组，不要Markdown代码块。"""


# ========== 核心评分类 ==========

class SmartScoringEngine:
    """智能评分引擎 v2.4 - 支持异步批量评分"""
    
    def __init__(self, api_key: str = None, embedding_model=None, use_local_intent: bool = True):
        """
        Args:
            api_key: Moonshot API Key
            embedding_model: 向量模型（可选，默认使用全局单例）
            use_local_intent: 是否使用本地意图分类（默认True）
        """
        self.api_key = api_key or os.getenv("MOONSHOT_API_KEY")
        # 使用传入的模型或全局单例
        self.embedding_model = embedding_model or get_embedding_model()
        self.use_local_intent = use_local_intent
        
        # 初始化漏斗式意图分类器
        self.intent_classifier = None
        if use_local_intent:
            try:
                from intent_classifier_v3 import RobustIntentClassifier
                self.intent_classifier = RobustIntentClassifier()
                print("✅ 漏斗式意图分类器已初始化 (规则优先，qwen2.5兜底)")
            except Exception as e:
                print(f"⚠️ 漏斗式意图分类器初始化失败: {e}，将使用关键词规则")
        
    def _analyze_session_pre(self, messages: List[Dict]) -> Dict:
        """会话预分析 - 漏斗式分类
        
        第一层：毫秒级规则匹配（高频简单意图）
        第二层：本地Qwen3:4b（复杂/长尾意图）
        第三层：关键词规则回退
        
        Args:
            messages: 消息列表
            
        Returns:
            预分析结果
        """
        # 尝试使用漏斗式分类器
        if self.intent_classifier:
            try:
                result = self.intent_classifier.classify(messages)
                if result:
                    return {
                        "scene": result.scene,
                        "sub_scene": result.sub_scene,
                        "intent": result.intent,
                        "sentiment": result.sentiment,
                        "confidence": result.confidence,
                        "reasoning": result.reasoning,
                        "source": result.source,
                        "latency_ms": result.latency_ms
                    }
            except Exception as e:
                print(f"⚠️ 漏斗式分类失败: {e}，回退到关键词规则")
        
        # 回退：关键词规则匹配
        return self._analyze_session_keyword_fallback(messages)
    
    def _compact_session_for_prompt(self, session_data: dict) -> str:
        """【优化C】将会话数据精简为纯文本格式，去掉元数据
        
        原格式：JSON 包含 session_id, timestamp, sender 等元数据
        新格式：[角色] 内容
        
        预计节省：输入 token 减少 30-40%
        """
        messages = session_data.get('messages', [])
        lines = []
        for m in messages:
            role = m.get('role', 'unknown')
            content = m.get('content', '').strip()
            if content:
                if role in ('user', 'customer'):
                    lines.append(f"[用户] {content}")
                elif role == 'staff':
                    lines.append(f"[客服] {content}")
                else:
                    lines.append(f"[{role}] {content}")
        return '\n'.join(lines)
    
    def _analyze_session_keyword_fallback(self, messages: List[Dict]) -> Dict:
        """关键词规则匹配（回退方案）
        
        Args:
            messages: 消息列表
            
        Returns:
            预分析结果
        """
        user_messages = [m['content'] for m in messages if m.get('role') == 'user']
        all_text = ' '.join(user_messages)
        
        # 场景识别
        scene_keywords = {
            "售前咨询": ["多少钱", "价格", "优惠", "活动", "有没有", "推荐"],
            "安装咨询": ["安装", "尺寸", "预留", "辅材", "怎么装"],
            "客诉处理": ["骗子", "投诉", "退货", "退款", "不满意", "质量差"],
            "售后维修": ["坏了", "故障", "维修", "保修", "售后"],
            "活动咨询": ["国补", "补贴", "赠品", "保价", "活动规则"]
        }
        
        scene_scores = {}
        for scene, keywords in scene_keywords.items():
            score = sum(1 for kw in keywords if kw in all_text)
            scene_scores[scene] = score
        
        detected_scene = max(scene_scores, key=scene_scores.get) if max(scene_scores.values()) > 0 else "其他"
        
        # 情绪识别
        negative_words = ["骗子", "垃圾", "投诉", "退钱", "欺诈", "糊弄", "愤怒", "生气"]
        urgent_words = ["马上", "立刻", "赶紧", "急", "催"]
        
        negative_count = sum(1 for w in negative_words if w in all_text)
        urgent_count = sum(1 for w in urgent_words if w in all_text)
        
        if negative_count >= 2:
            sentiment = "negative"
        elif urgent_count >= 2:
            sentiment = "urgent"
        elif negative_count == 1:
            sentiment = "neutral"
        else:
            sentiment = "positive"
        
        # 意图识别
        intent_keywords = {
            "咨询": ["多少钱", "怎么样", "有什么", "推荐"],
            "客诉": ["骗子", "投诉", "欺骗"],
            "退款": ["退货", "退款", "不要了"],
            "维修": ["坏了", "故障", "维修"],
            "安装": ["安装", "尺寸", "预留"]
        }
        
        intent_scores = {}
        for intent, keywords in intent_keywords.items():
            score = sum(1 for kw in keywords if kw in all_text)
            intent_scores[intent] = score
        
        detected_intent = max(intent_scores, key=intent_scores.get) if max(intent_scores.values()) > 0 else "其他"
        
        return {
            "scene": detected_scene,
            "sub_scene": "其他",
            "intent": detected_intent,
            "sentiment": sentiment,
            "confidence": 0.5,
            "reasoning": "基于关键词规则匹配",
            "source": "keyword"  # 标记来源
        }
    
    def _retrieve_rules(self, session_analysis: Dict, messages_text: str) -> List[Dict]:
        """检索相关规则（使用混合检索）
        
        Args:
            session_analysis: 会话预分析结果
            messages_text: 会话文本（用于向量检索）
            
        Returns:
            相关规则列表
        """
        # 优先使用混合检索
        if HYBRID_RETRIEVER_AVAILABLE:
            try:
                retriever = HybridRuleRetriever(embedding_model=self.embedding_model)
                rules = retriever.search(
                    query=messages_text,
                    scene_filter=session_analysis.get('scene'),
                    top_k=5,
                    use_hybrid=True
                )
                if rules:
                    print(f"📚 混合检索返回 {len(rules)} 条规则")
                    return rules
            except Exception as e:
                print(f"⚠️ 混合检索失败: {e}，回退到基础检索")
        
        # 回退：基础检索（元数据过滤 + 向量检索）
        rules = []
        
        # 1. 基于元数据过滤获取规则
        scene_rules = get_approved_rules(
            scene_category=session_analysis.get('scene')
        )
        rules.extend(scene_rules)
        
        # 2. 向量检索补充
        try:
            vector_rules = search_rules_by_vector(
                query_text=messages_text,
                top_k=3,
                scene_filter=session_analysis.get('scene'),
                embedding_model=self.embedding_model
            )
            
            # 合并去重
            existing_ids = {r['rule_id'] for r in rules}
            for vr in vector_rules:
                if vr['rule_id'] not in existing_ids:
                    rules.append(vr)
        except Exception as e:
            print(f"向量检索失败: {e}")
        
        return rules[:5]  # 最多返回5条规则
    
    def _format_rules_for_prompt(self, rules: List[Dict]) -> str:
        """将规则格式化为Prompt文本
        
        Args:
            rules: 规则列表
            
        Returns:
            格式化后的规则文本
        """
        if not rules:
            return "（知识库中暂无针对该场景的明确规则，请基于通用标准评判）"
        
        formatted = []
        for i, rule in enumerate(rules, 1):
            rule_text = f"""
### 规则{i}: {rule.get('rule_id', 'N/A')}
- **适用场景**: {rule.get('scene_category', 'N/A')} / {rule.get('scene_sub_category', 'N/A')}
- **触发条件**: {', '.join(rule.get('trigger_keywords', []))}
- **评分维度**: {rule.get('rule_dimension', 'N/A')}
- **核心判定**: {rule.get('rule_criteria', 'N/A')}

**5分标准**: {rule.get('rule_score_guide', {}).get('5', {}).get('description', 'N/A')}
- Checkpoints: {', '.join(rule.get('rule_score_guide', {}).get('5', {}).get('checkpoints', []))}

**3分标准**: {rule.get('rule_score_guide', {}).get('3', {}).get('description', 'N/A')}
- Checkpoints: {', '.join(rule.get('rule_score_guide', {}).get('3', {}).get('checkpoints', []))}

**1分标准**: {rule.get('rule_score_guide', {}).get('1', {}).get('description', 'N/A')}
- Checkpoints: {', '.join(rule.get('rule_score_guide', {}).get('1', {}).get('checkpoints', []))}
"""
            formatted.append(rule_text)
        
        return "\n---\n".join(formatted)
    
    # ========== 单通评分（保留兼容） ==========
    
    def score_session(self, session_data: Dict) -> Dict:
        """对会话进行智能评分
        
        Args:
            session_data: 会话数据，包含messages等
            
        Returns:
            评分结果JSON
            
        Raises:
            ScoringError: 当AI评分失败时抛出，包含具体失败原因
        """
        messages = session_data.get('messages', [])
        
        # 1. 会话预分析
        pre_analysis = self._analyze_session_pre(messages)
        print(f"📊 预分析: {pre_analysis}")
        
        # 2. 规则检索
        messages_text = '\n'.join([f"{m.get('role')}: {m.get('content')}" for m in messages[:10]])
        retrieved_rules = self._retrieve_rules(pre_analysis, messages_text)
        print(f"📚 检索到 {len(retrieved_rules)} 条规则")
        
        # 3. 构建Prompt
        rules_text = self._format_rules_for_prompt(retrieved_rules)
        prompt = SCORING_PROMPT_TEMPLATE.format(
            retrieved_rules=rules_text,
            session_data=json.dumps(session_data, ensure_ascii=False, indent=2)
        )
        
        # 4. 调用Kimi API
        try:
            import openai
            import time
            
            client = openai.OpenAI(
                api_key=self.api_key,
                base_url="https://api.moonshot.cn/v1",
                max_retries=2
            )
            
            model = os.getenv('KIMI_MODEL', 'kimi-k2.5')
            
            max_retries = 3
            base_delay = 2.0
            last_exception = None
            
            for attempt in range(max_retries):
                try:
                    # 【v2.5】添加120秒超时保护，防止Worker僵死
                    response = client.chat.completions.create(
                        model=model,
                        messages=[
                            {"role": "system", "content": "你是专业的客服质检专家，严格按JSON格式输出评分结果。"},
                            {"role": "user", "content": prompt}
                        ],
                        temperature=1,
                        max_tokens=int(os.getenv('KIMI_MAX_TOKENS', 16000)),
                        timeout=int(os.getenv('KIMI_API_TIMEOUT', 300))  # 【v2.5】从.env读取超时时间（默认300秒）
                    )
                    break
                    
                except Exception as e:
                    last_exception = e
                    error_msg = str(e)
                    
                    if "429" in error_msg or "Too Many Requests" in error_msg:
                        if attempt < max_retries - 1:
                            delay = base_delay * (2 ** attempt)
                            print(f"   ⏳ API限流(429)，等待 {delay:.1f}s 后重试 (第{attempt+1}/{max_retries}次)...")
                            time.sleep(delay)
                            continue
                        else:
                            print(f"   ⚠️ 限流重试耗尽，最后一次错误: {error_msg[:100]}")
                            raise
                    else:
                        raise
            else:
                raise last_exception if last_exception else Exception("API调用失败")
            
            content = response.choices[0].message.content
            
            result = self._parse_json_robust(content)
            
            # 【修复】截断各维度分数到有效范围
            if result:
                result = self._clamp_scores(result)
            
            if result is None:
                raise ScoringError(
                    message="JSON解析失败: 无法解析AI返回的内容",
                    error_type="json_parse_error",
                    details={"content_preview": content[:1000]}
                )
            
            result['_metadata'] = {
                'scored_at': datetime.now().isoformat(),
                'retrieved_rules': [r['rule_id'] for r in retrieved_rules],
                'pre_analysis': pre_analysis,
                'model': model,
            }
            
            return result
                
        except ScoringError:
            raise
        except Exception as e:
            raise ScoringError(
                message=f"评分失败: {str(e)}",
                error_type="api_error",
                details={"original_error": str(e)}
            )
    
    # ========== 批量评分方法（v2.4新增） ==========
    
    async def score_sessions_batch_async(self, sessions: List[Dict], pre_analyses: List[Dict] = None) -> List[Dict]:
        """【v2.6.1】异步批量评分 - 跨场景合并优化
        
        核心变更：
        - 不再按场景分组，所有会话统一批量处理
        - 场景信息通过pre_analysis传入，由模型自行处理
        - 检索规则时使用混合策略（覆盖所有场景）
        
        Args:
            sessions: 会话数据列表（20-40通）
            pre_analyses: 预分析结果列表（包含场景信息）
            
        Returns:
            评分结果列表（与输入顺序一致）
        """
        print(f"[DEBUG] score_sessions_batch_async START - {len(sessions)} sessions", flush=True)
        
        if not sessions:
            print(f"[DEBUG] No sessions, returning empty list", flush=True)
            return []
        
        # 如果未提供预分析结果，快速分析
        if pre_analyses is None:
            print(f"[DEBUG] Running pre-analysis for {len(sessions)} sessions...", flush=True)
            loop = asyncio.get_event_loop()
            pre_analyses = await asyncio.gather(*[
                loop.run_in_executor(None, self._analyze_session_pre, s.get('messages', []))
                for s in sessions
            ])
            print(f"[DEBUG] Pre-analysis completed", flush=True)
        
        # 【v2.6.1】跨场景统一评分（不再分组）
        print(f"[DEBUG] Processing {len(sessions)} sessions cross-scene", flush=True)
        results = await self._score_batch_cross_scene(sessions, pre_analyses)
        
        print(f"[DEBUG] score_sessions_batch_async END - returning {len([r for r in results if r is not None])} results", flush=True)
        return results
    
    async def _score_batch_cross_scene(self, sessions: List[Dict], 
                                          pre_analyses: List[Dict]) -> List[Dict]:
        """【v2.6.1】跨场景批量评分
        
        不再限制同一场景，支持混合场景的批量评分
        场景信息直接标注在每个会话前，由模型自行处理
        """
        if not sessions:
            return []
        
        # 收集所有场景
        scenes = list(set(p.get('scene', '其他') for p in pre_analyses))
        print(f"   📚 跨场景评分: {len(sessions)}通会话, 场景: {scenes}")
        
        # 检索所有相关场景的规则（混合检索）
        messages_text = '\n'.join([
            f"会话{i+1}({p.get('scene', '其他')}): " + '\n'.join([
                f"{m.get('role')}: {m.get('content')}" 
                for m in session.get('messages', [])[:3]
            ])
            for i, (session, p) in enumerate(zip(sessions, pre_analyses))
        ])
        
        # 检索规则：不限制场景，获取最相关的规则
        retrieved_rules = self._retrieve_rules_cross_scene(scenes, messages_text)
        rules_text = self._format_rules_for_prompt(retrieved_rules)
        
        # 构建跨场景批量Prompt（标注场景信息）
        sessions_json = '\n\n'.join([
               f"=== 会话{i+1} [场景: {pre_analyses[i].get('scene', '其他')}] ===\n{self._compact_session_for_prompt(s)}"
            for i, s in enumerate(sessions)
        ])
        
        prompt = BATCH_SCORING_PROMPT_TEMPLATE.format(
            count=len(sessions),
            retrieved_rules=rules_text,
            sessions_content=sessions_json
        )
        
        # === Prompt 结构追踪 START ===
        try:
            prompt_parts = {
                "rules_chars": len(rules_text),
                "sessions_chars": len(sessions_json),
                "template_chars": len(prompt) - len(rules_text) - len(sessions_json),
                "total_prompt_chars": len(prompt),
                "session_count": len(sessions),
                "session_avg_chars": len(sessions_json) // max(len(sessions), 1)
            }
            print(f"   📐 PROMPT_STRUCT|{json.dumps(prompt_parts, ensure_ascii=False)}", flush=True)
        except Exception as e:
            print(f"   ⚠️ PROMPT_STRUCT logging failed: {e}", flush=True)
        # === Prompt 结构追踪 END ===
        
        # 调用API
        result = await self._call_kimi_async(prompt, len(sessions), pre_analyses)
        return result
    
    def _retrieve_rules_cross_scene(self, scenes: List[str], messages_text: str) -> List[Dict]:
        """【方案B】按场景分别检索规则
        
        策略变更：
        1. 移除混合检索（混合场景下失效）
        2. 为每个场景分别检索规则（售前→售前规则，售中→售中规则...）
        3. 合并去重后返回
        
        这样即使20通混合批次，也能获取所有场景的规则。
        """
        all_rules = []
        seen_ids = set()
        
        # 去重场景列表
        unique_scenes = list(set(scenes))
        print(f"   📚 为 {len(unique_scenes)} 个场景分别检索规则: {unique_scenes}")
        
        # 为每个场景分别检索规则
        for scene in unique_scenes:
            try:
                # 1. 从知识库获取该场景的已批准规则
                scene_rules = get_approved_rules(scene_category=scene)
                for r in scene_rules:
                    if r['rule_id'] not in seen_ids:
                        all_rules.append(r)
                        seen_ids.add(r['rule_id'])
                
                if scene_rules:
                    print(f"     ✓ 场景'{scene}': {len(scene_rules)}条规则")
                
                # 2. 向量检索补充（带场景过滤）
                try:
                    # 构建该场景的查询文本
                    scene_query = f"{scene} 客服服务标准"
                    vector_rules = search_rules_by_vector(
                        query_text=scene_query,
                        top_k=3,
                        embedding_model=self.embedding_model
                    )
                    # 过滤：只保留匹配当前场景的规则
                    for vr in vector_rules:
                        if vr['rule_id'] not in seen_ids and vr.get('scene_category') == scene:
                            all_rules.append(vr)
                            seen_ids.add(vr['rule_id'])
                except Exception as e:
                    print(f"     ⚠️ 场景'{scene}'向量检索失败: {e}")
                    
            except Exception as e:
                print(f"   ⚠️ 检索场景'{scene}'规则失败: {e}")
        
        print(f"   📚 跨场景检索共 {len(all_rules)} 条规则（来自{len(unique_scenes)}个场景）")
        return all_rules[:10]  # 最多10条，避免Prompt过长
    
    async def _score_batch_same_scene(self, sessions: List[Dict], 
                                       pre_analyses: List[Dict],
                                       scene: str) -> List[Dict]:
        """对同一场景的会话进行批量评分（保留兼容）"""
        if not sessions:
            return []
        
        # 统一检索规则（同一场景用相同规则）
        messages_text = '\n'.join([
            f"会话{i+1}: " + '\n'.join([
                f"{m.get('role')}: {m.get('content')}" 
                for m in session.get('messages', [])[:5]
            ])
            for i, session in enumerate(sessions)
        ])
        
        retrieved_rules = self._retrieve_rules({'scene': scene}, messages_text)
        rules_text = self._format_rules_for_prompt(retrieved_rules)
        
        # 构建批量Prompt
        sessions_json = '\n\n'.join([
            f"=== 会话{i+1} ===\n{json.dumps(s, ensure_ascii=False, indent=2)}"
            for i, s in enumerate(sessions)
        ])
        
        prompt = BATCH_SCORING_PROMPT_TEMPLATE.format(
            count=len(sessions),
            retrieved_rules=rules_text,
            sessions_content=sessions_json
        )
        
        # 调用API（异步）
        result = await self._call_kimi_async(prompt, len(sessions), pre_analyses)
        return result
    
    async def _call_kimi_async(self, prompt: str, expected_count: int, pre_analyses: List[Dict] = None) -> List[Dict]:
        """异步调用Kimi API（带httpx精细超时控制）"""
        import logging
        logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(message)s')
        logger = logging.getLogger(__name__)
        
        logger.info(f"[DEBUG] _call_kimi_async START - expected_count={expected_count}")
        print(f"   [DEBUG] === _call_kimi_async START ===", flush=True)
        print(f"   [DEBUG] expected_count={expected_count}", flush=True)
        
        try:
            import openai
            import asyncio
            import httpx
            
            timeout_seconds = int(os.getenv('KIMI_API_TIMEOUT', '300'))  # 【v2.5】从环境变量读取，默认300秒
            import sys
            print(f"   [DEBUG] API Timeout: {timeout_seconds}s", file=sys.stderr, flush=True)
            print(f"   [DEBUG] Worker PID: {os.getpid()}", file=sys.stderr, flush=True)
            print(f"   [DEBUG] API Key exists: {bool(self.api_key)}", flush=True)
            
            client = openai.AsyncOpenAI(
                api_key=self.api_key,
                base_url="https://api.moonshot.cn/v1",
                max_retries=2,
                timeout=httpx.Timeout(
                    connect=30.0,           # 连接超时30秒
                    read=timeout_seconds,   # 读取超时从.env读取
                    write=30.0,             # 写入超时30秒
                    pool=30.0               # 连接池超时30秒
                )
            )
            
            model = os.getenv('KIMI_MODEL', 'kimi-k2.5')
            print(f"   [DEBUG] Using model: {model}", flush=True)
            
            # 【v2.5】添加超时保护（从.env读取，默认300秒）
            print(f"   [DEBUG] Calling API with timeout={timeout_seconds}s...", flush=True)
            start_time = datetime.now()
            
            async with asyncio.timeout(timeout_seconds):
                response = await client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": "你是专业的客服质检专家，严格按JSON格式输出评分结果。"},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=1,
                    max_tokens=int(os.getenv('KIMI_MAX_TOKENS', 16000))
                )
            
            elapsed = (datetime.now() - start_time).total_seconds()
            print(f"   [DEBUG] API call completed in {elapsed:.1f}s", flush=True)
            
            content = response.choices[0].message.content
            
            # === 成本追踪埋点 START ===
            try:
                usage = response.usage
                cost_log = {
                    "batch_idx": expected_count,
                    "prompt_tokens": usage.prompt_tokens,
                    "completion_tokens": usage.completion_tokens,
                    "total_tokens": usage.total_tokens,
                    "elapsed_seconds": elapsed,
                    "prompt_chars": len(prompt),
                    "model": model
                }
                print(f"   💰 TOKEN_COST|{json.dumps(cost_log, ensure_ascii=False)}", flush=True)
            except Exception as e:
                print(f"   ⚠️ TOKEN_COST logging failed: {e}", flush=True)
            # === 成本追踪埋点 END ===
            
            results = self._parse_batch_response(content, expected_count)
            print(f"   [DEBUG] Parsed {len(results)} results", flush=True)
            
            # 【修复】截断各维度分数到有效范围
            results = [self._clamp_scores(r) if isinstance(r, dict) else r for r in results]
            
            # 补充元数据（包含预分析数据）
            for i, r in enumerate(results):
                # 【Bug修复】确保r是字典，不是列表或其他类型
                if not isinstance(r, dict):
                    print(f"   ⚠️ 结果{i}类型错误: {type(r)}, 转换为错误字典", flush=True)
                    r = {"error": f"解析结果类型错误: {type(r)}", "_raw": str(r)[:200]}
                    results[i] = r
                # 确保基本字段存在
                if 'error' not in r:
                    r.setdefault('professionalism_score', 0)
                    r.setdefault('standardization_score', 0)
                    r.setdefault('policy_execution_score', 0)
                    r.setdefault('conversion_score', 0)
                if '_metadata' not in r:
                    r['_metadata'] = {}
                r['_metadata']['model'] = model
                r['_metadata']['scored_at'] = datetime.now().isoformat()
                # 添加预分析数据（从pre_analyses获取）
                if pre_analyses and i < len(pre_analyses):
                    r['_metadata']['pre_analysis'] = pre_analyses[i]
            
            print(f"   [DEBUG] === _call_kimi_async END (success) ===", flush=True)
            return results
            
        except asyncio.TimeoutError:
            timeout_val = os.getenv('KIMI_API_TIMEOUT', '300')
            print(f"⚠️ 批量评分超时: Kimi API调用超过{timeout_val}秒", flush=True)
            print(f"   [DEBUG] === _call_kimi_async END (timeout) ===", flush=True)
            return [{"error": f"API调用超时({timeout_val}s)"} for _ in range(expected_count)]
        except Exception as e:
            print(f"⚠️ 批量评分失败: {e}", flush=True)
            print(f"   [DEBUG] === _call_kimi_async END (error) ===", flush=True)
            return [{"error": str(e)} for _ in range(expected_count)]
    
    def _parse_batch_response(self, content: str, expected_count: int) -> List[Dict]:
        """解析批量评分响应"""
        # 清理Markdown代码块
        cleaned = content
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        elif cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()
        
        try:
            results = json.loads(cleaned)
            if isinstance(results, list) and len(results) == expected_count:
                return results
            elif isinstance(results, dict):
                if 'results' in results:
                    return results['results']
                return [results] + [{} for _ in range(expected_count - 1)]
        except json.JSONDecodeError:
            pass
        
        # 尝试逐个解析JSON对象
        try:
            pattern = r'\{[^{}]*"session_analysis"[^{}]*(?:\{[^{}]*\}[^{}]*)*\}'
            matches = re.findall(pattern, cleaned, re.DOTALL)
            if len(matches) >= expected_count:
                return [json.loads(m) for m in matches[:expected_count]]
        except:
            pass
        
        # 回退：尝试用单条解析器
        single_result = self._parse_json_robust(cleaned)
        if single_result:
            return [single_result] + [{} for _ in range(expected_count - 1)]
        
        return [{} for _ in range(expected_count)]
    
    # ========== 通用工具方法 ==========
    
    def _clamp_scores(self, result: Dict) -> Dict:
        """【修复】截断各维度分数到有效范围（1-5分）
        
        AI模型有时会给出超出1-5分范围的分数，需要截断
        """
        if not isinstance(result, dict):
            return result
        
        dims = ['professionalism', 'standardization', 'policy_execution', 'conversion']
        dim_scores = result.get('dimension_scores', {})
        
        for dim in dims:
            if dim in dim_scores and isinstance(dim_scores[dim], dict):
                score = dim_scores[dim].get('score', 3)
                # 截断到1-5分范围
                original_score = score
                clamped_score = max(1, min(5, score))
                if original_score != clamped_score:
                    print(f"   ⚠️ {dim}分数截断: {original_score} -> {clamped_score}")
                    dim_scores[dim]['score'] = clamped_score
                    # 添加截断标记
                    dim_scores[dim]['_clamped'] = True
                    dim_scores[dim]['_original_score'] = original_score
        
        # 重新计算总分
        total = sum(dim_scores.get(d, {}).get('score', 3) for d in dims)
        if 'summary' not in result:
            result['summary'] = {}
        result['summary']['total_score'] = total
        
        # 更新风险等级
        if total <= 8:
            result['summary']['risk_level'] = "高风险"
        elif total <= 12:
            result['summary']['risk_level'] = "中风险"
        else:
            result['summary']['risk_level'] = "正常"
        
        return result

    def _parse_json_robust(self, content: str) -> Optional[Dict]:
        """健壮JSON解析 - 处理截断和不完整JSON
        
        Args:
            content: 原始响应内容
            
        Returns:
            解析后的字典，或None
        """
        if not content:
            return None
        
        # 步骤1: 清理Markdown代码块
        cleaned = content
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        elif cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()
        
        # 步骤2: 尝试正常解析
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass
        
        # 步骤3: 尝试提取JSON对象（从第一个{到最后一个}）
        try:
            start = cleaned.find('{')
            end = cleaned.rfind('}')
            if start != -1 and end != -1 and end > start:
                extracted = cleaned[start:end+1]
                return json.loads(extracted)
        except json.JSONDecodeError:
            pass
        
        # 步骤4: 尝试修复截断的JSON
        try:
            fixed = self._fix_truncated_json(cleaned)
            if fixed:
                return json.loads(fixed)
        except json.JSONDecodeError:
            pass
        
        return None
    
    def _fix_truncated_json(self, content: str) -> Optional[str]:
        """尝试修复截断的JSON
        
        Args:
            content: 可能截断的JSON字符串
            
        Returns:
            修复后的JSON字符串，或None
        """
        if not content:
            return None
        
        fixed = content.strip()
        
        # 如果最后是不完整的字符串（在引号内截断）
        last_quote = fixed.rfind('"')
        if last_quote > 0:
            after_quote = fixed[last_quote+1:].strip()
            if after_quote and after_quote[0] not in [',', ':', '}', ']']:
                fixed = fixed + '"'
        
        # 统计开闭符号
        open_braces = fixed.count('{') - fixed.count('}')
        open_brackets = fixed.count('[') - fixed.count(']')
        
        # 补全缺失的闭括号
        fixed = fixed + ('}' * open_braces)
        fixed = fixed + (']' * open_brackets)
        
        # 如果最后是逗号，移除它
        if fixed.rstrip().endswith(','):
            fixed = fixed.rstrip()[:-1]
        
        return fixed


# ========== 便捷函数 ==========

def score_session_with_rules(session_data: Dict, api_key: str = None) -> Dict:
    """便捷函数：对会话进行规则增强评分
    
    Args:
        session_data: 会话数据
        api_key: Moonshot API Key
        
    Returns:
        评分结果
        
    Raises:
        ScoringError: 当AI评分失败时抛出
    """
    engine = SmartScoringEngine(api_key=api_key)
    return engine.score_session(session_data)


# ========== 测试 ==========

if __name__ == "__main__":
    # 测试数据
    test_session = {
        "session_id": "test_001",
        "messages": [
            {"role": "user", "content": "你们不是骗子吗？主播说的和客服说的不一样"},
            {"role": "staff", "content": "小主您可以以直播间主播的规则为准呢"},
            {"role": "user", "content": "那就是你们客服说的不算？"},
            {"role": "staff", "content": "小主您可以以直播间主播的规则为准呢"},
            {"role": "user", "content": "你只会重复这句话吗？"},
            {"role": "staff", "content": "小主您可以以直播间主播的规则为准呢"}
        ],
        "staff_name": "林内林小肖"
    }
    
    print("🧪 测试智能评分...")
    result = score_session_with_rules(test_session)
    
    if result:
        print("\n✅ 评分结果:")
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("\n❌ 评分失败")
