# 知识图谱质量控制方案

## 问题1：如何确保提取内容正确？

### 多层校验机制

```
┌─────────────────────────────────────────┐
│           知识提取质量管控               │
├─────────────────────────────────────────┤
│                                         │
│  第一层：机器自动校验                     │
│  ├─ 格式校验（ID、类型、必填字段）        │
│  ├─ 置信度过滤（<0.7不入库）            │
│  ├─ 重复检测（相似度>0.85合并）          │
│  └─ 冲突检测（同一实体属性矛盾）          │
│                                         │
│  第二层：专家规则审核                     │
│  ├─ 产品参数必须匹配官方规格书            │
│  ├─ 政策信息必须验证有效期               │
│  ├─ 价格数据必须与实际一致               │
│  └─ 场景分类必须符合业务定义             │
│                                         │
│  第三层：人工抽样审核                     │
│  ├─ 每日随机抽取10%新实体                │
│  ├─ 质检员确认/修正                      │
│  └─ 错误反馈闭环优化                     │
│                                         │
└─────────────────────────────────────────┘
```

### 具体校验规则

```python
class KnowledgeValidator:
    """知识校验器"""
    
    def validate_entity(self, entity):
        """实体校验"""
        checks = {
            # 1. 格式校验
            'id_format': self._check_id_format(entity['id']),
            'type_valid': entity['type'] in ['Product', 'Scene', 'Policy', 'Customer', 'Competitor', 'Page'],
            'name_length': 0 < len(entity['name']) <= 50,
            'attributes_not_empty': bool(entity.get('attributes')),
            
            # 2. 内容校验
            'no_sensitive_info': self._check_no_sensitive(entity),
            'no_garbage_text': self._check_no_garbage(entity['name']),
            'chinese_ratio': self._check_chinese_ratio(entity['name']),
        }
        
        return all(checks.values()), checks
    
    def validate_relation(self, relation, kg):
        """关系校验"""
        checks = {
            # 1. 端点存在
            'from_exists': kg.get_entity(relation['from_id']) is not None,
            'to_exists': kg.get_entity(relation['to_id']) is not None,
            
            # 2. 类型合法
            'type_valid': relation['type'] in [
                '升级版本', '适用政策', '常见咨询', '推荐产品',
                '已购买', '直接竞争', '展示产品', '存在问题',
                '涉及产品', '对比竞品'
            ],
            
            # 3. 无重复关系
            'not_duplicate': not self._is_duplicate_relation(relation, kg),
        }
        
        return all(checks.values()), checks
    
    def _check_id_format(self, entity_id):
        """检查ID格式"""
        # 格式: type_品牌_型号 或 type_类别_子类别
        pattern = r'^(product|scene|policy|customer|competitor|page)_[a-zA-Z0-9\u4e00-\u9fa5_]+$'
        return bool(re.match(pattern, entity_id))
    
    def _check_no_sensitive(self, entity):
        """检查是否包含敏感信息"""
        sensitive_keywords = ['密码', '手机号', '身份证', '银行卡']
        text = json.dumps(entity, ensure_ascii=False)
        return not any(kw in text for kw in sensitive_keywords)
    
    def _check_no_garbage(self, text):
        """检查是否为乱码/垃圾文本"""
        # 检查特殊字符比例
        special_chars = sum(1 for c in text if not c.isalnum() and not c.isspace() and c not in '._-')
        return special_chars / len(text) < 0.3 if text else False
    
    def _check_chinese_ratio(self, text):
        """检查中文比例（防止英文乱入）"""
        if not text:
            return True
        chinese_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
        return chinese_chars / len(text) > 0.3 or len(text) < 10
```

### 置信度评分

```python
class ConfidenceScorer:
    """置信度评分"""
    
    def score_entity(self, entity, source):
        """计算实体置信度"""
        scores = {
            'source_reliability': self._source_score(source),  # 来源可靠性
            'extraction_clarity': self._clarity_score(entity),  # 提取清晰度
            'context_support': self._context_score(entity),  # 上下文支持度
            'cross_validation': self._cross_validation_score(entity),  # 交叉验证
        }
        
        # 加权平均
        weights = {'source_reliability': 0.3, 'extraction_clarity': 0.3, 
                   'context_support': 0.2, 'cross_validation': 0.2}
        
        confidence = sum(scores[k] * weights[k] for k in scores)
        return confidence
    
    def _source_score(self, source):
        """来源可靠性评分"""
        source_weights = {
            'official_document': 1.0,  # 官方文档
            'trained_analyst': 0.9,     # 训练有素的分析师
            'ai_extraction': 0.7,       # AI自动提取
            'user_feedback': 0.6,       # 用户反馈
            'unknown': 0.3              # 未知来源
        }
        return source_weights.get(source, 0.5)
    
    def _cross_validation_score(self, entity):
        """交叉验证评分"""
        # 检查是否有多个独立来源支持同一实体
        # 例如：多个会话提到同一产品参数
        pass
```

---

## 问题2：相同内容/不同答案如何处理？

### 场景分类

```
┌─────────────────────────────────────────┐
│           冲突处理场景                   │
├─────────────────────────────────────────┤
│                                         │
│  场景A：完全相同的实体                   │
│  例：两个"林内GD32"实体                 │
│  → 自动合并，属性取并集                  │
│                                         │
│  场景B：同一实体，不同属性               │
│  例：GD32价格=3199 vs 3299              │
│  → 标记冲突，人工审核                    │
│                                         │
│  场景C：相似实体，不同粒度               │
│  例："林内GD32" vs "林内燃气热水器GD32"  │
│  → 规范化名称，合并为同一实体            │
│                                         │
│  场景D：同一关系，不同属性               │
│  例：GD32适用国补=20% vs 15%             │
│  → 时间戳优先，或人工确认                │
│                                         │
└─────────────────────────────────────────┘
```

### 冲突解决策略

```python
class ConflictResolver:
    """冲突解决器"""
    
    def resolve(self, existing, new, conflict_type):
        """主冲突解决入口"""
        resolvers = {
            'duplicate_entity': self._merge_duplicate_entity,
            'conflicting_attributes': self._resolve_attribute_conflict,
            'similar_entity': self._merge_similar_entity,
            'conflicting_relation': self._resolve_relation_conflict,
        }
        
        resolver = resolvers.get(conflict_type)
        if resolver:
            return resolver(existing, new)
        
        return None, 'unknown_conflict_type'
    
    def _merge_duplicate_entity(self, existing, new):
        """合并完全相同的实体"""
        # 策略：属性取并集，保留更详细的
        merged_attributes = {**existing['attributes']}
        
        for key, new_value in new['attributes'].items():
            if key not in merged_attributes:
                # 新属性，直接添加
                merged_attributes[key] = new_value
            else:
                # 已有属性，选择更详细的
                old_value = merged_attributes[key]
                merged_attributes[key] = self._select_better_value(old_value, new_value)
        
        merged = {
            **existing,
            'attributes': merged_attributes,
            'merge_history': existing.get('merge_history', []) + [{
                'merged_at': datetime.now().isoformat(),
                'source': new.get('source', 'unknown')
            }]
        }
        
        return merged, 'merged'
    
    def _resolve_attribute_conflict(self, existing, new):
        """解决属性冲突"""
        conflicts = []
        
        for key in set(existing['attributes'].keys()) & set(new['attributes'].keys()):
            old_val = existing['attributes'][key]
            new_val = new['attributes'][key]
            
            if old_val != new_val:
                conflicts.append({
                    'attribute': key,
                    'old_value': old_val,
                    'new_value': new_val,
                    'resolution': self._decide_resolution(key, old_val, new_val)
                })
        
        if not conflicts:
            return self._merge_duplicate_entity(existing, new)
        
        # 有冲突，标记待审核
        return {
            **existing,
            'pending_conflicts': conflicts,
            'status': 'pending_review'
        }, 'pending_review'
    
    def _decide_resolution(self, attribute, old_val, new_val):
        """决定如何解决特定属性的冲突"""
        # 根据属性类型决定策略
        strategies = {
            'price': 'keep_latest',           # 价格：保留最新
            'valid_to': 'keep_latest',        # 有效期：保留最新
            'valid_from': 'keep_earliest',    # 生效日期：保留最早
            'features': 'merge_list',         # 功能列表：合并
            'description': 'keep_longer',       # 描述：保留更详细的
        }
        
        strategy = strategies.get(attribute, 'manual_review')
        
        if strategy == 'keep_latest':
            return {'strategy': 'keep_latest', 'value': new_val}
        elif strategy == 'keep_earliest':
            return {'strategy': 'keep_earliest', 'value': old_val}
        elif strategy == 'merge_list':
            if isinstance(old_val, list) and isinstance(new_val, list):
                merged = list(set(old_val + new_val))
                return {'strategy': 'merge_list', 'value': merged}
        elif strategy == 'keep_longer':
            longer = old_val if len(str(old_val)) > len(str(new_val)) else new_val
            return {'strategy': 'keep_longer', 'value': longer}
        
        return {'strategy': 'manual_review', 'old': old_val, 'new': new_val}
    
    def _merge_similar_entity(self, existing, new):
        """合并相似实体（名称不同但指同一事物）"""
        # 使用标准化名称
        normalized_name = self._normalize_name(existing['name'], new['name'])
        
        merged = {
            **existing,
            'name': normalized_name,
            'aliases': list(set(
                existing.get('aliases', []) + [new['name']]
            )),
            'attributes': {**existing['attributes'], **new['attributes']}
        }
        
        return merged, 'merged_similar'
    
    def _resolve_relation_conflict(self, existing_rel, new_rel):
        """解决关系冲突"""
        # 关系冲突较少，通常保留属性更详细的
        if len(str(existing_rel.get('attributes', {}))) >= len(str(new_rel.get('attributes', {}))):
            return existing_rel, 'keep_existing'
        
        return new_rel, 'keep_new'
    
    def _select_better_value(self, old_val, new_val):
        """选择更好的属性值"""
        # 列表合并
        if isinstance(old_val, list) and isinstance(new_val, list):
            return list(set(old_val + new_val))
        
        # 字符串取更长的（通常更详细）
        if isinstance(old_val, str) and isinstance(new_val, str):
            return old_val if len(old_val) > len(new_val) else new_val
        
        # 数字取最新的（如果有时间戳）
        return new_val  # 默认取新值
    
    def _normalize_name(self, name1, name2):
        """标准化名称"""
        # 提取共同部分
        # 例如："林内GD32"和"林内燃气热水器GD32" → "林内GD32"
        if name1 in name2:
            return name1
        if name2 in name1:
            return name2
        
        # 取更短的（通常更标准）
        return name1 if len(name1) < len(name2) else name2


class DuplicateDetector:
    """重复检测器"""
    
    def __init__(self):
        self.similarity_threshold = 0.85
    
    def find_duplicates(self, entity, kg):
        """查找相似实体"""
        candidates = []
        
        # 1. 精确匹配ID
        exact = kg.get_entity(entity['id'])
        if exact:
            candidates.append(('exact', exact, 1.0))
        
        # 2. 名称相似度匹配
        all_entities = kg.get_entities_by_type(entity['type'])
        for candidate in all_entities:
            if candidate['id'] == entity['id']:
                continue
            
            name_sim = self._name_similarity(entity['name'], candidate['name'])
            if name_sim >= self.similarity_threshold:
                candidates.append(('similar_name', candidate, name_sim))
            
            # 3. 属性相似度
            attr_sim = self._attribute_similarity(
                entity.get('attributes', {}),
                candidate.get('attributes', {})
            )
            if attr_sim >= self.similarity_threshold:
                candidates.append(('similar_attrs', candidate, attr_sim))
        
        # 按相似度排序
        candidates.sort(key=lambda x: x[2], reverse=True)
        return candidates
    
    def _name_similarity(self, name1, name2):
        """计算名称相似度"""
        from difflib import SequenceMatcher
        return SequenceMatcher(None, name1, name2).ratio()
    
    def _attribute_similarity(self, attrs1, attrs2):
        """计算属性相似度"""
        if not attrs1 or not attrs2:
            return 0.0
        
        # 计算共同属性比例
        keys1 = set(attrs1.keys())
        keys2 = set(attrs2.keys())
        
        if not keys1 or not keys2:
            return 0.0
        
        intersection = keys1 & keys2
        union = keys1 | keys2
        
        return len(intersection) / len(union)
```

### 人工审核工作流

```python
class KnowledgeReviewWorkflow:
    """知识审核工作流"""
    
    def __init__(self):
        self.kg = KnowledgeGraph()
        self.resolver = ConflictResolver()
        self.validator = KnowledgeValidator()
    
    def process_new_knowledge(self, entities, relations):
        """处理新知识"""
        results = {
            'auto_approved': [],
            'pending_review': [],
            'rejected': [],
            'merged': []
        }
        
        for entity in entities:
            # 1. 自动校验
            is_valid, checks = self.validator.validate_entity(entity)
            if not is_valid:
                results['rejected'].append({
                    'entity': entity,
                    'reason': 'validation_failed',
                    'checks': checks
                })
                continue
            
            # 2. 检查重复
            duplicates = self.duplicate_detector.find_duplicates(entity, self.kg)
            
            if duplicates:
                best_match = duplicates[0]
                
                if best_match[0] == 'exact':
                    # 完全重复，自动合并
                    merged, status = self.resolver.resolve(
                        best_match[1], entity, 'duplicate_entity'
                    )
                    self.kg.update_entity(merged['id'], merged)
                    results['merged'].append({'entity': entity, 'with': best_match[1]})
                    
                elif best_match[2] > 0.95:
                    # 高度相似，自动合并
                    merged, status = self.resolver.resolve(
                        best_match[1], entity, 'similar_entity'
                    )
                    self.kg.update_entity(merged['id'], merged)
                    results['merged'].append({'entity': entity, 'with': best_match[1]})
                    
                else:
                    # 可能相关，人工确认
                    results['pending_review'].append({
                        'entity': entity,
                        'similar_to': best_match[1],
                        'similarity': best_match[2]
                    })
            else:
                # 全新实体，检查置信度
                confidence = self.scorer.score_entity(entity, 'ai_extraction')
                
                if confidence >= 0.8:
                    # 高置信度，自动入库
                    self.kg.add_entity(**entity)
                    results['auto_approved'].append(entity)
                else:
                    # 低置信度，人工审核
                    results['pending_review'].append({
                        'entity': entity,
                        'reason': 'low_confidence',
                        'confidence': confidence
                    })
        
        return results
    
    def get_pending_review(self):
        """获取待审核列表"""
        return self.kg.get_entities(status='pending_review')
    
    def approve(self, entity_id, reviewer):
        """审核通过"""
        self.kg.update_entity(entity_id, {
            'status': 'approved',
            'approved_by': reviewer,
            'approved_at': datetime.now().isoformat()
        })
    
    def reject(self, entity_id, reason, reviewer):
        """审核拒绝"""
        self.kg.update_entity(entity_id, {
            'status': 'rejected',
            'reject_reason': reason,
            'rejected_by': reviewer,
            'rejected_at': datetime.now().isoformat()
        })
    
    def modify_and_approve(self, entity_id, modifications, reviewer):
        """修改后通过"""
        entity = self.kg.get_entity(entity_id)
        if entity:
            modified = {**entity, **modifications}
            self.kg.update_entity(entity_id, modified)
            self.approve(entity_id, reviewer)
```

---

## 实施建议

### 第一阶段：自动+人工（当前）

```
AI提取 → 自动校验 → 高置信度自动入库
                    → 低置信度/冲突 → 人工审核 → 确认入库
```

### 第二阶段：逐步自动化（3个月后）

```
AI提取 → 自动校验 → 90%自动入库
                    → 10%疑难案例 → 人工审核
```

### 审核界面设计

```python
# Streamlit审核界面
st.header("知识审核中心")

# 待审核列表
pending = review_workflow.get_pending_review()
st.write(f"待审核: {len(pending)} 条")

for item in pending:
    with st.expander(f"[{item['entity']['type']}] {item['entity']['name']}"):
        st.json(item['entity'])
        
        if 'similar_to' in item:
            st.warning(f"相似实体: {item['similar_to']['name']} (相似度: {item['similarity']:.2f})")
        
        col1, col2, col3 = st.columns(3)
        with col1:
            if st.button("✅ 通过", key=f"approve_{item['entity']['id']}"):
                review_workflow.approve(item['entity']['id'], "质检员A")
        with col2:
            if st.button("❌ 拒绝", key=f"reject_{item['entity']['id']}"):
                review_workflow.reject(item['entity']['id'], "信息不准确", "质检员A")
        with col3:
            if st.button("✏️ 修改", key=f"modify_{item['entity']['id']}"):
                # 弹出修改界面
                pass
```

---

## 金总，总结

| 问题 | 解决方案 |
|------|---------|
| **提取内容是否正确** | 三层校验：机器自动校验 → 专家规则 → 人工抽样 |
| **相同内容** | 自动合并，属性取并集 |
| **不同答案** | 冲突标记 → 策略解决（时间优先/详细优先） → 人工兜底 |

**核心原则：机器处理80%常规情况，人工专注20%疑难案例。**

---

**金总，是否实施质量控制方案？**