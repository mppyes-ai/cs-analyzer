"""混合检索模块 - LanceDB全文+向量混合搜索

结合全文检索和向量检索的优势：
- 全文检索：精确匹配关键词（如"骗子"、"E1故障"）
- 向量检索：语义相似度（如"生气"和"愤怒"）
- 混合排序：综合两种检索结果，rerank后返回

作者: 小虾米
更新: 2026-03-18（优化：使用全局Embedding模型）
"""

import sys
import os
import numpy as np
from typing import List, Dict, Optional, Tuple

sys.path.insert(0, os.path.dirname(__file__))

from knowledge_base_v2 import get_connection

# ========== 全局Embedding模型单例 ==========
_embedding_model_cache = None

def get_cached_embedding_model():
    """获取全局缓存的Embedding模型"""
    global _embedding_model_cache
    if _embedding_model_cache is None:
        from sentence_transformers import SentenceTransformer
        _embedding_model_cache = SentenceTransformer(
            'paraphrase-multilingual-MiniLM-L12-v2',
            device='cpu'
        )
    return _embedding_model_cache

# ========== 混合检索核心类 ==========

class HybridRuleRetriever:
    """混合规则检索器（全文+向量）"""
    
    def __init__(self, 
                 embedding_model=None,
                 vector_weight: float = 0.6,
                 keyword_weight: float = 0.4):
        """
        Args:
            embedding_model: 向量模型
            vector_weight: 向量检索权重
            keyword_weight: 关键词检索权重
        """
        self.embedding_model = embedding_model
        self.vector_weight = vector_weight
        self.keyword_weight = keyword_weight
        
        # 验证权重
        assert abs(vector_weight + keyword_weight - 1.0) < 0.01, "权重之和必须等于1"
    
    def _keyword_search(self, query_keywords: List[str], 
                       scene_filter: str = None,
                       dimension_filter: str = None,
                       top_k: int = 10) -> List[Dict]:
        """全文关键词检索
        
        在SQLite中进行LIKE模糊匹配，支持多关键词OR/AND检索
        
        Args:
            query_keywords: 查询关键词列表
            scene_filter: 场景过滤
            dimension_filter: 维度过滤
            top_k: 返回数量
            
        Returns:
            匹配的规则列表（含匹配分数）
        """
        conn = get_connection()
        cursor = conn.cursor()
        
        # 构建查询
        base_query = """
            SELECT 
                rule_id, rule_type, scene_category, scene_sub_category,
                scene_description, trigger_keywords, trigger_intent, trigger_mood,
                rule_dimension, rule_criteria, rule_score_guide,
                status, full_json
            FROM rules
            WHERE status = 'approved'
            AND (trigger_valid_to IS NULL OR trigger_valid_to > datetime('now'))
        """
        params = []
        
        # 场景过滤
        if scene_filter:
            base_query += " AND scene_category = ?"
            params.append(scene_filter)
        
        # 维度过滤
        if dimension_filter:
            base_query += " AND rule_dimension = ?"
            params.append(dimension_filter)
        
        cursor.execute(base_query, params)
        all_rules = cursor.fetchall()
        conn.close()
        
        if not all_rules:
            return []
        
        columns = ['rule_id', 'rule_type', 'scene_category', 'scene_sub_category',
                   'scene_description', 'trigger_keywords', 'trigger_intent', 'trigger_mood',
                   'rule_dimension', 'rule_criteria', 'rule_score_guide',
                   'status', 'full_json']
        
        # 计算关键词匹配分数
        scored_rules = []
        for row in all_rules:
            rule = dict(zip(columns, row))
            
            # 解析JSON字段
            import json
            try:
                rule['trigger_keywords'] = json.loads(rule.get('trigger_keywords', '[]'))
            except:
                rule['trigger_keywords'] = []
            
            try:
                rule['rule_score_guide'] = json.loads(rule.get('rule_score_guide', '{}'))
            except:
                rule['rule_score_guide'] = {}
            
            # 计算匹配分数
            match_count = 0
            total_weight = 0
            
            # 1. 关键词匹配（权重最高）
            rule_keywords = [kw.lower() for kw in rule.get('trigger_keywords', [])]
            for kw in query_keywords:
                kw_lower = kw.lower()
                # 精确匹配
                if kw_lower in rule_keywords:
                    match_count += 1.0
                # 部分匹配（如"骗子"匹配"骗子行为"）
                elif any(kw_lower in rk for rk in rule_keywords):
                    match_count += 0.5
            
            if query_keywords:
                keyword_score = match_count / len(query_keywords)
            else:
                keyword_score = 0
            
            # 2. 场景描述匹配
            scene_desc = rule.get('scene_description', '').lower()
            scene_matches = sum(1 for kw in query_keywords if kw.lower() in scene_desc)
            scene_score = scene_matches / len(query_keywords) if query_keywords else 0
            
            # 3. 判定标准匹配
            criteria = rule.get('rule_criteria', '').lower()
            criteria_matches = sum(1 for kw in query_keywords if kw.lower() in criteria)
            criteria_score = criteria_matches / len(query_keywords) if query_keywords else 0
            
            # 综合分数
            final_score = keyword_score * 0.6 + scene_score * 0.2 + criteria_score * 0.2
            
            if final_score > 0:  # 只返回有匹配的
                rule['_keyword_score'] = final_score
                scored_rules.append(rule)
        
        # 排序并返回Top-K
        scored_rules.sort(key=lambda x: x['_keyword_score'], reverse=True)
        return scored_rules[:top_k]
    
    def _vector_search(self, query_text: str,
                      scene_filter: str = None,
                      dimension_filter: str = None,
                      top_k: int = 10) -> List[Dict]:
        """向量语义检索
        
        Args:
            query_text: 查询文本
            scene_filter: 场景过滤
            dimension_filter: 维度过滤
            top_k: 返回数量
            
        Returns:
            匹配的规则列表（含向量距离）
        """
        try:
            import lancedb
            
            # 使用全局缓存的模型
            model = self.embedding_model or get_cached_embedding_model()
            query_vector = model.encode(query_text).tolist()
            
            # 连接LanceDB
            LANCE_DB_PATH = os.path.join(os.path.dirname(__file__), "data", "knowledge.lance")
            db = lancedb.connect(LANCE_DB_PATH)
            
            if "rule_vectors" not in db.table_names():
                print("⚠️ 向量表不存在")
                return []
            
            table = db.open_table("rule_vectors")
            
            # 构建过滤条件
            filters = ["status = 'approved'"]
            if scene_filter:
                filters.append(f"scene_category = '{scene_filter}'")
            if dimension_filter:
                filters.append(f"rule_dimension = '{dimension_filter}'")
            
            filter_str = " AND ".join(filters)
            
            # 向量搜索
            results = table.search(query_vector).where(filter_str, prefilter=True).limit(top_k).to_pandas()
            
            # 获取完整规则
            from knowledge_base_v2 import get_rule_by_id
            
            vector_rules = []
            for _, row in results.iterrows():
                rule = get_rule_by_id(row['rule_id'])
                if rule:
                    # 向量距离转换为相似度分数（距离越小分数越高）
                    distance = row.get('_distance', 1.0)
                    # 使用指数衰减将距离转换为0-1之间的分数
                    similarity = np.exp(-distance)
                    rule['_vector_score'] = similarity
                    rule['_distance'] = distance
                    vector_rules.append(rule)
            
            return vector_rules
            
        except Exception as e:
            print(f"⚠️ 向量检索失败: {e}")
            return []
    
    def _fuse_results(self, keyword_results: List[Dict], 
                     vector_results: List[Dict],
                     top_k: int = 5) -> List[Dict]:
        """融合两种检索结果
        
        使用RRF（Reciprocal Rank Fusion）算法融合排序
        
        Args:
            keyword_results: 关键词检索结果
            vector_results: 向量检索结果
            top_k: 返回数量
            
        Returns:
            融合后的结果列表
        """
        # RRF常数
        k = 60
        
        # 收集所有规则ID
        all_rule_ids = set()
        for r in keyword_results:
            all_rule_ids.add(r['rule_id'])
        for r in vector_results:
            all_rule_ids.add(r['rule_id'])
        
        # 计算RRF分数
        rrf_scores = {}
        
        for rule_id in all_rule_ids:
            score = 0
            
            # 关键词检索排名
            for rank, rule in enumerate(keyword_results):
                if rule['rule_id'] == rule_id:
                    score += self.keyword_weight * (1 / (k + rank + 1))
                    break
            
            # 向量检索排名
            for rank, rule in enumerate(vector_results):
                if rule['rule_id'] == rule_id:
                    score += self.vector_weight * (1 / (k + rank + 1))
                    break
            
            rrf_scores[rule_id] = score
        
        # 获取完整规则并排序
        from knowledge_base_v2 import get_rule_by_id
        
        fused_results = []
        for rule_id, score in sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)[:top_k]:
            rule = get_rule_by_id(rule_id)
            if rule:
                rule['_rrf_score'] = score
                rule['_keyword_score'] = next((r['_keyword_score'] for r in keyword_results if r['rule_id'] == rule_id), 0)
                rule['_vector_score'] = next((r['_vector_score'] for r in vector_results if r['rule_id'] == rule_id), 0)
                fused_results.append(rule)
        
        return fused_results
    
    def search(self, query: str,
              scene_filter: str = None,
              dimension_filter: str = None,
              top_k: int = 5,
              use_hybrid: bool = True) -> List[Dict]:
        """混合检索（主接口）
        
        Args:
            query: 查询文本
            scene_filter: 场景过滤
            dimension_filter: 维度过滤
            top_k: 返回数量
            use_hybrid: 是否使用混合检索（False则只用向量检索）
            
        Returns:
            检索结果列表
        """
        if not use_hybrid:
            # 纯向量检索
            return self._vector_search(query, scene_filter, dimension_filter, top_k)
        
        # 1. 提取关键词（简单分词）
        # 可以在这里接入更复杂的中文分词（如jieba）
        query_keywords = [w for w in query.split() if len(w) >= 2]
        if not query_keywords:
            query_keywords = [query]  # 如果分词为空，用整句
        
        # 2. 全文关键词检索
        keyword_results = self._keyword_search(
            query_keywords, scene_filter, dimension_filter, top_k * 2
        )
        
        # 3. 向量语义检索
        vector_results = self._vector_search(
            query, scene_filter, dimension_filter, top_k * 2
        )
        
        # 4. 融合结果
        fused_results = self._fuse_results(keyword_results, vector_results, top_k)
        
        return fused_results


# ========== 便捷函数 ==========

def hybrid_search_rules(query: str, 
                       scene_filter: str = None,
                       dimension_filter: str = None,
                       top_k: int = 5) -> List[Dict]:
    """便捷函数：混合检索规则
    
    Args:
        query: 查询文本
        scene_filter: 场景过滤
        dimension_filter: 维度过滤
        top_k: 返回数量
        
    Returns:
        检索结果
    """
    retriever = HybridRuleRetriever()
    return retriever.search(query, scene_filter, dimension_filter, top_k)


# ========== 测试 ==========

def test_hybrid_search():
    """测试混合检索"""
    print("🧪 测试混合检索...")
    print("=" * 60)
    
    retriever = HybridRuleRetriever()
    
    # 测试查询
    test_queries = [
        "用户生气投诉说我们是骗子",
        "热水器显示E1故障代码",
        "安装需要预留什么尺寸",
        "政府补贴15%怎么领",
    ]
    
    for query in test_queries:
        print(f"\n🔍 查询: {query}")
        print("-" * 60)
        
        results = retriever.search(query, top_k=3)
        
        if not results:
            print("  未找到匹配规则")
            continue
        
        for idx, rule in enumerate(results, 1):
            print(f"\n  [{idx}] {rule['rule_id']}")
            print(f"      场景: {rule.get('scene_category', 'N/A')}")
            print(f"      维度: {rule.get('rule_dimension', 'N/A')}")
            print(f"      判定: {rule.get('rule_criteria', 'N/A')[:50]}...")
            print(f"      RRF分数: {rule.get('_rrf_score', 0):.4f}")
            print(f"      关键词分: {rule.get('_keyword_score', 0):.4f}")
            print(f"      向量分: {rule.get('_vector_score', 0):.4f}")


if __name__ == "__main__":
    test_hybrid_search()
