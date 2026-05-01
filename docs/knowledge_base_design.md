# CS-Analyzer 知识库功能设计方案

## 目标
设计一套完整的知识库功能，实现：
1. **质检-知识双向飞轮**：质检沉淀知识，知识增强质检
2. **三层知识架构**：动态质检知识 + 静态业务知识 + 融合推理知识
3. **人工友好维护**：可视化编辑、版本管理、效果追踪
4. **Streamlit前端适配**：保持现有UI风格，新增知识库页面

---

## 功能模块设计

### 模块1：知识库首页（新增页面）
**页面**：`pages/8_🏠_知识库首页.py`

**功能**：
- 知识库总览统计（规则总数、待审核、已生效、今日新增）
- 最近更新动态（规则修改、新增、审批）
- 快捷入口（规则审核、规则录入、知识检索、效果分析）
- 知识库健康度（覆盖率、冲突数、过期规则）

**Streamlit组件**：
```python
st.metric() - 统计卡片
st.columns() - 布局
st.container() - 区域划分
st.button() - 快捷入口
```

---

### 模块2：规则审核优化（升级现有页面）
**页面**：`pages/7_📚_规则审核_v2.py` → `pages/7_📚_规则审核_v3.py`

**新增功能**：
1. **版本对比**：显示规则修改前后的差异
2. **审批流程**：提交 → 审核 → 批准/拒绝 → 生效
3. **批量操作**：批量批准、批量拒绝、批量删除
4. **规则关联**：显示关联的矫正记录、会话详情

**Streamlit组件**：
```python
st.dataframe() - 规则列表
st.expander() - 展开详情
st.columns() - 并排对比
st.form() - 审批表单
st.toast() - 操作提示
```

---

### 模块3：规则录入（新增页面）
**页面**：`pages/9_📝_规则录入.py`

**功能**：
1. **可视化编辑器**：
   - 场景选择（下拉框）
   - 触发条件（关键词、意图、情绪）
   - 评分标准（1/3/5分详细描述）
   - 规则权重（滑块调整）
   
2. **模板库**：
   - 常见规则模板（售前咨询、客诉处理等）
   - 一键导入模板
   
3. **实时预览**：
   - 输入规则后，实时显示Prompt效果
   - 模拟评分结果预览

**Streamlit组件**：
```python
st.selectbox() - 场景选择
st.text_input() - 关键词输入
st.text_area() - 评分标准描述
st.slider() - 权重调整
st.tabs() - 模板分类
st.code() - Prompt预览
```

---

### 模块4：知识检索（新增页面）
**页面**：`pages/10_🔍_知识检索.py`

**功能**：
1. **多维度检索**：
   - 按场景检索
   - 按维度检索（专业/规范/政策/转化）
   - 按关键词检索
   - 按状态检索（生效/待审核/过期）
   
2. **检索结果展示**：
   - 规则卡片（ID、场景、维度、状态）
   - 相关性评分
   - 快速编辑入口

3. **高级筛选**：
   - 时间范围（创建时间、生效时间）
   - 来源类型（人工录入/自动提取/矫正生成）
   - 审批人筛选

**Streamlit组件**：
```python
st.text_input() - 搜索框
st.multiselect() - 多选筛选
st.date_input() - 时间范围
st.container() - 规则卡片
st.markdown() - 格式化显示
```

---

### 模块5：效果分析（新增页面）
**页面**：`pages/11_📊_效果分析.py`

**功能**：
1. **规则触发统计**：
   - 每条规则的触发次数
   - 触发率趋势图
   - 未触发规则列表（可能失效）
   
2. **评分改善分析**：
   - 规则生效前后的评分对比
   - A/B测试效果
   - 评分分布变化

3. **知识库覆盖率**：
   - 场景覆盖率（哪些场景缺少规则）
   - 维度覆盖率（哪些维度规则不足）
   - 时间趋势（覆盖率提升曲线）

**Streamlit组件**：
```python
st.line_chart() - 趋势图
st.bar_chart() - 对比图
st.area_chart() - 覆盖率曲线
st.dataframe() - 详细数据
st.progress() - 覆盖率进度条
```

---

### 模块6：版本管理（新增功能，嵌入各页面）

**功能**：
1. **版本历史**：
   - 每条规则的修改记录
   - 修改人、修改时间、修改内容
   
2. **版本对比**：
   - 并排显示两个版本的差异
   - 高亮修改部分
   
3. **版本回滚**：
   - 一键回滚到历史版本
   - 回滚前确认提示

**Streamlit组件**：
```python
st.selectbox() - 版本选择
st.columns() - 并排对比
st.markdown() - 差异高亮
st.button() - 回滚按钮
st.warning() - 确认提示
```

---

## 数据库表设计

### 新增表1：规则版本表
```sql
CREATE TABLE rule_versions (
    version_id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id TEXT NOT NULL,
    version_number INTEGER NOT NULL,
    rule_content TEXT NOT NULL,  -- JSON格式
    modified_by TEXT,
    modified_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    change_summary TEXT,  -- 修改摘要
    FOREIGN KEY (rule_id) REFERENCES rules(rule_id)
);
```

### 新增表2：规则审批记录表
```sql
CREATE TABLE rule_approvals (
    approval_id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id TEXT NOT NULL,
    action TEXT NOT NULL,  -- submit/approve/reject
    action_by TEXT,
    action_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    comment TEXT,  -- 审批意见
    previous_status TEXT,
    new_status TEXT,
    FOREIGN KEY (rule_id) REFERENCES rules(rule_id)
);
```

### 新增表3：规则效果追踪表
```sql
CREATE TABLE rule_effectiveness (
    tracking_id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id TEXT NOT NULL,
    session_id TEXT,
    triggered_at TIMESTAMP,
    score_before INTEGER,  -- 规则生效前评分
    score_after INTEGER,   -- 规则生效后评分
    improvement REAL,      -- 改善幅度
    FOREIGN KEY (rule_id) REFERENCES rules(rule_id)
);
```

---

## Streamlit前端适配要点

### 1. 保持现有风格
```python
# 沿用现有配色和布局
st.set_page_config(
    page_title="知识库",
    page_icon="🏠",
    layout="wide"
)

# 使用相同的侧边栏样式
with st.sidebar:
    st.header("📋 导航")
    # ...
```

### 2. 响应式布局
```python
# 适配不同屏幕尺寸
col1, col2, col3 = st.columns([1, 2, 1])

# 移动端优化
if st.session_state.get('mobile', False):
    st.markdown("""
    <style>
    .stButton>button {width: 100%;}
    </style>
    """, unsafe_allow_html=True)
```

### 3. 性能优化
```python
# 大数据量分页
@st.cache_data(ttl=300)
def load_rules(page=1, page_size=50):
    return get_rules_page(page, page_size)

# 异步加载
import asyncio
rules = asyncio.run(load_rules_async())
```

### 4. 交互体验
```python
# 操作反馈
st.toast("✅ 规则已保存", icon="✅")

# 确认对话框
if st.button("删除规则"):
    if st.confirm("确定删除？此操作不可撤销"):
        delete_rule(rule_id)

# 进度显示
with st.spinner("正在分析..."):
    result = analyze_rule(rule_id)
```

---

## 实施计划

### Phase 1：基础功能（2周）
1. 数据库表创建
2. 知识库首页
3. 规则录入页面
4. 版本管理功能

### Phase 2：核心功能（3周）
1. 规则审核优化
2. 知识检索页面
3. 审批流程完善
4. 效果追踪基础

### Phase 3：高级功能（4周）
1. 效果分析页面
2. A/B测试框架
3. 智能推荐
4. 冲突检测

---

## 技术风险

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| Streamlit性能瓶颈 | 大数据量页面卡顿 | 分页加载、缓存优化 |
| 数据库迁移复杂 | 数据丢失风险 | 备份、双写验证 |
| 版本管理存储膨胀 | 磁盘空间不足 | 定期归档、压缩历史 |
| 效果追踪数据量大 | 查询缓慢 | 分表、索引优化 |

---

## 金总，设计方案已完成

**核心特点**：
1. **6个功能模块**：首页、审核、录入、检索、分析、版本
2. **3个新增数据表**：版本表、审批表、效果追踪表
3. **Streamlit友好**：保持现有风格，响应式布局
4. **分阶段实施**：9周完成，风险可控

**是否开始Phase 1开发？**