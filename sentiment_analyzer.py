# -*- coding: utf-8 -*-
"""
情绪判断模块 - 使用本地qwen2.5:7b进行语义级情绪分析

解决关键词匹配无法识别委婉投诉的问题
"""

import json
import re
from typing import Dict, List, Tuple
from dataclasses import dataclass

# 导入Ollama客户端
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from ollama_client import OllamaClient


@dataclass
class SentimentAnalysisResult:
    """情绪分析结果"""
    sentiment: str           # positive/neutral/negative/urgent/complaint
    confidence: float        # 0-1
    is_complaint: bool       # 是否投诉
    complaint_type: str      # 投诉类型（如有）
    reasoning: str           # 判断理由
    severity: int            # 严重程度 1-5


class SentimentAnalyzer:
    """情绪分析器（本地qwen2.5:7b）"""
    
    SENTIMENT_PROMPT = """你是一位专业的客服情绪分析专家。请分析以下客服会话的情绪状态。

会话内容:
```
{messages}
```

分析要求:
1. 判断用户整体情绪（positive/neutral/negative/urgent/complaint）
2. 识别是否有投诉意图（即使措辞委婉）
3. 如果是投诉，判断投诉类型
4. 评估严重程度（1-5分）

投诉场景示例（措辞委婉）:
- "主播说的和客服说的不一样" → 争议/信息不一致投诉
- "说的30天返现，都2个月了" → 承诺未兑现投诉  
- "这个安装费收得不合理吧" → 费用争议投诉
- "你们这活动规则变来变去" → 政策变动投诉
- "客服态度不太好" → 服务态度投诉
- "质量有问题，想退货" → 产品质量投诉

输出JSON格式:
{{
    "sentiment": "complaint",
    "confidence": 0.92,
    "is_complaint": true,
    "complaint_type": "承诺未兑现",
    "complaint_subtype": "返现延迟",
    "reasoning": "用户提到'30天返现，都2个月了'，表明承诺未兑现，情绪负面",
    "severity": 4,
    "key_evidence": "'都2个月了还没到账'",
    "suggested_action": "核实返现进度，安抚用户情绪，提供明确到账时间"
}}"""

    def __init__(self, timeout: int = 3):
        self.client = OllamaClient()
        self.timeout = timeout
        self.enabled = True
    
    def analyze(self, messages: List[Dict]) -> SentimentAnalysisResult:
        """
        分析会话情绪
        
        Args:
            messages: 消息列表 [{"role": "customer"/"staff", "content": "..."}]
        
        Returns:
            SentimentAnalysisResult
        """
        if not self.enabled:
            return self._fallback_result()
        
        # 提取用户消息（最多前5条，控制token）
        user_messages = [m for m in messages if m.get('role') == 'customer'][:5]
        if not user_messages:
            return self._fallback_result()
        
        # 构建会话文本
        conversation_text = "\n".join([
            f"用户: {m['content'][:100]}"  # 截断控制长度
            for m in user_messages
        ])
        
        try:
            prompt = self.SENTIMENT_PROMPT.format(messages=conversation_text)
            
            response = self.client.generate(
                prompt=prompt,
                options={
                    "temperature": 0.1,
                    "num_predict": 400,
                    "timeout": self.timeout
                }
            )
            
            # 解析JSON
            result = self._extract_json(response)
            
            return SentimentAnalysisResult(
                sentiment=result.get('sentiment', 'neutral'),
                confidence=result.get('confidence', 0.5),
                is_complaint=result.get('is_complaint', False),
                complaint_type=result.get('complaint_type', ''),
                reasoning=result.get('reasoning', ''),
                severity=result.get('severity', 1)
            )
            
        except Exception as e:
            # 超时或失败，fallback到关键词匹配
            return self._keyword_fallback(user_messages)
    
    def _extract_json(self, text: str) -> dict:
        """从模型输出中提取JSON"""
        json_match = re.search(r'\{.*\}', text, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
        raise ValueError("未找到JSON")
    
    def _keyword_fallback(self, user_messages: List[Dict]) -> SentimentAnalysisResult:
        """关键词回退（模型失败时使用）"""
        text = " ".join([m['content'] for m in user_messages])
        
        # 投诉关键词（扩展版）
        complaint_keywords = {
            '服务态度': ['态度', '不耐烦', '敷衍', '冷漠'],
            '承诺未兑现': ['答应', '承诺', '没兑现', '没做到', '说话不算'],
            '费用争议': ['乱收费', '不合理', '多收费', '费用问题'],
            '产品质量': ['质量', '坏了', '故障', '不好用', ' defective'],
            '物流问题': ['没送到', '送错', '破损', '延迟太久'],
            '信息不一致': ['说的不一样', '前后矛盾', '主播说的'],
            '退款退货': ['退货', '退款', '不要了', '取消订单']
        }
        
        for comp_type, keywords in complaint_keywords.items():
            for kw in keywords:
                if kw in text:
                    return SentimentAnalysisResult(
                        sentiment='complaint',
                        confidence=0.6,
                        is_complaint=True,
                        complaint_type=comp_type,
                        reasoning=f"关键词匹配: {kw}",
                        severity=3
                    )
        
        # 负面情绪词
        negative_words = ['不满', '失望', '生气', '郁闷', '麻烦', '差劲']
        if any(w in text for w in negative_words):
            return SentimentAnalysisResult(
                sentiment='negative',
                confidence=0.5,
                is_complaint=False,
                complaint_type='',
                reasoning='负面情绪词',
                severity=2
            )
        
        return SentimentAnalysisResult(
            sentiment='neutral',
            confidence=0.8,
            is_complaint=False,
            complaint_type='',
            reasoning='无明显情绪',
            severity=1
        )
    
    def _fallback_result(self) -> SentimentAnalysisResult:
        """默认fallback"""
        return SentimentAnalysisResult(
            sentiment='neutral',
            confidence=1.0,
            is_complaint=False,
            complaint_type='',
            reasoning='无用户消息',
            severity=1
        )
    
    def batch_analyze(self, sessions: List[List[Dict]]) -> List[SentimentAnalysisResult]:
        """批量分析（带进度）"""
        results = []
        for i, session in enumerate(sessions):
            result = self.analyze(session)
            results.append(result)
            if (i + 1) % 10 == 0:
                print(f"已分析 {i+1}/{len(sessions)} 条会话")
        return results


# 便捷函数
def quick_sentiment_check(text: str) -> Tuple[str, bool]:
    """
    快速情绪检查（单条文本）
    
    Returns:
        (sentiment, is_complaint)
    """
    analyzer = SentimentAnalyzer()
    messages = [{'role': 'customer', 'content': text}]
    result = analyzer.analyze(messages)
    return result.sentiment, result.is_complaint


if __name__ == "__main__":
    # 测试
    test_cases = [
        # 明显投诉
        [{"role": "customer", "content": "我要投诉你们客服，态度太差了"}],
        
        # 委婉投诉（承诺未兑现）
        [{"role": "customer", "content": "说的30天返现，都2个月了还没到账"}],
        
        # 委婉投诉（信息不一致）
        [{"role": "customer", "content": "你们主播说的和客服说的怎么不一样，听谁的？"}],
        
        # 正常咨询
        [{"role": "customer", "content": "这款热水器多少钱？有活动吗"}],
        
        # 负面情绪
        [{"role": "customer", "content": "这个安装费收得不合理吧"}]
    ]
    
    analyzer = SentimentAnalyzer()
    print("🧪 情绪分析测试\n")
    
    for i, messages in enumerate(test_cases, 1):
        result = analyzer.analyze(messages)
        print(f"【测试{i}】{messages[0]['content'][:40]}...")
        print(f"  情绪: {result.sentiment} (置信度: {result.confidence:.2f})")
        print(f"  是否投诉: {'是' if result.is_complaint else '否'}")
        if result.is_complaint:
            print(f"  投诉类型: {result.complaint_type}")
            print(f"  严重程度: {result.severity}/5")
        print(f"  理由: {result.reasoning}")
        print()
