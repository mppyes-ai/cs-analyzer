# 日常客服会话自动入库方案

## 当前流程 vs 目标流程

### 当前流程（CS-Analyzer）
```
客服会话日志 → 解析 → 评分 → 生成报告 → 推送飞书
    ↓
数据用完即弃，没有沉淀到知识库
```

### 目标流程（知识图谱增强）
```
客服会话日志 → 解析 → 评分 → 提取知识 → 存入图谱 → 生成报告 → 推送飞书
    ↓
每次分析都自动丰富知识图谱
```

---

## 具体实现方案

### 1. 数据流改造

```python
# 当前：batch_analyzer.py 分析完即结束
def analyze_session(session_data):
    # 1. 解析会话
    messages = parse_messages(session_data)
    
    # 2. 评分（现有逻辑）
    scores = score_session(messages)
    
    # 3. 生成报告（现有逻辑）
    report = generate_report(scores)
    
    return report

# 目标：分析后自动提取知识
def analyze_session_enhanced(session_data):
    # 1. 解析会话（现有）
    messages = parse_messages(session_data)
    
    # 2. 评分（现有）
    scores = score_session(messages)
    
    # 3. 【新增】提取知识图谱实体
    kg = KnowledgeGraph()
    extractor = SessionExtractor(kg)
    
    analysis_result = {
        'session_analysis': {
            'scene_category': scores.get('scene_category'),
            'user_intent': scores.get('user_intent'),
            'user_sentiment': scores.get('user_sentiment')
        }
    }
    
    # 自动提取并入库
    kg_result = extractor.extract_from_session(session_data, analysis_result)
    
    # 4. 【新增】关联评分标准
    _link_scoring_rules(kg, session_data, scores)
    
    # 5. 生成报告（现有）
    report = generate_report(scores)
    
    # 6. 【新增】知识图谱统计
    report['knowledge_graph'] = {
        'new_entities': kg_result['entities'],
        'new_relations': kg_result['relations'],
        'total_entities': kg.get_stats()['entity_count'],
        'total_relations': kg.get_stats()['relation_count']
    }
    
    return report
```

### 2. 自动提取内容

| 提取类型 | 来源 | 示例 |
|---------|------|------|
| **产品实体** | 会话中的型号 | GD32、GD31、GD33 |
| **场景实体** | 分析结果 | 售前咨询-价格决策 |
| **政策实体** | 会话中的政策词 | 国补、优惠券、以旧换新 |
| **问题实体** | 用户抱怨/投诉 | 价格贵、安装费高、故障 |
| **关系** | 实体间关联 | 场景→产品、产品→政策 |

### 3. 入库触发时机

```python
# 方案A：实时入库（推荐）
# 每次分析完立即入库
class EnhancedAnalyzer:
    def __init__(self):
        self.kg = KnowledgeGraph()
        self.extractor = SessionExtractor(self.kg)
    
    def analyze_and_learn(self, session_data):
        # 1. 正常分析
        result = self.analyze(session_data)
        
        # 2. 提取知识
        kg_result = self.extractor.extract_from_session(
            session_data, 
            result
        )
        
        # 3. 记录学习日志
        logger.info(f"知识图谱更新: +{len(kg_result['entities'])}实体, +{kg_result['relations']}关系")
        
        return result

# 方案B：批量入库（备选）
# 每日/每小时批量处理
class BatchKnowledgeSync:
    def __init__(self):
        self.kg = KnowledgeGraph()
        self.extractor = SessionExtractor(self.kg)
    
    def daily_sync(self, date):
        # 1. 获取昨日所有分析完成的会话
        sessions = self.get_completed_sessions(date)
        
        # 2. 批量提取
        for session in sessions:
            self.extractor.extract_from_session(session)
        
        # 3. 生成日报
        stats = self.kg.get_stats()
        return {
            'date': date,
            'processed_sessions': len(sessions),
            'total_entities': stats['entity_count'],
            'total_relations': stats['relation_count'],
            'new_entities_today': stats['new_today']
        }
```

### 4. 数据质量保障

```python
class KnowledgeQuality:
    """知识质量管控"""
    
    def validate_entity(self, entity):
        """验证实体质量"""
        checks = {
            'id_format': self._check_id_format(entity['id']),
            'name_length': len(entity['name']) <= 50,  # 名称不超过50字
            'attributes': self._check_attributes(entity['attributes']),
            'duplicate': not self._is_duplicate(entity)
        }
        return all(checks.values()), checks
    
    def merge_similar(self, entity1, entity2):
        """合并相似实体"""
        similarity = self._calculate_similarity(entity1, entity2)
        if similarity > 0.85:
            # 合并属性
            merged = {
                **entity1,
                'attributes': {**entity1['attributes'], **entity2['attributes']}
            }
            return merged
        return None
    
    def resolve_conflict(self, old_entity, new_entity):
        """解决冲突"""
        # 保留更详细的属性
        if len(str(old_entity['attributes'])) < len(str(new_entity['attributes'])):
            return new_entity
        return old_entity
```

### 5. 知识图谱增长预期

| 数据源 | 日产量 | 月产量 | 知识提取率 |
|--------|--------|--------|-----------|
| 客服会话 | 1000-5000通 | 3-15万通 | 10-20% |
| 提取实体 | 100-500个 | 3000-15000个 | - |
| 建立关系 | 200-1000条 | 6000-30000条 | - |

**3个月后预期规模：**
- 实体：1-5万个
- 关系：2-10万条
- 覆盖场景：90%+常见客服场景

---

## 实施步骤

### Step 1：改造分析流程（1-2天）

```python
# 在 batch_analyzer.py 中添加知识提取
from knowledge_graph import KnowledgeGraph, SessionExtractor

class BatchAnalyzer:
    def __init__(self):
        # 现有初始化...
        self.kg = KnowledgeGraph()
        self.extractor = SessionExtractor(self.kg)
    
    def process_session(self, session_data):
        # 现有分析逻辑...
        analysis_result = self.analyze(session_data)
        
        # 【新增】提取知识
        if analysis_result:
            self.extractor.extract_from_session(session_data, analysis_result)
        
        return analysis_result
```

### Step 2：添加监控面板（2-3天）

```python
# 在知识库首页显示实时统计
st.metric("今日新增实体", kg_stats['new_entities_today'])
st.metric("知识图谱实体总数", kg_stats['total_entities'])
st.metric("关系总数", kg_stats['total_relations'])

# 显示最新提取的知识
st.subheader("最新发现")
for entity in kg.get_recent_entities(limit=5):
    st.write(f"[{entity['type']}] {entity['name']}")
```

### Step 3：质量审核机制（3-5天）

```python
# 人工审核新提取的知识
class KnowledgeReview:
    def get_pending_review(self):
        """获取待审核实体"""
        return self.kg.get_entities(status='pending_review')
    
    def approve(self, entity_id):
        """审核通过"""
        self.kg.update_entity(entity_id, status='approved')
    
    def reject(self, entity_id, reason):
        """审核拒绝"""
        self.kg.update_entity(entity_id, status='rejected', reject_reason=reason)
```

---

## 金总，核心收益

| 收益 | 说明 |
|------|------|
| **自动积累** | 无需人工整理，分析即沉淀 |
| **实时更新** | 新知识立即可用，不用等月度汇总 |
| **数据驱动** | 基于真实会话，不是拍脑袋编规则 |
| **持续进化** | 用得越多，知识图谱越丰富越准确 |

**简单说：现在分析客服会话，只是出一份报告。改造后，每分析一通会话，知识库就自动变聪明一点。**

---

**金总，是否立即改造分析流程，实现会话数据自动入库？**