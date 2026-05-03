#!/usr/bin/env python3.14
"""
客服聊天记录综合分析报告生成器
- 分组合并分析
- 知识图谱实体提取
- 四维度评分汇总
- 异常标记
"""
import sqlite3
import json
import os
import sys
from datetime import datetime

# 添加cs-analyzer到路径
sys.path.insert(0, '/Users/jinlu/.openclaw/workspace/skills/cs-analyzer')
from knowledge_graph import KnowledgeGraph, SessionExtractor

def load_sessions():
    """从数据库加载本次分析的4通会话"""
    db_path = "/Users/jinlu/.openclaw/workspace/skills/cs-analyzer/data/cs_analyzer_new.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT session_id, user_id, staff_name, messages, summary,
               professionalism_score, standardization_score, 
               policy_execution_score, conversion_score, total_score,
               analysis_json, strengths, issues, suggestions,
               session_count, related_sessions, start_time, end_time
        FROM sessions 
        WHERE session_id LIKE 'session_000%'
        ORDER BY created_at DESC
        LIMIT 4
    """)
    
    sessions = []
    for row in cursor.fetchall():
        sessions.append(dict(row))
    conn.close()
    return sessions

def extract_knowledge(sessions):
    """提取知识图谱实体"""
    kg = KnowledgeGraph()
    extractor = SessionExtractor(kg)
    
    knowledge_results = []
    
    for session in sessions:
        try:
            analysis = json.loads(session['analysis_json']) if session['analysis_json'] else {}
            messages = session['messages']
            if isinstance(messages, str):
                try:
                    messages = json.loads(messages)
                except:
                    messages = []
            
            session_data = {'messages': messages}
            result = extractor.extract_from_session(session_data, analysis)
            
            # 手动补充实体（因为regex可能漏掉）
            sa = analysis.get('session_analysis', {})
            theme = sa.get('theme', '')
            
            # 提取产品和场景关键词
            products_found = []
            scenes_found = []
            
            # 从主题和消息内容中提取
            all_text = theme + ' ' + session.get('summary', '')
            if session.get('strengths'):
                try:
                    strengths = json.loads(session['strengths'])
                    for s in strengths:
                        all_text += ' ' + s
                except:
                    pass
            
            # 产品关键词
            product_keywords = {
                '热水器': '燃气热水器',
                '16升': '16L热水器', '16L': '16L热水器',
                '13升': '13L热水器', '13L': '13L热水器',
                '20升': '20L热水器', '20L': '20L热水器',
                '24升': '24L热水器', '24L': '24L热水器',
                '烟管': '烟管配件', '打孔': '安装打孔',
                '预埋': '预埋烟管服务'
            }
            
            for kw, product in product_keywords.items():
                if kw in all_text or kw in str(messages):
                    if product not in products_found:
                        products_found.append(product)
            
            # 场景关键词
            scene_keywords = {
                '升数': '产品选型', '一厨一卫': '户型匹配', '一厨两卫': '户型匹配',
                '安装': '安装咨询', '打孔': '安装咨询', '预埋': '安装咨询',
                '尺寸': '安装尺寸', '气源': '安装条件',
                '延迟发货': '物流咨询', '提前发货': '物流咨询',
                '优惠券': '优惠咨询', '价格': '价格咨询'
            }
            
            for kw, scene in scene_keywords.items():
                if kw in all_text or kw in str(messages):
                    if scene not in scenes_found:
                        scenes_found.append(scene)
            
            # 从预分析获取场景
            pre = analysis.get('_metadata', {}).get('pre_analysis', {})
            scene_main = pre.get('scene', '未知')
            sub_scene = pre.get('sub_scene', '未知')
            intent = pre.get('intent', '未知')
            
            knowledge_results.append({
                'session_id': session['session_id'],
                'products': products_found,
                'scenes': scenes_found,
                'scene_main': scene_main,
                'sub_scene': sub_scene,
                'intent': intent,
                'kg_entities': result.get('entities', []),
                'kg_relations': result.get('relations', 0)
            })
        except Exception as e:
            knowledge_results.append({
                'session_id': session['session_id'],
                'error': str(e),
                'products': [],
                'scenes': [],
                'scene_main': '未知',
                'sub_scene': '未知',
                'intent': '未知'
            })
    
    kg.close()
    return knowledge_results

def group_sessions(sessions, knowledge):
    """按场景和主题分组合并"""
    # 构建分组
    groups = {
        '售前咨询-产品选型': [],
        '售前咨询-安装咨询': [],
        '售中服务-物流咨询': [],
        '售后阶段-其他': [],
        '未分组': []
    }
    
    for session, kg in zip(sessions, knowledge):
        scene = kg.get('scene_main', '未知')
        sub = kg.get('sub_scene', '未知')
        
        # 根据场景和主题智能分组
        if '安装' in str(session.get('summary', '')) or '打孔' in str(session.get('summary', '')) or '预埋' in str(session.get('summary', '')):
            groups['售前咨询-安装咨询'].append((session, kg))
        elif '升数' in str(session.get('summary', '')) or '一厨' in str(session.get('summary', '')) or '够' in str(session.get('summary', '')):
            groups['售前咨询-产品选型'].append((session, kg))
        elif '发货' in str(session.get('summary', '')) or '物流' in str(session.get('summary', '')):
            groups['售中服务-物流咨询'].append((session, kg))
        elif scene == '售前阶段':
            groups['售前咨询-产品选型'].append((session, kg))
        elif scene == '售中阶段':
            groups['售中服务-物流咨询'].append((session, kg))
        elif scene == '售后阶段':
            groups['售后阶段-其他'].append((session, kg))
        else:
            groups['未分组'].append((session, kg))
    
    # 清理空组
    return {k: v for k, v in groups.items() if v}

def generate_report(sessions, knowledge, groups):
    """生成综合分析报告"""
    report = []
    report.append("=" * 70)
    report.append("📊 客服聊天记录综合分析报告")
    report.append(f"   生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    report.append(f"   数据来源: 客服聊天记录(5).log")
    report.append(f"   总会话数: {len(sessions)}通 (已过滤超短会话1通)")
    report.append("=" * 70)
    
    # 一、每通会话评分
    report.append("\n📋 一、逐通会话评分详情（4维度）\n")
    
    for i, (session, kg) in enumerate(zip(sessions, knowledge), 1):
        sid = session['session_id']
        staff = session['staff_name']
        summary = session['summary'] or '无'
        
        p = session['professionalism_score'] or 0
        s = session['standardization_score'] or 0
        pol = session['policy_execution_score'] or 0
        c = session['conversion_score'] or 0
        total = session['total_score'] or 0
        
        # 风险等级
        if total <= 8:
            risk = "🔴 高风险"
        elif total <= 12:
            risk = "🟡 中风险"
        else:
            risk = "🟢 正常"
        
        report.append(f"  【会话{i}】{sid}")
        report.append(f"   客服: {staff}")
        report.append(f"   主题: {summary}")
        report.append(f"   专业性: {p}/5  |  标准化: {s}/5  |  政策执行: {pol}/5  |  转化能力: {c}/5")
        report.append(f"   总分: {total}/20  →  {risk}")
        
        # 异常标记
        issues = []
        if p <= 2: issues.append("专业性严重不足")
        if s <= 2: issues.append("标准化严重不足")
        if pol <= 2: issues.append("政策执行严重不足")
        if c <= 2: issues.append("转化能力严重不足")
        if total <= 12: issues.append("整体质量偏低")
        
        if issues:
            report.append(f"   ⚠️ 异常标记: {'; '.join(issues)}")
        else:
            report.append(f"   ✅ 无明显异常")
        
        # 优势与问题
        try:
            strengths = json.loads(session['strengths']) if session['strengths'] else []
            problems = json.loads(session['issues']) if session['issues'] else []
            if strengths:
                report.append(f"   💪 优势: {'; '.join(strengths)}")
            if problems:
                report.append(f"   ⚡ 问题: {'; '.join(problems)}")
        except:
            pass
        
        report.append("")
    
    # 二、知识提取结果
    report.append("\n🧠 二、知识图谱提取结果\n")
    
    all_products = set()
    all_scenes = set()
    
    for kg in knowledge:
        for p in kg.get('products', []):
            all_products.add(p)
        for s in kg.get('scenes', []):
            all_scenes.add(s)
    
    report.append(f"  📦 提取产品实体: {len(all_products)}个")
    for p in sorted(all_products):
        report.append(f"     • {p}")
    
    report.append(f"\n  🏠 提取场景实体: {len(all_scenes)}个")
    for s in sorted(all_scenes):
        report.append(f"     • {s}")
    
    # 每通会话的知识
    report.append(f"\n  📑 逐会话知识映射:\n")
    for i, kg in enumerate(knowledge, 1):
        report.append(f"     会话{i}: 场景={kg.get('scene_main', '未知')}/{kg.get('sub_scene', '未知')}")
        report.append(f"            意图={kg.get('intent', '未知')}")
        report.append(f"            产品={', '.join(kg.get('products', [])) or '无'}")
        report.append(f"            场景标签={', '.join(kg.get('scenes', [])) or '无'}")
        report.append("")
    
    # 三、分组统计
    report.append("\n📦 三、分组合并统计\n")
    
    for group_name, items in groups.items():
        if not items:
            continue
        
        report.append(f"  【{group_name}】({len(items)}通会话)")
        
        scores = [s['total_score'] or 0 for s, _ in items]
        avg_score = sum(scores) / len(scores) if scores else 0
        
        report.append(f"     平均总分: {avg_score:.1f}/20")
        
        # 组员列表
        for s, kg in items:
            risk_icon = "🟢" if (s['total_score'] or 0) >= 13 else ("🟡" if (s['total_score'] or 0) >= 9 else "🔴")
            report.append(f"     {risk_icon} {s['session_id']} | {s['staff_name']} | {s['total_score']}/20 | {s['summary'][:30]}...")
        
        # 组内共性
        common_issues = []
        for s, _ in items:
            try:
                problems = json.loads(s['issues']) if s['issues'] else []
                for p in problems:
                    if '礼貌' in p or '问候' in p or '结束语' in p:
                        if '礼貌用语' not in common_issues:
                            common_issues.append('礼貌用语缺失')
                    if '转化' in p or '销售' in p or '引导' in p:
                        if '转化引导不足' not in common_issues:
                            common_issues.append('转化引导不足')
                    if '格式' in p or '混乱' in p:
                        if '回复格式问题' not in common_issues:
                            common_issues.append('回复格式问题')
            except:
                pass
        
        if common_issues:
            report.append(f"     ⚠️ 共性短板: {'; '.join(common_issues)}")
        
        report.append("")
    
    # 四、异常标记汇总
    report.append("\n🚨 四、异常标记汇总\n")
    
    anomalies = []
    for i, (session, kg) in enumerate(zip(sessions, knowledge), 1):
        p = session['professionalism_score'] or 0
        s = session['standardization_score'] or 0
        pol = session['policy_execution_score'] or 0
        c = session['conversion_score'] or 0
        total = session['total_score'] or 0
        
        flags = []
        if p <= 3: flags.append(f"专业性{p}分")
        if s <= 3: flags.append(f"标准化{s}分")
        if pol <= 3: flags.append(f"政策执行{pol}分")
        if c <= 2: flags.append(f"转化能力{c}分")
        if total <= 12: flags.append(f"总分{total}分")
        
        # 特殊异常
        staff = session['staff_name']
        if 'jimi' in staff.lower():
            flags.append("机器人客服")
        
        if flags:
            anomalies.append({
                'session': i,
                'sid': session['session_id'],
                'staff': staff,
                'flags': flags,
                'total': total
            })
    
    if anomalies:
        report.append(f"  共发现 {len(anomalies)} 通会话存在异常标记:\n")
        for a in anomalies:
            report.append(f"     会话{a['session']} | {a['sid'][:20]}... | {a['staff']}")
            report.append(f"     异常项: {'; '.join(a['flags'])}")
            report.append("")
    else:
        report.append("  ✅ 所有会话均无异常标记")
    
    # 五、统计摘要
    report.append("\n📊 五、统计摘要\n")
    
    all_scores = [s['total_score'] or 0 for s in sessions]
    avg_total = sum(all_scores) / len(all_scores) if all_scores else 0
    min_total = min(all_scores) if all_scores else 0
    max_total = max(all_scores) if all_scores else 0
    
    p_scores = [s['professionalism_score'] or 0 for s in sessions]
    s_scores = [s['standardization_score'] or 0 for s in sessions]
    pol_scores = [s['policy_execution_score'] or 0 for s in sessions]
    c_scores = [s['conversion_score'] or 0 for s in sessions]
    
    report.append(f"  总分均值: {avg_total:.1f}/20 (范围: {min_total}-{max_total})")
    report.append(f"  专业性均值: {sum(p_scores)/len(p_scores):.1f}/5")
    report.append(f"  标准化均值: {sum(s_scores)/len(s_scores):.1f}/5")
    report.append(f"  政策执行均值: {sum(pol_scores)/len(pol_scores):.1f}/5")
    report.append(f"  转化能力均值: {sum(c_scores)/len(c_scores):.1f}/5")
    
    # 客服统计
    staff_stats = {}
    for s in sessions:
        staff = s['staff_name']
        if staff not in staff_stats:
            staff_stats[staff] = {'count': 0, 'total': 0}
        staff_stats[staff]['count'] += 1
        staff_stats[staff]['total'] += s['total_score'] or 0
    
    report.append(f"\n  客服统计:")
    for staff, stat in sorted(staff_stats.items(), key=lambda x: x[1]['total']/x[1]['count'], reverse=True):
        avg = stat['total'] / stat['count']
        report.append(f"     {staff}: {stat['count']}通 | 均分{avg:.1f}")
    
    report.append("\n" + "=" * 70)
    report.append("报告生成完毕")
    report.append("=" * 70)
    
    return '\n'.join(report)

def main():
    print("📂 加载会话数据...")
    sessions = load_sessions()
    print(f"   ✓ 加载 {len(sessions)} 通会话")
    
    print("\n🧠 提取知识图谱实体...")
    knowledge = extract_knowledge(sessions)
    print(f"   ✓ 知识提取完成")
    
    print("\n📦 执行分组合并...")
    groups = group_sessions(sessions, knowledge)
    print(f"   ✓ 分为 {len(groups)} 个组")
    
    print("\n📝 生成综合分析报告...")
    report = generate_report(sessions, knowledge, groups)
    
    # 保存报告
    report_path = "/Users/jinlu/.openclaw/workspace/skills/cs-analyzer/reports/客服聊天记录5_综合分析报告.txt"
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(report)
    
    print(f"\n✅ 报告已保存: {report_path}")
    print("\n" + report)

if __name__ == '__main__':
    main()
