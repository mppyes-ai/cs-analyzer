"""
客服会话分析系统 v2 - 主页

页面导航:
- 系统概览
- 个人表现（会话列表）
- 矫正中心
- 规则审核（v2）
"""
import streamlit as st

st.set_page_config(
    page_title="客服会话分析系统",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.title("🤖 客服会话分析系统")

st.markdown("""
## 欢迎使用

本系统用于分析客服会话质量，支持：

### 📊 核心功能

| 功能 | 说明 |
|------|------|
| **会话分析** | 查看AI自动分析的4维度评分（含CoT判定过程） |
| **矫正中心** | 对AI评分进行人工矫正 |
| **规则提取** | 从矫正记录自动提取结构化规则 |
| **规则审核** | 审核、确认提取的结构化规则（v2） |

### 🔄 工作流程

```
会话数据 → AI分析（含规则检索）→ 人工矫正 → 规则提取（AI生成JSON草案）
  → 规则审核（人工确认）→ 向量库同步 → 投入新会话评分
```

### 📈 当前状态
""")

# 显示统计
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from db_utils import get_correction_stats
from knowledge_base_v2 import get_rules_stats

col1, col2, col3 = st.columns(3)

with col1:
    st.metric("矫正记录", "查看矫正中心")
    try:
        stats = get_correction_stats()
        st.caption(f"总计: {stats.get('total', 0)} | 待处理: {stats.get('pending', 0)}")
    except:
        st.caption("数据加载中...")

with col2:
    st.metric("规则库（v2）", "查看规则审核")
    try:
        stats = get_rules_stats()
        total = stats.get('total', 0)
        pending = stats.get('status_pending', 0)
        approved = stats.get('status_approved', 0)
        st.caption(f"总计: {total} | 待审核: {pending} | 已确认: {approved}")
    except:
        st.caption("规则库初始化中...")

with col3:
    st.metric("操作指引", "👈 点击左侧导航")
    st.caption("矫正中心 → 规则审核")

st.divider()

st.markdown("""
### 🚀 快速开始

1. **查看会话分析** → 点击左侧「个人表现」或「会话明细_v2」
2. **提交矫正** → 进入「矫正中心」，选择会话修改评分
3. **提取规则** → 命令行运行 `python3 rule_extractor_v2.py process <correction_id>`
4. **审核规则** → 进入「规则审核_v2」确认提取的结构化规则
""")