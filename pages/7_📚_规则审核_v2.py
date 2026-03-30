"""规则审核页面 - CS-Analyzer v2

人工审核AI生成的规则草案，确认后同步到向量库。

页面位置: pages/6_📚_规则审核_v2.py
作者: 小虾米
更新: 2026-03-17
"""

import streamlit as st
import streamlit.components.v1 as components
import json
import sys
import os
import time

# 添加父目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from knowledge_base_v2 import (
    get_pending_rules, get_rule_by_id, approve_rule, reject_rule,
    generate_combined_text, sync_rule_to_vector_db, get_rules_stats,
    update_rule, delete_rule, get_rules_by_status
)

# 导入db_utils获取矫正记录关联的会话
from db_utils import get_correction_by_id

st.set_page_config(
    page_title="规则审核 v2",
    page_icon="📚",
    layout="wide"
)

# ========== 数据准备 ==========
# ========== 筛选器 ==========
filter_col1, filter_col2 = st.columns([1, 2])
with filter_col1:
    status_filter = st.selectbox(
        "状态筛选",
        ["待审核", "已确认", "已拒绝", "全部"],
        index=0,
        key="status_filter"
    )
with filter_col2:
    search_query = st.text_input("🔍 搜索规则", placeholder="输入关键词（规则ID、判定标准、场景）...", key="search_query")

# 状态映射
status_map = {
    "待审核": "pending",
    "已确认": "approved",
    "已拒绝": "rejected",
    "全部": "all"
}

# 获取规则列表和统计
rules_df = get_rules_by_status(
    status=status_map.get(status_filter, "pending"),
    search_query=search_query if search_query else None
)
stats = get_rules_stats()

if rules_df.empty:
    st.info("🎉 没有符合条件的规则")
    st.stop()

# 使用 session state 存储选中的规则
if 'selected_rule_id' not in st.session_state:
    st.session_state.selected_rule_id = rules_df.iloc[0]['rule_id']

# 维度中文映射
_dim_map = {
    'professionalism': '专业性',
    'standardization': '标准化',
    'policy_execution': '政策执行',
    'conversion': '转化能力'
}

# ========== 侧边栏：规则列表 ==========
with st.sidebar:
    st.header("📋 规则列表")
    st.markdown(f"<div style='font-size: 12px; color: #888; margin-bottom: 10px;'>共 {len(rules_df)} 条</div>", unsafe_allow_html=True)
    st.markdown('<style>.stButton>button {padding: 0.15rem 0.3rem !important; font-size: 11px !important; margin: 1px 0 !important; min-height: 28px !important;}</style>', unsafe_allow_html=True)
    
    for _, row in rules_df.iterrows():
        rule_id = row['rule_id']
        dim_display = _dim_map.get(row.get('rule_dimension', 'N/A'), row.get('rule_dimension', 'N/A'))
        is_selected = st.session_state.selected_rule_id == rule_id
        
        # 按钮显示规则ID和维度
        btn_text = f"#{rule_id[-8:]} {dim_display}"
        
        if st.button(btn_text, key=f"rule_btn_{rule_id}", use_container_width=True, type="primary" if is_selected else "secondary"):
            st.session_state.selected_rule_id = rule_id
            st.rerun()

# ========== 右侧顶部：规则库统计 ==========
st.markdown("### 📊 规则库统计")
stat_col1, stat_col2, stat_col3, stat_col4 = st.columns(4)

with stat_col1:
    st.metric("总规则", stats.get('total', 0))
with stat_col2:
    st.metric("待审核", stats.get('status_pending', 0))
with stat_col3:
    st.metric("已确认", stats.get('status_approved', 0))
with stat_col4:
    st.metric("已拒绝", stats.get('status_rejected', 0))

st.divider()

# ========== 主内容区：规则详情 ==========
# 获取当前选中的规则
selected_rule_id = st.session_state.selected_rule_id
selected_row = rules_df[rules_df['rule_id'] == selected_rule_id]

if selected_row.empty:
    st.error("❌ 选中的规则不存在")
    st.stop()

row = selected_row.iloc[0]
rule_id = row['rule_id']

# 加载完整规则
rule = get_rule_by_id(rule_id)

if not rule:
    st.error("⚠️ 无法加载规则详情")
    st.stop()

# 维度中文映射（详情页使用）
dim_map = {
    'professionalism': '专业性',
    'standardization': '标准化',
    'policy_execution': '政策执行',
    'conversion': '转化能力'
}

# 获取矫正记录信息用于跳转
correction_id = row.get('source_correction_id', 'N/A')
session_id_for_link = None
if correction_id and correction_id != 'N/A':
    try:
        corr_data = get_correction_by_id(int(correction_id))
        if corr_data is not None:
            session_id_for_link = corr_data.get('session_id', '')
    except:
        pass

# 标题行：规则草案 + 跳转按钮
title_col, btn_col = st.columns([3, 1])
with title_col:
    st.subheader(f"📋 规则草案 #{rule_id}")
with btn_col:
    if session_id_for_link:
        if st.button(f"📎 查看来源会话", key=f"goto_session_{rule_id}", use_container_width=True):
            st.session_state.jump_to_session = session_id_for_link
            st.switch_page("pages/6_🔍_会话分析与矫正.py")

# 信息列表（紧凑单行）
info_parts = [
    f"场景: {row.get('scene_category', 'N/A')} / {row.get('scene_sub_category', 'N/A')}",
    f"维度: {dim_map.get(row.get('rule_dimension', 'N/A'), row.get('rule_dimension', 'N/A'))}",
    f"创建: {row.get('created_at', 'N/A')[:10]}",
    "🟡 待审核"
]
info_line = " │ ".join(info_parts)
st.markdown(f"<div style='font-size: 14px; color: #666; margin-bottom: 10px;'>○ {info_line}</div>", unsafe_allow_html=True)

st.divider()

# === 场景信息（表单外，支持实时联动）===
st.markdown("**【场景信息】**")

scene_col1, scene_col2 = st.columns(2)
with scene_col1:
    scene_category = st.selectbox(
        "一级场景",
        ["售前咨询", "安装咨询", "客诉处理", "售后维修", "活动咨询"],
        index=["售前咨询", "安装咨询", "客诉处理", "售后维修", "活动咨询"].index(
            rule.get('scene_category', '客诉处理')
        ) if rule.get('scene_category') in ["售前咨询", "安装咨询", "客诉处理", "售后维修", "活动咨询"] else 2,
        key=f"scene_cat_{rule_id}"
    )

# 检测一级场景变化，触发页面刷新
original_cat_key = f"original_cat_{rule_id}"
if original_cat_key not in st.session_state:
    st.session_state[original_cat_key] = rule.get('scene_category', '客诉处理')

if scene_category != st.session_state[original_cat_key]:
    # 一级场景变化了，更新存储并刷新页面
    st.session_state[original_cat_key] = scene_category
    # 重置二级场景为默认值
    sub_options_default = {
        "售前咨询": "产品咨询",
        "安装咨询": "安装费用",
        "客诉处理": "情绪安抚",
        "售后维修": "故障排查",
        "活动咨询": "促销规则"
    }
    st.session_state[f"scene_sub_{rule_id}"] = sub_options_default.get(scene_category, "其他")
    st.rerun()

with scene_col2:
    # 二级场景根据一级动态
    sub_options = {
        "售前咨询": ["产品咨询", "价格咨询", "活动咨询", "对比咨询", "促销引导"],
        "安装咨询": ["安装费用", "预埋要求", "辅材清单", "尺寸图纸"],
        "客诉处理": ["情绪安抚", "退款协商", "投诉处理", "质疑回应"],
        "售后维修": ["故障排查", "维修预约", "保修政策", "退换货"],
        "活动咨询": ["促销规则", "政府补贴", "赠品政策", "保价规则"]
    }
    
    options = sub_options.get(scene_category, ["其他"])
    # 获取当前二级场景值
    current_sub = st.session_state.get(f"scene_sub_{rule_id}", rule.get('scene_sub_category', options[0]))
    # 确保当前值在选项列表中
    if current_sub not in options:
        options = [current_sub] + options
    
    scene_sub_category = st.selectbox(
        "二级场景",
        options,
        index=options.index(current_sub) if current_sub in options else 0,
        key=f"scene_sub_{rule_id}"
    )

scene_description = st.text_area(
    "场景描述（用于向量化）",
    value=rule.get('scene_description', ''),
    key=f"scene_desc_{rule_id}"
)

st.divider()

# === 触发条件（移到有效期设置上面）===
st.markdown("**【触发条件】**")

# Tooltip 样式（与会话分析页面一致）
st.markdown('''
<style>
.tooltip {
    position: relative;
    display: inline-block;
    cursor: help;
}
.tooltip .tooltiptext {
    visibility: hidden;
    width: 280px;
    background-color: rgba(0, 0, 0, 0.9);
    color: #fff;
    text-align: left;
    padding: 10px 14px;
    border-radius: 8px;
    font-size: 13px;
    line-height: 1.6;
    position: absolute;
    z-index: 1000;
    top: 125%;
    left: 50%;
    margin-left: -140px;
    opacity: 0;
    transition: opacity 0.3s;
    box-shadow: 0 4px 12px rgba(0,0,0,0.4);
}
.tooltip:hover .tooltiptext {
    visibility: visible;
    opacity: 1;
}
.tooltip-icon {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 14px;
    height: 14px;
    background-color: #666;
    color: white;
    border-radius: 50%;
    font-size: 10px;
    margin-left: 4px;
    vertical-align: middle;
}
</style>
''', unsafe_allow_html=True)

trigger_col1, trigger_col2, trigger_col3 = st.columns(3)

with trigger_col1:
    st.markdown(
        '关键词（逗号分隔）'
        '<span class="tooltip">'
        '<span class="tooltip-icon">?</span>'
        '<span class="tooltiptext">用于精确匹配用户消息中的关键词<br>示例：预埋,烟管,安装费用<br>多个关键词用逗号分隔，满足任意一个即可触发</span>'
        '</span>',
        unsafe_allow_html=True
    )
    keywords_str = st.text_input(
        "",
        value=','.join(rule.get('trigger_keywords', [])),
        key=f"keywords_{rule_id}",
        label_visibility="collapsed"
    )

with trigger_col2:
    st.markdown(
        '意图'
        '<span class="tooltip">'
        '<span class="tooltip-icon">?</span>'
        '<span class="tooltiptext">用户的核心目的或需求类型<br>• 咨询：了解产品/服务信息<br>• 客诉：表达不满或投诉<br>• 退款：要求退货退款<br>• 维修：报修或预约维修<br>• 比价：与其他品牌对比<br>• 安装：询问安装相关</span>'
        '</span>',
        unsafe_allow_html=True
    )
    intent = st.selectbox(
        "",
        ["咨询", "客诉", "退款", "维修", "比价", "安装"],
        index=["咨询", "客诉", "退款", "维修", "比价", "安装"].index(
            rule.get('trigger_intent', '客诉')
        ) if rule.get('trigger_intent') in ["咨询", "客诉", "退款", "维修", "比价", "安装"] else 1,
        key=f"intent_{rule_id}",
        label_visibility="collapsed"
    )

with trigger_col3:
    st.markdown(
        '情绪'
        '<span class="tooltip">'
        '<span class="tooltip-icon">?</span>'
        '<span class="tooltiptext">用户的情绪状态<br>• positive：积极友好，满意度高<br>• neutral：平和正常沟通<br>• negative：消极不满，有抱怨<br>• urgent：紧急焦虑，需要立即处理</span>'
        '</span>',
        unsafe_allow_html=True
    )
    mood = st.selectbox(
        "",
        ["positive", "neutral", "negative", "urgent"],
        index=["positive", "neutral", "negative", "urgent"].index(
            rule.get('trigger_mood', 'negative')
        ) if rule.get('trigger_mood') in ["positive", "neutral", "negative", "urgent"] else 2,
        key=f"mood_{rule_id}",
        label_visibility="collapsed"
    )

st.divider()

# === 有效期设置 ===
st.markdown(
    '**有效期设置**'
    '<span class="tooltip">'
    '<span class="tooltip-icon">?</span>'
    '<span class="tooltiptext">规则的有效期设置<br>• 长期有效：永久适用（如产品参数、服务规范）<br>• 限时有效：指定起止时间（如促销活动、临时政策）<br>• 到期后规则自动失效，不再参与评分</span>'
    '</span>',
    unsafe_allow_html=True
)

valid_type = st.selectbox(
    "",
    ["长期有效", "限时有效"],
    key=f"valid_type_{rule_id}",
    label_visibility="collapsed"
)

# 限时有效时显示起止时间
if valid_type == "限时有效":
    time_col1, time_col2 = st.columns(2)
    with time_col1:
        st.caption("生效时间")
        valid_from_col1, valid_from_col2 = st.columns([2, 1])
        with valid_from_col1:
            valid_from_date = st.date_input(
                "日期",
                value=None,
                key=f"valid_from_date_{rule_id}",
                label_visibility="collapsed"
            )
        with valid_from_col2:
            valid_from_time = st.time_input(
                "时间",
                value=None,
                key=f"valid_from_time_{rule_id}",
                label_visibility="collapsed"
            )
    
    with time_col2:
        st.caption("失效时间")
        valid_to_col1, valid_to_col2 = st.columns([2, 1])
        with valid_to_col1:
            valid_to_date = st.date_input(
                "日期",
                key=f"valid_to_date_{rule_id}",
                label_visibility="collapsed"
            )
        with valid_to_col2:
            valid_to_time = st.time_input(
                "时间",
                key=f"valid_to_time_{rule_id}",
                label_visibility="collapsed"
            )
    
    st.divider()

# 可编辑表单（评分规则、来源案例、标签等）
with st.form(key=f"form_{rule_id}"):
    
    # === 评分规则（表格布局）===
    st.markdown("**【评分规则】**")
    
    # 左侧：评分维度 + 核心判定标准
    score_left, score_right = st.columns([1, 1])
    
    with score_left:
        st.markdown("**评分维度**")
        dimension = st.selectbox(
            "",
            ["professionalism", "standardization", "policy_execution", "conversion"],
            format_func=lambda x: {
                "professionalism": "专业性",
                "standardization": "标准化",
                "policy_execution": "政策执行",
                "conversion": "转化能力"
            }.get(x, x),
            index=["professionalism", "standardization", "policy_execution", "conversion"].index(
                rule.get('rule_dimension', 'standardization')
            ) if rule.get('rule_dimension') in ["professionalism", "standardization", "policy_execution", "conversion"] else 1,
            key=f"dimension_{rule_id}",
            label_visibility="collapsed"
        )
        
        st.markdown("**核心判定标准**")
        criteria = st.text_area(
            "",
            value=rule.get('rule_criteria', ''),
            key=f"criteria_{rule_id}",
            height=120,
            label_visibility="collapsed"
        )
    
    with score_right:
        st.markdown("**评分标准**")
        
        with st.expander("【5分标准-优秀】", expanded=False):
            st.caption("📋 描述")
            score_5_desc = st.text_area(
                "",
                value=rule.get('rule_score_guide', {}).get('5', {}).get('description', ''),
                key=f"score_5_desc_{rule_id}",
                height=60,
                label_visibility="collapsed"
            )
            st.caption("✅ 检查点（每行一个）")
            score_5_checks = st.text_area(
                "",
                value='\n'.join(rule.get('rule_score_guide', {}).get('5', {}).get('checkpoints', [])),
                key=f"score_5_checks_{rule_id}",
                height=60,
                label_visibility="collapsed"
            )
        
        with st.expander("【3分标准-及格】", expanded=False):
            st.caption("📋 描述")
            score_3_desc = st.text_area(
                "",
                value=rule.get('rule_score_guide', {}).get('3', {}).get('description', ''),
                key=f"score_3_desc_{rule_id}",
                height=60,
                label_visibility="collapsed"
            )
            st.caption("✅ 检查点（每行一个）")
            score_3_checks = st.text_area(
                "",
                value='\n'.join(rule.get('rule_score_guide', {}).get('3', {}).get('checkpoints', [])),
                key=f"score_3_checks_{rule_id}",
                height=60,
                label_visibility="collapsed"
            )
        
        with st.expander("【1分标准-不合格】", expanded=False):
            st.caption("📋 描述")
            score_1_desc = st.text_area(
                "",
                value=rule.get('rule_score_guide', {}).get('1', {}).get('description', ''),
                key=f"score_1_desc_{rule_id}",
                height=60,
                label_visibility="collapsed"
            )
            st.caption("✅ 检查点（每行一个）")
            score_1_checks = st.text_area(
                "",
                value='\n'.join(rule.get('rule_score_guide', {}).get('1', {}).get('checkpoints', [])),
                key=f"score_1_checks_{rule_id}",
                height=60,
                label_visibility="collapsed"
            )
    
    st.divider()
    
    # === 来源案例 ===
    st.markdown(
        '**【来源案例】**'
        '<span class="tooltip">'
        '<span class="tooltip-icon">?</span>'
        '<span class="tooltiptext">从矫正记录中提取的原始对话片段<br>用于追溯规则产生的背景和依据<br>• 展示关键对话轮次<br>• 显示AI原始评分和人工矫正评分<br>• 仅作参考，不可编辑</span>'
        '</span>',
        unsafe_allow_html=True
    )
    
    examples = rule.get('examples', [])
    if examples:
        for ex in examples:
            st.text_area(
                f"案例 ({ex.get('type', 'unknown')})",
                value=ex.get('dialogue_snippet', ''),
                disabled=True,
                key=f"example_{ex.get('case_id', '')}_{rule_id}"
            )
            st.caption(f"AI原判: {ex.get('ai_score_before', 'N/A')}分 → 人工矫正: {ex.get('human_corrected_score', 'N/A')}分")
    else:
        st.info("📭 无来源案例")
    
    st.divider()
    
    # === 标签 ===
    st.markdown(
        '**【标签】**'
        '<span class="tooltip">'
        '<span class="tooltip-icon">?</span>'
        '<span class="tooltiptext">用于规则分类和检索的关键词<br>多个标签用逗号分隔<br><br>示例：<br>• 产品型号：GD31, GD32<br>• 业务类型：国补, 以旧换新<br>• 场景标签：紧急, 高频问题<br>• 季节标签：双11, 618</span>'
        '</span>',
        unsafe_allow_html=True
    )
    
    tags_str = st.text_input(
        "标签（逗号分隔）",
        value=','.join(rule.get('tags', [])),
        key=f"tags_{rule_id}"
    )
    
    st.divider()
    
    # === 操作按钮 ===
    btn_col1, btn_col2, btn_col3, btn_col4 = st.columns(4)
    
    with btn_col1:
        submit_pass = st.form_submit_button("✅ 通过并生效", use_container_width=True, type="primary")
    
    with btn_col2:
        submit_edit = st.form_submit_button("✏️ 保存编辑", use_container_width=True)
    
    with btn_col3:
        submit_reject = st.form_submit_button("❌ 拒绝", use_container_width=True)
    
    with btn_col4:
        submit_delete = st.form_submit_button("🗑️ 删除", use_container_width=True)
    
    # 处理提交 - 从session_state读取表单外的场景信息
    # 获取场景信息（表单外）
    scene_category_val = st.session_state.get(f"scene_cat_{rule_id}", rule.get('scene_category', '客诉处理'))
    scene_sub_category_val = st.session_state.get(f"scene_sub_{rule_id}", rule.get('scene_sub_category', '其他'))
    scene_description_val = st.session_state.get(f"scene_desc_{rule_id}", rule.get('scene_description', ''))
    
    if submit_pass:
        with st.spinner("⏳ 正在确认规则并同步到向量库，请稍候..."):
            # 通过并生效
            if approve_rule(rule_id, 'admin'):
                # 同步到向量库
                if sync_rule_to_vector_db(rule_id):
                    st.success(f"✅ 规则 {rule_id} 已确认并同步到向量库")
                    time.sleep(0.5)  # 给用户看到成功消息的时间
                    st.rerun()
                else:
                    st.warning("⚠️ 规则已确认，但同步到向量库失败")
            else:
                st.error("❌ 规则确认失败")
    
    elif submit_edit:
        # 保存编辑功能
        updates = {
            'scene_category': scene_category_val,
            'scene_sub_category': scene_sub_category_val,
            'scene_description': scene_description_val,
            'trigger_keywords': [k.strip() for k in keywords_str.split(',') if k.strip()],
            'trigger_intent': intent,
            'trigger_mood': mood,
            'rule_dimension': dimension,
            'rule_criteria': criteria,
            'tags': [t.strip() for t in tags_str.split(',') if t.strip()]
        }
        
        # 处理有效期
        if valid_type == "限时有效":
            updates['trigger_valid_from'] = valid_from_date.isoformat() if valid_from_date else None
            updates['trigger_valid_to'] = valid_to_date.isoformat() if valid_to_date else None
        else:
            updates['trigger_valid_from'] = None
            updates['trigger_valid_to'] = None
        
        # 处理评分标准
        score_guide = {
            '5': {
                'description': score_5_desc,
                'checkpoints': [c.strip() for c in score_5_checks.split('\n') if c.strip()]
            },
            '3': {
                'description': score_3_desc,
                'checkpoints': [c.strip() for c in score_3_checks.split('\n') if c.strip()]
            },
            '1': {
                'description': score_1_desc,
                'checkpoints': [c.strip() for c in score_1_checks.split('\n') if c.strip()]
            }
        }
        updates['rule_score_guide'] = score_guide
        
        # 更新数据库
        with st.spinner("⏳ 正在保存规则修改，请稍候..."):
            if update_rule(rule_id, updates):
                st.success(f"✅ 规则 {rule_id} 已保存")
                time.sleep(0.3)
                st.rerun()
            else:
                st.error("❌ 保存失败")
    
    elif submit_delete:
        # 删除功能
        with st.spinner("⏳ 正在删除规则，请稍候..."):
            if delete_rule(rule_id):
                st.success(f"🗑️ 规则 {rule_id} 已删除")
                if 'selected_rule_id' in st.session_state:
                    del st.session_state['selected_rule_id']
                time.sleep(0.3)
                st.rerun()
            else:
                st.error("❌ 删除失败")
    
    elif submit_reject:
        # 拒绝功能
        with st.spinner("⏳ 正在拒绝规则，请稍候..."):
            if reject_rule(rule_id):
                st.success(f"❌ 规则 {rule_id} 已拒绝")
                time.sleep(0.3)
                st.rerun()
            else:
                st.error("❌ 拒绝操作失败")

# 底部说明
st.markdown("""
---
**使用说明**:
1. AI从矫正记录自动提取规则草案
2. 在此页面审核规则内容，可编辑字段
3. 点击「通过并生效」后，规则会同步到向量库
4. 后续评分时会检索已确认的规则辅助判定
""")
