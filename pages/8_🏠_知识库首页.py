"""知识库首页 - CS-Analyzer v2

知识库总览、快捷入口、健康度监控

页面位置: pages/8_🏠_知识库首页.py
作者: 小虾米
更新: 2026-04-27
"""

import streamlit as st
import sys
import os

# 添加父目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from knowledge_base_v2 import get_rules_stats
from db_utils import get_connection

st.set_page_config(
    page_title="知识库首页",
    page_icon="🏠",
    layout="wide"
)

# ========== 样式配置 ==========
st.markdown("""
<style>
.metric-card {
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    padding: 20px;
    border-radius: 10px;
    color: white;
    text-align: center;
}
.metric-value {
    font-size: 36px;
    font-weight: bold;
    margin: 10px 0;
}
.metric-label {
    font-size: 14px;
    opacity: 0.9;
}
.quick-entry {
    background: #f0f2f6;
    padding: 20px;
    border-radius: 10px;
    text-align: center;
    cursor: pointer;
    transition: all 0.3s;
}
.quick-entry:hover {
    background: #e0e2e6;
    transform: translateY(-2px);
}
.health-indicator {
    display: inline-block;
    width: 12px;
    height: 12px;
    border-radius: 50%;
    margin-right: 8px;
}
.health-good { background: #28a745; }
.health-warning { background: #ffc107; }
.health-danger { background: #dc3545; }
</style>
""", unsafe_allow_html=True)

# ========== 页面标题 ==========
st.title("🏠 知识库首页")
st.markdown("---")

# ========== 数据加载 ==========
stats = get_rules_stats()

# 获取更详细的统计
conn = get_connection()
cursor = conn.cursor()

# 今日新增（合并rules和rule_drafts表）
cursor.execute("""
    SELECT COUNT(*) FROM rules 
    WHERE DATE(created_at) = DATE('now')
""")
today_new_rules = cursor.fetchone()[0]

cursor.execute("""
    SELECT COUNT(*) FROM rule_drafts 
    WHERE DATE(created_at) = DATE('now')
""")
today_new_drafts = cursor.fetchone()[0]
today_new = today_new_rules + today_new_drafts

# 本周新增（合并rules和rule_drafts表）
cursor.execute("""
    SELECT COUNT(*) FROM rules 
    WHERE created_at >= DATE('now', '-7 days')
""")
week_new_rules = cursor.fetchone()[0]

cursor.execute("""
    SELECT COUNT(*) FROM rule_drafts 
    WHERE created_at >= DATE('now', '-7 days')
""")
week_new_drafts = cursor.fetchone()[0]
week_new = week_new_rules + week_new_drafts

# 过期规则（只统计rules表）
cursor.execute("""
    SELECT COUNT(*) FROM rules 
    WHERE status = 'approved' 
    AND trigger_valid_to IS NOT NULL 
    AND trigger_valid_to < DATETIME('now')
""")
expired_rules = cursor.fetchone()[0]

# 场景覆盖情况（合并两个表）
cursor.execute("""
    SELECT scene_category, COUNT(*) as count 
    FROM rules 
    WHERE status = 'approved'
    GROUP BY scene_category
""")
scene_coverage = cursor.fetchall()

conn.close()

# ========== 统计卡片 ==========
st.markdown("### 📊 知识库概览")

stat_col1, stat_col2, stat_col3, stat_col4, stat_col5 = st.columns(5)

with stat_col1:
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-label">总规则数</div>
        <div class="metric-value">{stats.get('total', 0)}</div>
    </div>
    """, unsafe_allow_html=True)

with stat_col2:
    st.markdown(f"""
    <div class="metric-card" style="background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%);">
        <div class="metric-label">待审核</div>
        <div class="metric-value">{stats.get('status_pending', 0)}</div>
    </div>
    """, unsafe_allow_html=True)

with stat_col3:
    st.markdown(f"""
    <div class="metric-card" style="background: linear-gradient(135deg, #4facfe 0%, #00f2fe 100%);">
        <div class="metric-label">已生效</div>
        <div class="metric-value">{stats.get('status_approved', 0)}</div>
    </div>
    """, unsafe_allow_html=True)

with stat_col4:
    st.markdown(f"""
    <div class="metric-card" style="background: linear-gradient(135deg, #43e97b 0%, #38f9d7 100%);">
        <div class="metric-label">今日新增</div>
        <div class="metric-value">{today_new}</div>
    </div>
    """, unsafe_allow_html=True)

with stat_col5:
    st.markdown(f"""
    <div class="metric-card" style="background: linear-gradient(135deg, #fa709a 0%, #fee140 100%);">
        <div class="metric-label">本周新增</div>
        <div class="metric-value">{week_new}</div>
    </div>
    """, unsafe_allow_html=True)

st.markdown("---")

# ========== 快捷入口 ==========
st.markdown("### 🚀 快捷入口")

entry_col1, entry_col2, entry_col3, entry_col4 = st.columns(4)

with entry_col1:
    if st.button("📚 规则审核", use_container_width=True):
        st.switch_page("pages/7_📚_规则审核_v2.py")
    st.markdown("""
    <div style="text-align: center; color: #666; font-size: 12px;">
        审核待确认的规则
    </div>
    """, unsafe_allow_html=True)

with entry_col2:
    if st.button("📝 规则录入", use_container_width=True):
        st.info("规则录入页面开发中...")
    st.markdown("""
    <div style="text-align: center; color: #666; font-size: 12px;">
        手动录入新规则
    </div>
    """, unsafe_allow_html=True)

with entry_col3:
    if st.button("🔍 知识检索", use_container_width=True):
        st.info("知识检索页面开发中...")
    st.markdown("""
    <div style="text-align: center; color: #666; font-size: 12px;">
        检索已有规则
    </div>
    """, unsafe_allow_html=True)

with entry_col4:
    if st.button("📊 效果分析", use_container_width=True):
        st.info("效果分析页面开发中...")
    st.markdown("""
    <div style="text-align: center; color: #666; font-size: 12px;">
        分析规则效果
    </div>
    """, unsafe_allow_html=True)

st.markdown("---")

# ========== 知识库健康度 ==========
st.markdown("### 🏥 知识库健康度")

health_col1, health_col2 = st.columns(2)

with health_col1:
    st.markdown("#### 场景覆盖率")
    
    if scene_coverage:
        for scene, count in scene_coverage:
            # 计算覆盖率（假设每个场景至少需要5条规则）
            coverage = min(count / 5 * 100, 100)
            
            if coverage >= 80:
                indicator = "health-good"
                status = "良好"
            elif coverage >= 50:
                indicator = "health-warning"
                status = "一般"
            else:
                indicator = "health-danger"
                status = "不足"
            
            st.markdown(f"""
            <div style="margin: 10px 0;">
                <span class="health-indicator {indicator}"></span>
                <strong>{scene}</strong>: {count}条规则 ({status})
                <div style="margin-left: 20px;">
                    {st.progress(coverage/100)}
                </div>
            </div>
            """, unsafe_allow_html=True)
    else:
        st.info("暂无场景数据")

with health_col2:
    st.markdown("#### 风险提示")
    
    # 过期规则
    if expired_rules > 0:
        st.warning(f"⚠️ 有 {expired_rules} 条规则已过期，请及时更新")
    else:
        st.success("✅ 无过期规则")
    
    # 待审核规则
    pending_count = stats.get('status_pending', 0)
    if pending_count > 10:
        st.warning(f"⚠️ 有 {pending_count} 条规则待审核，请及时处理")
    elif pending_count > 0:
        st.info(f"ℹ️ 有 {pending_count} 条规则待审核")
    else:
        st.success("✅ 无待审核规则")
    
    # 规则冲突（简化检查：同场景同维度的规则数量）
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT scene_category, rule_dimension, COUNT(*) as count
        FROM rules
        WHERE status = 'approved'
        GROUP BY scene_category, rule_dimension
        HAVING count > 5
    """)
    potential_conflicts = cursor.fetchall()
    conn.close()
    
    if potential_conflicts:
        st.warning(f"⚠️ 发现 {len(potential_conflicts)} 个潜在冲突点（同场景同维度规则过多）")
    else:
        st.success("✅ 无明显规则冲突")

st.markdown("---")

# ========== 最近动态 ==========
st.markdown("### 📈 最近动态")

conn = get_connection()
cursor = conn.cursor()

# 最近7天的规则操作记录
cursor.execute("""
    SELECT 
        '新增规则' as action,
        rule_id,
        created_at as time,
        status
    FROM rules
    WHERE created_at >= DATE('now', '-7 days')
    
    UNION ALL
    
    SELECT 
        '规则修改' as action,
        rule_id,
        updated_at as time,
        status
    FROM rules
    WHERE updated_at >= DATE('now', '-7 days')
    AND updated_at != created_at
    
    ORDER BY time DESC
    LIMIT 10
""")

recent_activities = cursor.fetchall()
conn.close()

if recent_activities:
    for action, rule_id, time, status in recent_activities:
        st.markdown(f"""
        <div style="padding: 8px; border-left: 3px solid #667eea; margin: 5px 0; background: #f8f9fa;">
            <strong>{action}</strong>: {rule_id[-8:]} 
            <span style="color: #666; font-size: 12px;">({time})</span>
            <span style="color: {'#28a745' if status == 'approved' else '#ffc107' if status == 'pending' else '#dc3545'}; font-size: 12px;">
                {status}
            </span>
        </div>
        """, unsafe_allow_html=True)
else:
    st.info("最近7天无规则操作记录")

# ========== 页脚 ==========
st.markdown("---")
st.markdown("""
<div style="text-align: center; color: #666; font-size: 12px;">
    CS-Analyzer 知识库系统 | 版本 2.0 | 更新于 2026-04-27
</div>
""", unsafe_allow_html=True)