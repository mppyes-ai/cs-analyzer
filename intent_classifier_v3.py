"""意图分类模块 v3.1 - 集成情绪判断与扩展关键词

核心改进：
1. 使用本地qwen2.5:7b进行语义级情绪分析（识别委婉投诉）
2. 扩展关键词库（减少未分类比例）
3. 场景分类优化（生命周期4类）

作者: 小虾米
更新: 2026-03-21（集成情绪判断）
"""

import json
import re
import logging
import os
from typing import Dict, List, Optional
from dataclasses import dataclass
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

from ollama_client import OllamaClient, OllamaConfig

# 导入扩展功能
from keywords_extended import classify_with_extended_keywords, extract_product_id
from sentiment_analyzer import SentimentAnalyzer, SentimentAnalysisResult

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('intent_classifier_v3')


@dataclass
class IntentClassificationResult:
    """意图分类结果"""
    scene: str                    # 售前/售中/售后/客诉/其他
    sub_scene: str               # 二级场景
    intent: str                  # 咨询/投诉/退款/维修/安装等
    sentiment: str               # positive/neutral/negative/urgent/complaint
    is_complaint: bool          # 是否投诉
    complaint_type: str         # 投诉类型（如有）
    confidence: float           # 置信度
    reasoning: str              # 判断理由
    source: str                 # 来源：rule/extended_keyword/qwen3/sentiment_analyzer/keyword_fallback
    latency_ms: float           # 延迟
    error: Optional[str] = None


class RuleBasedIntentClassifier:
    """基于规则的意图分类器（毫秒级）- 更新为生命周期分类"""

    # 物流能力咨询关键词（售前场景：用户无订单，询问物流是为了购买决策）
    PRE_SALE_LOGISTICS_KEYWORDS = [
        "你们一般", "你们", "通常", "正常", "一般", "大概", "多久"
    ]

    # 订单物流查询关键词（售中场景：用户已下单，查询具体订单物流）
    POST_SALE_LOGISTICS_KEYWORDS = [
        "我的", "订单", "已下单", "发货了吗", "到哪了", "查询"
    ]

    RULES = [
        # 售中阶段 - 订单/物流（高优先级，用户已付款）
        {
            "id": "order_modify",
            "patterns": [r"改地址", r"换型号", r"取消订单", r"退单", r"不要了"],
            "scene": "售中阶段",
            "sub_scene": "订单管理",
            "intent": "订单修改",
            "priority": 110
        },
        {
            "id": "shipping_query_post_sale",
            "patterns": [r"我的.*发货", r"订单.*到哪", r"发货了吗", r"查询物流"],
            "scene": "售中阶段",
            "sub_scene": "物流跟踪",
            "intent": "物流查询",
            "priority": 105
        },
        # 售前物流咨询（优先级较低，需要根据上下文动态判断）
        {
            "id": "shipping_query_pre_sale",
            "patterns": [r"发什么快递", r"什么时候到", r"物流", r"几天", r"多久.*发货", r"多久.*到"],
            "scene": "售前阶段",  # 默认售前，实际会根据订单上下文调整
            "sub_scene": "产品认知",
            "intent": "物流能力咨询",
            "priority": 95
        },
        {
            "id": "delay_delivery",
            "patterns": [r"延迟发货", r"晚点发", r"存仓", r"暂存", r"推迟"],
            "scene": "售中阶段",
            "sub_scene": "延迟发货",
            "intent": "延迟发货咨询",
            "priority": 105
        },
        {
            "id": "invoice_query",
            "patterns": [r"发票", r"专票", r"普票", r"开票", r"电子发票"],
            "scene": "售中阶段",
            "sub_scene": "支付票据",
            "intent": "发票咨询",
            "priority": 100
        },
        
        # 售后阶段 - 安装/维修
        {
            "id": "install_query",
            "patterns": [r"安装", r"预约安装", r"安装费", r"辅材", r"烟管", r"打孔"],
            "scene": "售后阶段",
            "sub_scene": "安装服务",
            "intent": "安装咨询",
            "priority": 100
        },
        {
            "id": "repair_query",
            "patterns": [r"维修", r"坏了", r"故障", r"E1", r"报错", r"不出热水", r"保修"],
            "scene": "售后阶段",
            "sub_scene": "故障维修",
            "intent": "维修咨询",
            "priority": 100
        },
        {
            "id": "usage_guide",
            "patterns": [r"怎么用", r"使用", r"操作", r"设置", r"温度", r"教程"],
            "scene": "售后阶段",
            "sub_scene": "使用指导",
            "intent": "使用咨询",
            "priority": 95
        },
        
        # 售前阶段 - 产品/价格
        {
            "id": "price_query",
            "patterns": [r"多少钱", r"价格", r"优惠", r"活动", r"便宜", r"打折", r"返现"],
            "scene": "售前阶段",
            "sub_scene": "价格决策",
            "intent": "价格咨询",
            "priority": 95
        },
        {
            "id": "product_model",
            "patterns": [r"GD\d+", r"型号", r"升", r"L", r"燃气", r"电热水器", r"壁挂炉"],
            "scene": "售前阶段",
            "sub_scene": "产品认知",
            "intent": "型号咨询",
            "priority": 90
        },
        {
            "id": "comparison",
            "patterns": [r"区别", r"哪个好", r"推荐", r"对比", r"比价"],
            "scene": "售前阶段",
            "sub_scene": "购买促成",
            "intent": "推荐请求",
            "priority": 90
        },
        
        # 客诉处理 - 严重投诉（最高优先级）
        {
            "id": "complaint_urgent",
            "patterns": [r"骗子", r"投诉", r"315", r"举报", r"欺诈", r"退钱", r"欺骗"],
            "scene": "客诉处理",
            "sub_scene": "严重投诉",
            "intent": "严重客诉",
            "priority": 120
        },
        
        # 其他 - 结束语
        {
            "id": "simple_thanks",
            "patterns": [r"^谢谢$", r"^好的$", r"^知道了$", r"^再见$", r"^拜拜$"],
            "scene": "其他",
            "sub_scene": "结束语",
            "intent": "其他",
            "priority": 80
        }
    ]
    
    @classmethod
    def _has_logistics_keywords(cls, text: str) -> bool:
        """检查是否包含物流相关关键词"""
        logistics_keywords = ["发什么快递", "物流", "几天", "多久", "什么时候到", "几天到", "发货", "配送"]
        return any(kw in text for kw in logistics_keywords)

    @classmethod
    def _is_pre_sale_logistics(cls, text: str) -> bool:
        """
        判断是否为售前物流咨询（无订单，了解物流能力用于购买决策）
        特征：使用"你们一般"、"通常"等泛化询问，而非"我的订单"
        """
        # 如果有明确的订单指向词，则是售中
        for kw in cls.POST_SALE_LOGISTICS_KEYWORDS:
            if kw in text:
                return False

        # 如果有售前泛化询问词，则是售前
        for kw in cls.PRE_SALE_LOGISTICS_KEYWORDS:
            if kw in text:
                return True

        # 默认按售前处理（保守策略）
        return True

    @classmethod
    def classify(cls, messages: List[Dict], user_id: str = None) -> Optional[IntentClassificationResult]:
        """规则分类 - 支持基于订单上下文的动态判断"""
        start_time = datetime.now()

        user_text = ' '.join([m['content'] for m in messages if m.get('role') in ['customer', 'user']])

        # 先检查是否是物流相关查询（需要特殊处理售前/售中边界）
        if cls._has_logistics_keywords(user_text):
            # 根据语义判断是否为售前咨询
            is_pre_sale = cls._is_pre_sale_logistics(user_text)

            if not is_pre_sale:
                # 明确的售中物流查询（"我的订单"）
                latency = (datetime.now() - start_time).total_seconds() * 1000
                return IntentClassificationResult(
                    scene="售中阶段",
                    sub_scene="物流跟踪",
                    intent="物流查询",
                    sentiment="neutral",
                    is_complaint=False,
                    complaint_type="",
                    confidence=0.9,
                    reasoning="命中规则: 订单物流查询（包含'我的/订单'等明确售中关键词）",
                    source="rule",
                    latency_ms=latency
                )
            else:
                # 售前物流咨询（"你们一般几天到"）
                latency = (datetime.now() - start_time).total_seconds() * 1000
                return IntentClassificationResult(
                    scene="售前阶段",
                    sub_scene="产品认知",
                    intent="物流能力咨询",
                    sentiment="neutral",
                    is_complaint=False,
                    complaint_type="",
                    confidence=0.85,
                    reasoning="命中规则: 售前物流能力咨询（泛化询问，用于购买决策）",
                    source="rule",
                    latency_ms=latency
                )

        sorted_rules = sorted(cls.RULES, key=lambda x: x['priority'], reverse=True)

        for rule in sorted_rules:
            for pattern in rule['patterns']:
                if re.search(pattern, user_text):
                    latency = (datetime.now() - start_time).total_seconds() * 1000

                    return IntentClassificationResult(
                        scene=rule['scene'],
                        sub_scene=rule['sub_scene'],
                        intent=rule['intent'],
                        sentiment="neutral",
                        is_complaint=(rule['scene'] == "客诉处理"),
                        complaint_type=rule['sub_scene'] if rule['scene'] == "客诉处理" else "",
                        confidence=0.95,
                        reasoning=f"命中规则: {rule['id']}",
                        source="rule",
                        latency_ms=latency
                    )

        return None


class RobustIntentClassifier:
    """健壮版意图分类器 - 集成情绪分析"""
    
    def __init__(self, 
                 enable_rule_first: bool = True,
                 enable_sentiment_analysis: bool = True,
                 enable_extended_keywords: bool = True,
                 ollama_config: Optional[OllamaConfig] = None,
                 max_workers: int = None):
        """
        Args:
            enable_rule_first: 是否启用规则优先
            enable_sentiment_analysis: 是否启用情绪分析（识别委婉投诉）
            enable_extended_keywords: 是否启用扩展关键词
            ollama_config: Ollama配置
            max_workers: 最大并发数，默认从环境变量INTENT_MAX_WORKERS读取，默认10
        """
        self.enable_rule_first = enable_rule_first
        self.enable_sentiment_analysis = enable_sentiment_analysis
        self.enable_extended_keywords = enable_extended_keywords
        self.ollama_config = ollama_config or OllamaConfig()
        self.max_workers = max_workers or int(os.getenv('INTENT_MAX_WORKERS', 10))
        
        # 延迟初始化
        self._ollama_client: Optional[OllamaClient] = None
        self._client_initialized = False
        self._sentiment_analyzer: Optional[SentimentAnalyzer] = None
        
        # 统计
        self.stats = {
            "total_calls": 0,
            "rule_hits": 0,
            "extended_keyword_hits": 0,
            "qwen2.5_hits": 0,
            "qwen2.5_failures": 0,
            "sentiment_analysis_hits": 0,
            "complaint_detected": 0,
            "keyword_fallbacks": 0
        }
    
    @property
    def ollama_client(self) -> OllamaClient:
        """延迟初始化Ollama客户端"""
        if not self._client_initialized:
            logger.info("初始化Ollama客户端...")
            self._ollama_client = OllamaClient(self.ollama_config)
            self._client_initialized = True
        return self._ollama_client
    
    @property
    def sentiment_analyzer(self) -> SentimentAnalyzer:
        """延迟初始化情绪分析器"""
        if self._sentiment_analyzer is None:
            self._sentiment_analyzer = SentimentAnalyzer()
        return self._sentiment_analyzer
    
    def classify(self, messages: List[Dict], user_id: str = None) -> IntentClassificationResult:
        """
        分类入口 - 四层架构

        1. 规则匹配（毫秒级）- 支持基于订单上下文的动态判断
        2. 扩展关键词（处理口语化、链接等）
        3. Qwen3语义分类（复杂场景）
        4. 情绪分析（识别委婉投诉）- 可独立触发
        """
        self.stats["total_calls"] += 1
        start_time = datetime.now()

        # 第一层：规则匹配（支持 user_id 用于订单上下文判断）
        if self.enable_rule_first:
            rule_result = RuleBasedIntentClassifier.classify(messages, user_id=user_id)
            if rule_result:
                self.stats["rule_hits"] += 1
                # 如果是客诉，不再走情绪分析（规则已识别）
                if rule_result.is_complaint:
                    self.stats["complaint_detected"] += 1
                logger.debug(f"⚡ 规则拦截: {rule_result.sub_scene} ({rule_result.latency_ms:.1f}ms)")
                return rule_result
        
        # 第二层：扩展关键词
        if self.enable_extended_keywords:
            ext_result = self._classify_with_extended_keywords(messages)
            if ext_result:
                self.stats["extended_keyword_hits"] += 1
                logger.debug(f"🔍 扩展关键词: {ext_result.sub_scene}")
                return ext_result
        
        # 第三层：情绪分析（识别委婉投诉）
        # 这一步可以独立于意图分类，专门识别投诉情绪
        if self.enable_sentiment_analysis:
            sentiment_result = self.sentiment_analyzer.analyze(messages)
            self.stats["sentiment_analysis_hits"] += 1
            
            if sentiment_result.is_complaint:
                self.stats["complaint_detected"] += 1
                # 如果是投诉，返回客诉处理场景
                return IntentClassificationResult(
                    scene="客诉处理",
                    sub_scene=sentiment_result.complaint_type or "一般投诉",
                    intent="客诉",
                    sentiment=sentiment_result.sentiment,
                    is_complaint=True,
                    complaint_type=sentiment_result.complaint_type,
                    confidence=sentiment_result.confidence,
                    reasoning=f"情绪分析: {sentiment_result.reasoning}",
                    source="sentiment_analyzer",
                    latency_ms=(datetime.now() - start_time).total_seconds() * 1000
                )
        
        # 第四层：Qwen3语义分类
        qwen_result = self._classify_with_qwen_safe(messages)
        if qwen_result:
            self.stats["qwen2.5_hits"] += 1
            # 检查Qwen2.5结果是否是投诉
            if qwen_result.scene == "客诉处理":
                self.stats["complaint_detected"] += 1
            return qwen_result
        
        # 第五层：关键词回退
        self.stats["keyword_fallbacks"] += 1
        return self._classify_keyword_fallback(messages)
    
    def _classify_with_extended_keywords(self, messages: List[Dict]) -> Optional[IntentClassificationResult]:
        """使用扩展关键词分类"""
        start_time = datetime.now()
        
        user_text = ' '.join([m['content'] for m in messages if m.get('role') in ['customer', 'user']])
        
        # 调用扩展关键词分类
        categories = classify_with_extended_keywords(user_text)
        
        if not categories:
            return None
        
        # 映射到生命周期分类
        category_mapping = {
            '01-价格/优惠': ('售前阶段', '价格决策'),
            '02-产品咨询': ('售前阶段', '产品认知'),
            '03-安装': ('售后阶段', '安装服务'),
            '04-售后/维修': ('售后阶段', '故障维修'),
            '05-发货/物流': ('售中阶段', '物流跟踪'),
            '06-发票': ('售中阶段', '支付票据'),
            '07-延迟发货': ('售中阶段', '延迟发货'),
            '08-以旧换新': ('售前阶段', '购买促成'),
            '09-国补/补贴': ('售前阶段', '价格决策'),  # 已确认归入价格决策
            '10-赠品/礼品': ('售前阶段', '购买促成'),
            '11-对比/推荐': ('售前阶段', '购买促成'),
            '12-投诉/不满': ('客诉处理', '一般投诉'),
            '13-产品功能': ('售前阶段', '产品认知'),
            '14-使用场景': ('售前阶段', '产品认知'),
            '15-品牌产地': ('售前阶段', '产品认知'),
            '16-订单操作': ('售中阶段', '订单管理'),
            '17-预约时间': ('售后阶段', '安装服务'),
            '18-配件辅材': ('售后阶段', '安装服务'),
            '19-安全认证': ('售前阶段', '产品认知'),
            '20-能效环保': ('售前阶段', '产品认知'),
            '21-售后政策': ('售后阶段', '故障维修'),
            '22-佣金返利': ('售前阶段', '价格决策'),
            '23-装修设计': ('售前阶段', '产品认知'),
            '24-支付金融': ('售中阶段', '支付票据')
        }
        
        # 取第一个匹配的分类
        first_cat = categories[0]
        if first_cat in category_mapping:
            scene, sub_scene = category_mapping[first_cat]
            latency = (datetime.now() - start_time).total_seconds() * 1000
            
            return IntentClassificationResult(
                scene=scene,
                sub_scene=sub_scene,
                intent="咨询",
                sentiment="neutral",
                is_complaint=(scene == "客诉处理"),
                complaint_type=sub_scene if scene == "客诉处理" else "",
                confidence=0.7,
                reasoning=f"扩展关键词匹配: {first_cat}",
                source="extended_keyword",
                latency_ms=latency
            )
        
        return None
    
    def _classify_with_qwen_safe(self, messages: List[Dict]) -> Optional[IntentClassificationResult]:
        """安全调用Qwen3 - 更新为生命周期分类"""
        start_time = datetime.now()
        
        user_messages = [m['content'] for m in messages if m.get('role') in ['customer', 'user']]
        user_text = '\n'.join(user_messages[:5])
        
        prompt = f"""直接输出JSON，不要解释：

一级场景必须从以下选择：售前阶段/售中阶段/售后阶段/客诉处理/其他
二级场景自由描述（3-8字）

{{"scene": "售前阶段/售中阶段/售后阶段/客诉处理/其他", "sub_scene": "细分场景", "intent": "咨询/客诉/退款/维修/安装/比价/催单/其他", "sentiment": "positive/neutral/negative/urgent", "confidence": 0.85, "reasoning": "分类理由"}}

会话内容：
{user_text}"""
        
        try:
            result = self.ollama_client.generate(
                prompt=prompt,
                options={"temperature": 0.1, "num_predict": 300}
            )
            
            if not result:
                logger.warning("Qwen3返回空结果")
                self.stats["qwen2.5_failures"] += 1
                return None
            
            content = self.ollama_client.extract_response(result)
            
            if '```json' in content:
                content = content.split('```json')[1]
            if '```' in content:
                content = content.split('```')[0]
            
            data = json.loads(content.strip())
            
            latency = (datetime.now() - start_time).total_seconds() * 1000
            
            # 判断是否是投诉
            is_complaint = (data.get('scene') == "客诉处理") or ("complaint" in data.get('sentiment', ''))
            
            return IntentClassificationResult(
                scene=data.get('scene', '其他'),
                sub_scene=data.get('sub_scene', '其他'),
                intent=data.get('intent', '咨询'),
                sentiment=data.get('sentiment', 'neutral'),
                is_complaint=is_complaint,
                complaint_type=data.get('sub_scene', '') if is_complaint else "",
                confidence=data.get('confidence', 0.5),
                reasoning=data.get('reasoning', ''),
                source="qwen2.5",
                latency_ms=latency
            )
            
        except Exception as e:
            logger.error(f"Qwen2.5分类异常: {e}")
            self.stats["qwen2.5_failures"] += 1
            return None
    
    def _classify_keyword_fallback(self, messages: List[Dict]) -> IntentClassificationResult:
        """关键词回退 - 更新为生命周期分类"""
        start_time = datetime.now()
        
        user_text = ' '.join([m['content'] for m in messages if m.get('role') in ['customer', 'user']])
        
        # 生命周期场景关键词
        scene_keywords = {
            "售中阶段": ["订单", "发货", "物流", "发票", "改地址", "取消"],
            "售后阶段": ["安装", "坏了", "故障", "维修", "保修", "使用", "怎么用"],
            "售前阶段": ["多少钱", "价格", "优惠", "推荐", "型号", "活动", "区别"],
            "客诉处理": ["投诉", "不满", "骗", "忽悠", "态度", "差评", "退货", "退钱"]
        }
        
        scene_scores = {k: sum(1 for w in v if w in user_text) for k, v in scene_keywords.items()}
        detected_scene = max(scene_scores, key=scene_scores.get) if max(scene_scores.values()) > 0 else "其他"
        
        # 判断投诉
        is_complaint = (detected_scene == "客诉处理") or any(w in user_text for w in ["投诉", "骗", "退钱", "不满"])
        
        # 情绪
        if is_complaint:
            sentiment = "complaint"
        elif any(w in user_text for w in ["生气", "失望", "郁闷"]):
            sentiment = "negative"
        else:
            sentiment = "neutral"
        
        latency = (datetime.now() - start_time).total_seconds() * 1000
        
        return IntentClassificationResult(
            scene=detected_scene,
            sub_scene="其他",
            intent="咨询",
            sentiment=sentiment,
            is_complaint=is_complaint,
            complaint_type=detected_scene if is_complaint else "",
            confidence=0.5,
            reasoning="关键词规则回退（Qwen2.5失败）",
            source="keyword_fallback",
            latency_ms=latency
        )
    
    def get_stats(self) -> Dict:
        """获取统计"""
        total = self.stats["total_calls"]
        return {
            "total_calls": total,
            "rule_hits": self.stats["rule_hits"],
            "rule_hit_rate": self.stats["rule_hits"] / total if total > 0 else 0,
            "extended_keyword_hits": self.stats["extended_keyword_hits"],
            "sentiment_analysis_hits": self.stats["sentiment_analysis_hits"],
            "complaint_detected": self.stats["complaint_detected"],
            "complaint_rate": self.stats["complaint_detected"] / total if total > 0 else 0,
            "qwen2.5_hits": self.stats["qwen2.5_hits"],
            "qwen2.5_failures": self.stats["qwen2.5_failures"],
            "keyword_fallbacks": self.stats["keyword_fallbacks"]
        }
    
    def close(self):
        """关闭客户端"""
        if self._client_initialized and self._ollama_client:
            self._ollama_client.close()
            logger.info("Ollama客户端已关闭")


# 便捷函数
def classify_intent(messages: List[Dict], user_id: str = None) -> Dict:
    """便捷函数：单条分类 - 支持订单上下文"""
    classifier = RobustIntentClassifier()
    try:
        result = classifier.classify(messages, user_id=user_id)
        return {
            'scene': result.scene,
            'sub_scene': result.sub_scene,
            'intent': result.intent,
            'sentiment': result.sentiment,
            'is_complaint': result.is_complaint,
            'complaint_type': result.complaint_type,
            'confidence': result.confidence,
            'reasoning': result.reasoning,
            'source': result.source,
            'latency_ms': result.latency_ms
        }
    finally:
        classifier.close()


if __name__ == "__main__":
    # 测试
    print("🧪 意图分类器 v3.1 测试（集成情绪分析 + 售前/售中物流边界）\n")

    classifier = RobustIntentClassifier()

    test_cases = [
        {
            "name": "委婉投诉（承诺未兑现）",
            "messages": [{"role": "customer", "content": "说的30天返现，都2个月了还没到账"}]
        },
        {
            "name": "委婉投诉（信息不一致）",
            "messages": [{"role": "customer", "content": "你们主播说的和客服说的怎么不一样"}]
        },
        {
            "name": "正常咨询",
            "messages": [{"role": "customer", "content": "这款热水器多少钱？有活动吗"}]
        },
        {
            "name": "口语化（包安装）",
            "messages": [{"role": "customer", "content": "包安嘛"}]
        },
        {
            "name": "【边界测试】售前物流咨询（无订单，了解物流能力）",
            "messages": [{"role": "customer", "content": "你们一般几天能发货？到上海要多久"}]
        },
        {
            "name": "【边界测试】售中物流查询（有订单，查询具体物流）",
            "messages": [{"role": "customer", "content": "我的订单发货了吗？现在到哪了"}]
        },
        {
            "name": "【边界测试】售前物流咨询（泛化询问）",
            "messages": [{"role": "customer", "content": "发什么快递？正常多久到"}]
        }
    ]

    for case in test_cases:
        print(f"【{case['name']}】")
        result = classifier.classify(case['messages'])
        print(f"  场景: {result.scene}/{result.sub_scene}")
        print(f"  意图: {result.intent}")
        print(f"  情绪: {result.sentiment}")
        print(f"  是否投诉: {'是' if result.is_complaint else '否'}")
        if result.is_complaint:
            print(f"  投诉类型: {result.complaint_type}")
        print(f"  来源: {result.source}")
        print(f"  理由: {result.reasoning}")
        print()

    print("【统计】")
    stats = classifier.get_stats()
    print(f"  总调用: {stats['total_calls']}")
    print(f"  规则拦截: {stats['rule_hits']} ({stats['rule_hit_rate']*100:.1f}%)")
    print(f"  投诉识别: {stats['complaint_detected']} ({stats['complaint_rate']*100:.1f}%)")

    classifier.close()

# 向后兼容
FunnelIntentClassifier = RobustIntentClassifier
