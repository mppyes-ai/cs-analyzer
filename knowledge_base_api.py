"""知识库API模块 - CS-Analyzer v2

后端API支持：版本管理、审批流程、效果追踪

文件位置: knowledge_base_api.py
作者: 小虾米
更新: 2026-04-27
"""

import sqlite3
import json
import os
from datetime import datetime
from typing import List, Dict, Optional, Tuple

# 数据库路径
DB_PATH = os.path.join(os.path.dirname(__file__), "data", "cs_analyzer_new.db")

def get_connection():
    """获取数据库连接"""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn

# ========== 版本管理API ==========

def save_rule_version(rule_id: str, rule_content: Dict, modified_by: str = "system", change_summary: str = "") -> bool:
    """保存规则版本
    
    Args:
        rule_id: 规则ID
        rule_content: 规则内容（字典）
        modified_by: 修改人
        change_summary: 修改摘要
        
    Returns:
        是否成功
    """
    try:
        conn = get_connection()
        cursor = conn.cursor()
        
        # 获取当前最大版本号
        cursor.execute("""
            SELECT MAX(version_number) FROM rule_versions WHERE rule_id = ?
        """, (rule_id,))
        
        result = cursor.fetchone()
        max_version = result[0] if result[0] else 0
        new_version = max_version + 1
        
        # 插入新版本
        cursor.execute("""
            INSERT INTO rule_versions (rule_id, version_number, rule_content, modified_by, change_summary)
            VALUES (?, ?, ?, ?, ?)
        """, (rule_id, new_version, json.dumps(rule_content, ensure_ascii=False), modified_by, change_summary))
        
        conn.commit()
        conn.close()
        
        return True
    except Exception as e:
        print(f"保存规则版本失败: {e}")
        return False


def get_rule_versions(rule_id: str) -> List[Dict]:
    """获取规则的所有版本
    
    Args:
        rule_id: 规则ID
        
    Returns:
        版本列表
    """
    try:
        conn = get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT version_number, modified_by, modified_at, change_summary, rule_content
            FROM rule_versions
            WHERE rule_id = ?
            ORDER BY version_number DESC
        """, (rule_id,))
        
        results = cursor.fetchall()
        conn.close()
        
        versions = []
        for row in results:
            versions.append({
                "version_number": row[0],
                "modified_by": row[1],
                "modified_at": row[2],
                "change_summary": row[3],
                "rule_content": json.loads(row[4]) if row[4] else {}
            })
        
        return versions
    except Exception as e:
        print(f"获取规则版本失败: {e}")
        return []


def get_rule_version(rule_id: str, version_number: int) -> Optional[Dict]:
    """获取指定版本的规则
    
    Args:
        rule_id: 规则ID
        version_number: 版本号
        
    Returns:
        规则内容
    """
    try:
        conn = get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT rule_content FROM rule_versions
            WHERE rule_id = ? AND version_number = ?
        """, (rule_id, version_number))
        
        result = cursor.fetchone()
        conn.close()
        
        if result:
            return json.loads(result[0]) if result[0] else {}
        return None
    except Exception as e:
        print(f"获取规则版本失败: {e}")
        return None


def rollback_rule_version(rule_id: str, version_number: int) -> bool:
    """回滚到指定版本
    
    Args:
        rule_id: 规则ID
        version_number: 目标版本号
        
    Returns:
        是否成功
    """
    try:
        # 获取目标版本内容
        target_version = get_rule_version(rule_id, version_number)
        if not target_version:
            return False
        
        # 保存当前版本（作为新版本）
        conn = get_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM rules WHERE rule_id = ?", (rule_id,))
        current_rule = cursor.fetchone()
        
        if current_rule:
            # 获取列名
            columns = [description[0] for description in cursor.description]
            current_dict = dict(zip(columns, current_rule))
            
            save_rule_version(
                rule_id, 
                current_dict, 
                "system", 
                f"回滚前保存（回滚到版本{version_number}）"
            )
        
        # 更新规则到目标版本
        cursor.execute("""
            UPDATE rules SET
                scene_category = ?,
                scene_sub_category = ?,
                trigger_keywords = ?,
                trigger_intent = ?,
                trigger_mood = ?,
                rule_dimension = ?,
                rule_criteria = ?,
                rule_score_guide = ?,
                rule_weight_adjustment = ?,
                updated_at = ?
            WHERE rule_id = ?
        """, (
            target_version.get('scene_category'),
            target_version.get('scene_sub_category'),
            json.dumps(target_version.get('trigger_keywords', []), ensure_ascii=False),
            target_version.get('trigger_intent'),
            target_version.get('trigger_mood'),
            target_version.get('rule_dimension'),
            target_version.get('rule_criteria'),
            json.dumps(target_version.get('rule_score_guide', {}), ensure_ascii=False),
            target_version.get('rule_weight_adjustment'),
            datetime.now().isoformat(),
            rule_id
        ))
        
        conn.commit()
        conn.close()
        
        # 记录回滚操作
        save_rule_version(
            rule_id,
            target_version,
            "system",
            f"回滚到版本{version_number}"
        )
        
        return True
    except Exception as e:
        print(f"回滚规则版本失败: {e}")
        return False


# ========== 审批流程API ==========

def submit_rule_for_approval(rule_id: str, submitted_by: str = "system", comment: str = "") -> bool:
    """提交规则进行审批
    
    Args:
        rule_id: 规则ID
        submitted_by: 提交人
        comment: 提交说明
        
    Returns:
        是否成功
    """
    try:
        conn = get_connection()
        cursor = conn.cursor()
        
        # 更新规则状态
        cursor.execute("""
            UPDATE rules SET status = 'pending', updated_at = ?
            WHERE rule_id = ?
        """, (datetime.now().isoformat(), rule_id))
        
        # 记录审批流程
        cursor.execute("""
            INSERT INTO rule_approvals (rule_id, action, action_by, comment, previous_status, new_status)
            VALUES (?, 'submit', ?, ?, 'draft', 'pending')
        """, (rule_id, submitted_by, comment))
        
        conn.commit()
        conn.close()
        
        return True
    except Exception as e:
        print(f"提交规则审批失败: {e}")
        return False


def approve_rule_v2(rule_id: str, approved_by: str = "admin", comment: str = "") -> bool:
    """批准规则（v2版本，带审批记录）
    
    Args:
        rule_id: 规则ID
        approved_by: 审批人
        comment: 审批意见
        
    Returns:
        是否成功
    """
    try:
        conn = get_connection()
        cursor = conn.cursor()
        
        # 获取当前状态
        cursor.execute("SELECT status FROM rules WHERE rule_id = ?", (rule_id,))
        result = cursor.fetchone()
        previous_status = result[0] if result else 'unknown'
        
        # 更新规则状态
        cursor.execute("""
            UPDATE rules SET status = 'approved', approved_at = ?, approved_by = ?, updated_at = ?
            WHERE rule_id = ?
        """, (datetime.now().isoformat(), approved_by, datetime.now().isoformat(), rule_id))
        
        # 记录审批流程
        cursor.execute("""
            INSERT INTO rule_approvals (rule_id, action, action_by, comment, previous_status, new_status)
            VALUES (?, 'approve', ?, ?, ?, 'approved')
        """, (rule_id, approved_by, comment, previous_status))
        
        conn.commit()
        conn.close()
        
        # 同步到向量库
        from knowledge_base_v2 import get_rule_by_id, generate_combined_text, sync_rule_to_vector_db
        rule = get_rule_by_id(rule_id)
        if rule:
            combined_text = generate_combined_text(rule)
            sync_rule_to_vector_db(rule_id, combined_text, rule)
        
        return True
    except Exception as e:
        print(f"批准规则失败: {e}")
        return False


def reject_rule_v2(rule_id: str, rejected_by: str = "admin", comment: str = "") -> bool:
    """拒绝规则（v2版本，带审批记录）
    
    Args:
        rule_id: 规则ID
        rejected_by: 审批人
        comment: 拒绝原因
        
    Returns:
        是否成功
    """
    try:
        conn = get_connection()
        cursor = conn.cursor()
        
        # 获取当前状态
        cursor.execute("SELECT status FROM rules WHERE rule_id = ?", (rule_id,))
        result = cursor.fetchone()
        previous_status = result[0] if result else 'unknown'
        
        # 更新规则状态
        cursor.execute("""
            UPDATE rules SET status = 'rejected', updated_at = ?
            WHERE rule_id = ?
        """, (datetime.now().isoformat(), rule_id))
        
        # 记录审批流程
        cursor.execute("""
            INSERT INTO rule_approvals (rule_id, action, action_by, comment, previous_status, new_status)
            VALUES (?, 'reject', ?, ?, ?, 'rejected')
        """, (rule_id, rejected_by, comment, previous_status))
        
        conn.commit()
        conn.close()
        
        return True
    except Exception as e:
        print(f"拒绝规则失败: {e}")
        return False


def get_rule_approvals(rule_id: str) -> List[Dict]:
    """获取规则的审批历史
    
    Args:
        rule_id: 规则ID
        
    Returns:
        审批记录列表
    """
    try:
        conn = get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT action, action_by, action_at, comment, previous_status, new_status
            FROM rule_approvals
            WHERE rule_id = ?
            ORDER BY action_at DESC
        """, (rule_id,))
        
        results = cursor.fetchall()
        conn.close()
        
        approvals = []
        for row in results:
            approvals.append({
                "action": row[0],
                "action_by": row[1],
                "action_at": row[2],
                "comment": row[3],
                "previous_status": row[4],
                "new_status": row[5]
            })
        
        return approvals
    except Exception as e:
        print(f"获取审批历史失败: {e}")
        return []


# ========== 效果追踪API ==========

def track_rule_effectiveness(rule_id: str, session_id: str, score_before: int, score_after: int) -> bool:
    """追踪规则效果
    
    Args:
        rule_id: 规则ID
        session_id: 会话ID
        score_before: 改善前评分
        score_after: 改善后评分
        
    Returns:
        是否成功
    """
    try:
        improvement = score_after - score_before
        
        conn = get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO rule_effectiveness (rule_id, session_id, triggered_at, score_before, score_after, improvement)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (rule_id, session_id, datetime.now().isoformat(), score_before, score_after, improvement))
        
        conn.commit()
        conn.close()
        
        return True
    except Exception as e:
        print(f"追踪规则效果失败: {e}")
        return False


def get_rule_effectiveness_stats(rule_id: str) -> Dict:
    """获取规则效果统计
    
    Args:
        rule_id: 规则ID
        
    Returns:
        效果统计
    """
    try:
        conn = get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT 
                COUNT(*) as trigger_count,
                AVG(score_before) as avg_before,
                AVG(score_after) as avg_after,
                AVG(improvement) as avg_improvement,
                MAX(improvement) as max_improvement,
                MIN(improvement) as min_improvement
            FROM rule_effectiveness
            WHERE rule_id = ?
        """, (rule_id,))
        
        result = cursor.fetchone()
        conn.close()
        
        if result:
            return {
                "trigger_count": result[0],
                "avg_score_before": round(result[1], 2) if result[1] else 0,
                "avg_score_after": round(result[2], 2) if result[2] else 0,
                "avg_improvement": round(result[3], 2) if result[3] else 0,
                "max_improvement": result[4] if result[4] else 0,
                "min_improvement": result[5] if result[5] else 0
            }
        
        return {}
    except Exception as e:
        print(f"获取规则效果统计失败: {e}")
        return {}


def get_all_rules_effectiveness() -> List[Dict]:
    """获取所有规则的效果统计
    
    Returns:
        规则效果列表
    """
    try:
        conn = get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT 
                r.rule_id,
                r.scene_category,
                r.rule_dimension,
                COUNT(e.tracking_id) as trigger_count,
                AVG(e.improvement) as avg_improvement
            FROM rules r
            LEFT JOIN rule_effectiveness e ON r.rule_id = e.rule_id
            WHERE r.status = 'approved'
            GROUP BY r.rule_id
            ORDER BY trigger_count DESC
        """)
        
        results = cursor.fetchall()
        conn.close()
        
        stats = []
        for row in results:
            stats.append({
                "rule_id": row[0],
                "scene_category": row[1],
                "rule_dimension": row[2],
                "trigger_count": row[3],
                "avg_improvement": round(row[4], 2) if row[4] else 0
            })
        
        return stats
    except Exception as e:
        print(f"获取所有规则效果失败: {e}")
        return []


# ========== 覆盖率分析API ==========

def get_scene_coverage() -> List[Dict]:
    """获取场景覆盖率
    
    Returns:
        场景覆盖统计
    """
    try:
        conn = get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT 
                scene_category,
                COUNT(*) as rule_count,
                COUNT(DISTINCT rule_dimension) as dimension_count
            FROM rules
            WHERE status = 'approved'
            GROUP BY scene_category
        """)
        
        results = cursor.fetchall()
        conn.close()
        
        coverage = []
        for row in results:
            # 假设每个场景需要至少5条规则，4个维度
            target_rules = 5
            target_dims = 4
            
            rule_coverage = min(row[1] / target_rules * 100, 100)
            dim_coverage = min(row[2] / target_dims * 100, 100)
            overall_coverage = (rule_coverage + dim_coverage) / 2
            
            coverage.append({
                "scene_category": row[0],
                "rule_count": row[1],
                "dimension_count": row[2],
                "rule_coverage": round(rule_coverage, 1),
                "dimension_coverage": round(dim_coverage, 1),
                "overall_coverage": round(overall_coverage, 1)
            })
        
        return coverage
    except Exception as e:
        print(f"获取场景覆盖率失败: {e}")
        return []


def get_dimension_coverage() -> List[Dict]:
    """获取维度覆盖率
    
    Returns:
        维度覆盖统计
    """
    try:
        conn = get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT 
                rule_dimension,
                COUNT(*) as rule_count,
                COUNT(DISTINCT scene_category) as scene_count
            FROM rules
            WHERE status = 'approved'
            GROUP BY rule_dimension
        """)
        
        results = cursor.fetchall()
        conn.close()
        
        coverage = []
        for row in results:
            # 假设每个维度需要至少5条规则，6个场景
            target_rules = 5
            target_scenes = 6
            
            rule_coverage = min(row[1] / target_rules * 100, 100)
            scene_coverage = min(row[2] / target_scenes * 100, 100)
            overall_coverage = (rule_coverage + scene_coverage) / 2
            
            coverage.append({
                "rule_dimension": row[0],
                "rule_count": row[1],
                "scene_count": row[2],
                "rule_coverage": round(rule_coverage, 1),
                "scene_coverage": round(scene_coverage, 1),
                "overall_coverage": round(overall_coverage, 1)
            })
        
        return coverage
    except Exception as e:
        print(f"获取维度覆盖率失败: {e}")
        return []


# ========== 批量操作API ==========

def batch_approve_rules(rule_ids: List[str], approved_by: str = "admin", comment: str = "") -> Tuple[int, List[str]]:
    """批量批准规则
    
    Args:
        rule_ids: 规则ID列表
        approved_by: 审批人
        comment: 审批意见
        
    Returns:
        (成功数量, 失败列表)
    """
    success_count = 0
    failed_rules = []
    
    for rule_id in rule_ids:
        try:
            if approve_rule_v2(rule_id, approved_by, comment):
                success_count += 1
            else:
                failed_rules.append(rule_id)
        except Exception as e:
            print(f"批量批准规则 {rule_id} 失败: {e}")
            failed_rules.append(rule_id)
    
    return success_count, failed_rules


def batch_reject_rules(rule_ids: List[str], rejected_by: str = "admin", comment: str = "") -> Tuple[int, List[str]]:
    """批量拒绝规则
    
    Args:
        rule_ids: 规则ID列表
        rejected_by: 审批人
        comment: 拒绝原因
        
    Returns:
        (成功数量, 失败列表)
    """
    success_count = 0
    failed_rules = []
    
    for rule_id in rule_ids:
        try:
            if reject_rule_v2(rule_id, rejected_by, comment):
                success_count += 1
            else:
                failed_rules.append(rule_id)
        except Exception as e:
            print(f"批量拒绝规则 {rule_id} 失败: {e}")
            failed_rules.append(rule_id)
    
    return success_count, failed_rules


def batch_delete_rules(rule_ids: List[str]) -> Tuple[int, List[str]]:
    """批量删除规则
    
    Args:
        rule_ids: 规则ID列表
        
    Returns:
        (成功数量, 失败列表)
    """
    success_count = 0
    failed_rules = []
    
    for rule_id in rule_ids:
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            # 删除规则
            cursor.execute("DELETE FROM rules WHERE rule_id = ?", (rule_id,))
            
            # 删除关联数据
            cursor.execute("DELETE FROM rule_versions WHERE rule_id = ?", (rule_id,))
            cursor.execute("DELETE FROM rule_approvals WHERE rule_id = ?", (rule_id,))
            cursor.execute("DELETE FROM rule_effectiveness WHERE rule_id = ?", (rule_id,))
            
            conn.commit()
            conn.close()
            
            success_count += 1
        except Exception as e:
            print(f"批量删除规则 {rule_id} 失败: {e}")
            failed_rules.append(rule_id)
    
    return success_count, failed_rules


# ========== 冲突检测API ==========

def detect_rule_conflicts() -> List[Dict]:
    """检测规则冲突
    
    Returns:
        冲突列表
    """
    try:
        conn = get_connection()
        cursor = conn.cursor()
        
        # 查找同场景同维度且关键词重叠的规则
        cursor.execute("""
            SELECT 
                r1.rule_id as rule1_id,
                r2.rule_id as rule2_id,
                r1.scene_category,
                r1.rule_dimension,
                r1.trigger_keywords as keywords1,
                r2.trigger_keywords as keywords2
            FROM rules r1
            JOIN rules r2 ON r1.scene_category = r2.scene_category 
                AND r1.rule_dimension = r2.rule_dimension
                AND r1.rule_id < r2.rule_id
            WHERE r1.status = 'approved' AND r2.status = 'approved'
        """)
        
        results = cursor.fetchall()
        conn.close()
        
        conflicts = []
        for row in results:
            # 解析关键词
            try:
                keywords1 = json.loads(row[4]) if row[4] else []
                keywords2 = json.loads(row[5]) if row[5] else []
            except:
                keywords1 = row[4].split(',') if row[4] else []
                keywords2 = row[5].split(',') if row[5] else []
            
            # 检查关键词重叠
            overlap = set(keywords1) & set(keywords2)
            
            if overlap:
                conflicts.append({
                    "rule1_id": row[0],
                    "rule2_id": row[1],
                    "scene_category": row[2],
                    "rule_dimension": row[3],
                    "overlap_keywords": list(overlap),
                    "conflict_type": "关键词重叠"
                })
        
        return conflicts
    except Exception as e:
        print(f"检测规则冲突失败: {e}")
        return []


# ========== 智能推荐API ==========

def recommend_rules_for_session(session_data: Dict) -> List[Dict]:
    """为会话推荐相关规则
    
    Args:
        session_data: 会话数据
        
    Returns:
        推荐规则列表
    """
    try:
        # 提取会话特征
        messages = session_data.get('messages', [])
        user_messages = [m['content'] for m in messages if m.get('role') in ('user', 'customer')]
        all_text = ' '.join(user_messages)
        
        # 简单关键词匹配（实际应使用向量检索）
        conn = get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT rule_id, scene_category, rule_dimension, trigger_keywords, rule_criteria
            FROM rules
            WHERE status = 'approved'
        """)
        
        results = cursor.fetchall()
        conn.close()
        
        recommendations = []
        for row in results:
            try:
                keywords = json.loads(row[3]) if row[3] else []
            except:
                keywords = row[3].split(',') if row[3] else []
            
            # 计算匹配度
            match_count = sum(1 for kw in keywords if kw in all_text)
            if match_count > 0:
                recommendations.append({
                    "rule_id": row[0],
                    "scene_category": row[1],
                    "rule_dimension": row[2],
                    "match_score": match_count,
                    "rule_criteria": row[4]
                })
        
        # 按匹配度排序
        recommendations.sort(key=lambda x: x['match_score'], reverse=True)
        
        return recommendations[:5]  # 返回Top 5
    except Exception as e:
        print(f"推荐规则失败: {e}")
        return []