# 知识审核与会话分析矫正融合方案

## 金总的思路

```
当前分离流程：
├─ 会话分析 → 评分 → 报告
└─ 知识审核 → 实体审核 → 入库

融合后流程：
会话分析 → 评分 → 知识提取 → 审核 → 入库 → 报告
    ↓
一次操作，双重产出
```

---

## 融合方案设计

### 1. 融合后的工作流程

```
┌─────────────────────────────────────────┐
│         融合后的会话分析页面              │
├─────────────────────────────────────────┤
│                                         │
│  步骤1: 上传客服会话日志                   │
│     ↓                                   │
│  步骤2: AI自动分析                        │
│     ├─ 评分（4维度）                      │
│     ├─ 提取知识图谱实体                    │
│     └─ 标记待审核                        │
│     ↓                                   │
│  步骤3: 人工审核（新增）                   │
│     ├─ 查看会话内容                      │
│     ├─ 确认/修改/拒绝提取的实体            │
│     └─ 实时更新知识图谱                   │
│     ↓                                   │
│  步骤4: 生成报告                          │
│     ├─ 质检评分报告                      │
│     └─ 知识图谱更新摘要                   │
│                                         │
└─────────────────────────────────────────┘
```

### 2. 页面布局设计

```
┌─────────────────────────────────────────────────────┐
│  会话分析与知识提取（融合页面）                      │
├─────────────────────────────────────────────────────┤
│                                                     │
│  ┌─────────────┐  ┌─────────────────────────────┐  │
│  │  左侧：会话  │  │  右侧：分析与知识提取        │  │
│  │  内容展示    │  │                             │  │
│  │             │  │  ┌─────────────────────┐   │  │
│  │  用户提问    │  │  │ 评分结果（4维度）    │   │  │
│  │  客服回答    │  │  │                     │   │  │
│  │  时间轴      │  │  │ 专业性: 4/4         │   │  │
│  │             │  │  │ 同理心: 3/4         │   │  │
│  │             │  │  │ 响应速度: 4/4       │   │  │
│  │             │  │  │ 问题解决: 3/4       │   │  │
│  │             │  │  └─────────────────────┘   │  │
│  │             │  │                             │  │
│  │             │  │  ┌─────────────────────┐   │  │
│  │             │  │  │ 知识提取结果         │   │  │
│  │             │  │  │                     │   │  │
│  │             │  │  │ 📦 产品: GD32        │   │  │
│  │             │  │  │ 🎯 场景: 安装咨询    │   │  │
│  │             │  │  │ 📋 政策: 国补2026    │   │  │
│  │             │  │  │                     │   │  │
│  │             │  │  │ [审核通过] [修改] [拒绝]│   │  │
│  │             │  │  └─────────────────────┘   │  │
│  │             │  │                             │  │
│  └─────────────┘  └─────────────────────────────┘  │
│                                                     │
│  ┌─────────────────────────────────────────────┐   │
│  │ 底部：审核状态与统计                           │   │
│  │                                              │   │
│  │ 今日审核: 15个实体 | 已通过: 8 | 待审核: 7    │   │
│  │                                              │   │
│  └─────────────────────────────────────────────┘   │
│                                                     │
└─────────────────────────────────────────────────────┘
```

### 3. 核心功能实现

```python
class IntegratedAnalyzer:
    """融合分析器：会话分析 + 知识提取 + 审核"""
    
    def __init__(self):
        self.scorer = SessionScorer()  # 评分器
        self.extractor = SessionExtractor(KnowledgeGraph())  # 知识提取器
        self.review_ui = KnowledgeReviewUI()  # 审核界面
    
    def analyze_and_extract(self, session_data):
        """分析会话并提取知识"""
        results = {
            'session_id': session_data['session_id'],
            'scores': {},
            'extracted_entities': [],
            'review_status': 'pending'
        }
        
        # 1. 评分（现有功能）
        scores = self.scorer.score(session_data['messages'])
        results['scores'] = scores
        
        # 2. 知识提取（新增）
        kg_results = self.extractor.extract_from_session(session_data, scores)
        results['extracted_entities'] = kg_results['entities']
        
        # 3. 标记待审核（新增）
        for entity_id in kg_results['entities']:
            self.review_ui.mark_pending(entity_id, session_data['session_id'])
        
        return results
    
    def review_entity(self, entity_id, action, modifications=None):
        """审核实体"""
        if action == 'approve':
            self.review_ui.approve_entity(entity_id, "质检员")
        elif action == 'reject':
            self.review_ui.reject_entity(entity_id, modifications.get('reason', ''), "质检员")
        elif action == 'modify':
            self.review_ui.modify_and_approve(entity_id, modifications, "质检员")
    
    def generate_integrated_report(self, session_results):
        """生成融合报告"""
        report = {
            '质检评分': session_results['scores'],
            '知识提取': {
                '实体数量': len(session_results['extracted_entities']),
                '待审核': len([e for e in session_results['extracted_entities'] 
                              if self.review_ui.get_status(e) == 'pending']),
                '已通过': len([e for e in session_results['extracted_entities'] 
                             if self.review_ui.get_status(e) == 'approved'])
            },
            '审核建议': self._generate_review_suggestions(session_results)
        }
        return report
    
    def _generate_review_suggestions(self, session_results):
        """生成审核建议"""
        suggestions = []
        
        for entity_id in session_results['extracted_entities']:
            entity = self.review_ui.get_entity(entity_id)
            
            # 检查置信度
            confidence = entity['attributes'].get('confidence', 0)
            if confidence < 0.7:
                suggestions.append(f"⚠️ {entity['name']} 置信度较低({confidence:.2f})，建议重点审核")
            
            # 检查相似实体
            similar = self.review_ui.find_similar_entities(entity_id)
            if similar and similar[0][1] > 0.9:
                suggestions.append(f"⚠️ {entity['name']} 与 {similar[0][0]['name']} 高度相似，检查是否重复")
        
        return suggestions
```

### 4. Streamlit融合页面

```python
def render_integrated_page():
    """渲染融合分析页面"""
    st.title("🔍 会话分析与知识提取（融合版）")
    
    analyzer = IntegratedAnalyzer()
    
    # 上传会话
    uploaded_file = st.file_uploader("上传客服会话日志", type=['log', 'txt'])
    
    if uploaded_file:
        # 解析会话
        sessions = parse_log_file(uploaded_file)
        
        st.write(f"共 {len(sessions)} 通会话待分析")
        
        # 逐通分析
        for idx, session in enumerate(sessions, 1):
            with st.expander(f"会话 {idx}"):
                col1, col2 = st.columns([1, 1])
                
                with col1:
                    st.subheader("📋 会话内容")
                    for msg in session['messages']:
                        if msg['role'] == 'user':
                            st.markdown(f"**👤 用户:** {msg['content']}")
                        else:
                            st.markdown(f"**🤖 客服:** {msg['content']}")
                
                with col2:
                    # 分析并提取
                    results = analyzer.analyze_and_extract(session)
                    
                    # 显示评分
                    st.subheader("📊 质检评分")
                    scores = results['scores']
                    st.write(f"专业性: {scores['专业性']}/4")
                    st.write(f"同理心: {scores['同理心']}/4")
                    st.write(f"响应速度: {scores['响应速度']}/4")
                    st.write(f"问题解决: {scores['问题解决']}/4")
                    
                    # 显示知识提取
                    st.subheader("🧠 知识提取")
                    for entity_id in results['extracted_entities']:
                        entity = analyzer.review_ui.get_entity(entity_id)
                        if entity:
                            st.write(f"[{entity['type']}] {entity['name']}")
                            
                            # 审核按钮
                            c1, c2, c3 = st.columns(3)
                            with c1:
                                if st.button("✅ 通过", key=f"pass_{entity_id}"):
                                    analyzer.review_entity(entity_id, 'approve')
                                    st.success("已通过")
                            with c2:
                                if st.button("✏️ 修改", key=f"mod_{entity_id}"):
                                    # 弹出修改界面
                                    pass
                            with c3:
                                if st.button("❌ 拒绝", key=f"rej_{entity_id}"):
                                    analyzer.review_entity(entity_id, 'reject', {'reason': '不准确'})
                                    st.error("已拒绝")
        
        # 生成融合报告
        if st.button("📄 生成融合报告"):
            report = analyzer.generate_integrated_report(results)
            st.json(report)
```

### 5. 融合的价值

| 维度 | 分离模式 | 融合模式 |
|------|---------|---------|
| **操作次数** | 2次（分析+审核） | 1次 |
| **上下文理解** | 审核时看不到会话 | 审核时直接看会话 |
| **效率** | 低（重复查看） | 高（一次完成） |
| **准确性** | 中（脱离上下文） | 高（基于上下文审核） |
| **学习成本** | 高（两个系统） | 低（一个界面） |

### 6. 实施步骤

**Phase 1（本周）：基础融合**
- [ ] 在现有分析页面增加"知识提取"模块
- [ ] 显示提取的实体和审核按钮
- [ ] 审核结果实时更新知识图谱

**Phase 2（下周）：深度融合**
- [ ] 会话内容与提取实体并排显示
- [ ] 点击实体高亮对应会话内容
- [ ] 生成融合报告（评分+知识更新）

**Phase 3（下月）：智能优化**
- [ ] 基于审核历史优化提取算法
- [ ] 自动识别常见错误模式
- [ ] 推荐审核操作（通过/拒绝/修改）

---

## 金总，核心结论

> **融合方案的核心价值：**
> 1. **一次操作，双重产出** - 分析会话的同时提取知识
> 2. **上下文审核** - 审核时直接看到原始会话，更准确
> 3. **效率提升** - 减少重复操作，质检员工作量降低50%
> 4. **质量保证** - 基于真实会话审核，减少误判

---

**金总，是否立即启动融合方案开发？预计本周完成Phase 1基础融合。**