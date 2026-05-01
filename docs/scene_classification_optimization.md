# 客服会话场景分类优化方案

## 当前问题

| 问题 | 案例 | 影响 |
|------|------|------|
| 关键词冲突 | "留孔"被"收费"干扰 | 场景分类错误 |
| 意图遗漏 | "一厨两卫"未识别 | 分类为一般咨询 |
| 混合意图 | 优惠券+安装 | 只识别第一个 |

## 优化策略

### 1. 用户消息优先（仅分析用户提问）

```python
# 当前：分析所有消息（包含客服回复）
combined = ' '.join([m['content'] for m in messages])

# 优化：仅分析用户消息
user_combined = ' '.join([m['content'] for m in messages if m['role'] == 'user'])
```

**原因**：客服回复中可能包含干扰词（如"收费"、"安装"等），应基于用户真实意图分类。

### 2. 增强关键词库

```python
# 售前咨询 - 安装（增强）
'安装', '尺寸', '预留', '辅材', '孔', '烟管', '吊顶', 
'预埋', '打孔', '开孔', '墙体', '蜂窝大板', '石膏板'

# 售前咨询 - 容量/选型（新增）
'几升', '多少升', '够用吗', '一厨', '两卫', '三口人', 
'四口人', '五口人', '选型', '推荐', '哪款', '主推款'

# 售中服务（增强）
'发货', '延迟发货', '物流', '快递', '什么时候到', 
'订单', '配送', '送达', '期望日期'
```

### 3. 混合意图识别

```python
def detect_mixed_intent(user_messages):
    """检测混合意图"""
    intents = []
    
    for msg in user_messages:
        content = msg['content']
        
        # 检查每个消息独立的意图
        if any(kw in content for kw in ['优惠', '券', '活动', '价格']):
            intents.append('价格决策')
        
        if any(kw in content for kw in ['安装', '尺寸', '预留']):
            intents.append('安装咨询')
        
        if any(kw in content for kw in ['发货', '物流']):
            intents.append('订单确认')
    
    # 去重
    unique_intents = list(set(intents))
    
    if len(unique_intents) > 1:
        return '混合意图', unique_intents
    
    return unique_intents[0] if unique_intents else '一般咨询'
```

### 4. 置信度评分

```python
def score_classification(user_messages, predicted_scene):
    """评分分类置信度"""
    
    # 1. 关键词匹配度
    keywords = scene_keywords[predicted_scene]
    matched = sum(1 for kw in keywords if kw in user_combined)
    keyword_score = matched / len(keywords)
    
    # 2. 消息长度（越长越可能混合意图）
    length_penalty = min(len(user_combined) / 100, 1.0)
    
    # 3. 历史准确率（基于反馈）
    historical_score = get_historical_accuracy(predicted_scene)
    
    confidence = (keyword_score * 0.5 + historical_score * 0.3 + (1-length_penalty) * 0.2)
    
    return confidence
```

## 优化后预期效果

| 会话 | 优化前 | 优化后 |
|------|--------|--------|
| 会话3（留孔） | 价格决策 ❌ | 安装咨询 ✅ |
| 会话4（容量） | 一般咨询 ❌ | 产品选型 ✅ |
| 会话5（混合） | 价格决策 ❌ | 混合意图（价格+安装）✅ |

## 实施步骤

1. **立即**：修改关键词匹配逻辑（仅分析用户消息）
2. **1周内**：扩充关键词库，增加常见口语表达
3. **2周内**：实现混合意图识别
4. **1月内**：基于反馈数据优化置信度模型

---

**金总，是否立即实施优化方案？**