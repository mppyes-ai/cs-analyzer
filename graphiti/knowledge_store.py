import json
from datetime import datetime
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, asdict
from enum import Enum
import sqlite3
import os

# ============ 数据模型 ============

class ReviewStatus(Enum):
    PENDING = "pending"      # 待审核
    APPROVED = "approved"    # 已通过
    REJECTED = "rejected"    # 已拒绝
    MODIFIED = "modified"    # 已修改

class EntityType(Enum):
    PRODUCT = "ProductEntity"
    SERIES = "ProductSeriesEntity"
    POLICY = "PolicyRuleEntity"
    PROMOTION = "PromotionActivityEntity"
    FAULT = "FaultProblemEntity"
    STAFF = "ServiceStaffEntity"
    CDF = "ConsumerDecisionFactor"

@dataclass
class ExtractedEntity:
    """提取的实体"""
    id: str                          # 唯一ID
    entity_type: str                 # 实体类型
    name: str                        # 实体名称
    attributes: Dict[str, Any]       # 属性
    confidence: float                # 置信度
    source_quote: str                # 原文引用
    source_session: str              # 来源会话ID
    status: str = "pending"          # 审核状态
    reviewer_notes: str = ""         # 审核备注
    modified_attributes: Optional[Dict] = None  # 修改后的属性
    
    def to_dict(self) -> dict:
        return asdict(self)

@dataclass
class ExtractedRelation:
    """提取的关系"""
    id: str                          # 唯一ID
    relation_type: str                 # 关系类型
    source_entity: str               # 源实体名称
    target_entity: str               # 目标实体名称
    attributes: Dict[str, Any]       # 属性
    fact_statement: str              # 事实陈述
    confidence: float                # 置信度
    source_session: str              # 来源会话ID
    status: str = "pending"          # 审核状态
    reviewer_notes: str = ""         # 审核备注
    
    def to_dict(self) -> dict:
        return asdict(self)

# ============ 数据库管理 ============

class KnowledgeStore:
    """知识存储 - SQLite"""
    
    def __init__(self, db_path: str = "knowledge_store.db"):
        self.db_path = db_path
        self.init_db()
    
    def init_db(self):
        """初始化数据库表"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 实体表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS entities (
                id TEXT PRIMARY KEY,
                entity_type TEXT NOT NULL,
                name TEXT NOT NULL,
                attributes TEXT,  -- JSON
                confidence REAL,
                source_quote TEXT,
                source_session TEXT,
                status TEXT DEFAULT 'pending',
                reviewer_notes TEXT,
                modified_attributes TEXT,  -- JSON
                -- 时序追踪字段 (Phase 1)
                first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                frequency INTEGER DEFAULT 1,
                source_sessions TEXT DEFAULT '[]',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # 实体来源关联表 (Phase 1)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS entity_sources (
                entity_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                extracted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                attributes_snapshot TEXT,  -- JSON
                PRIMARY KEY (entity_id, session_id),
                FOREIGN KEY (entity_id) REFERENCES entities(id)
            )
        ''')
        
        # 关系表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS relations (
                id TEXT PRIMARY KEY,
                relation_type TEXT NOT NULL,
                source_entity TEXT NOT NULL,
                target_entity TEXT NOT NULL,
                attributes TEXT,  -- JSON
                fact_statement TEXT,
                confidence REAL,
                source_session TEXT,
                status TEXT DEFAULT 'pending',
                reviewer_notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # 审核日志表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS review_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id TEXT NOT NULL,
                item_type TEXT NOT NULL,  -- 'entity' or 'relation'
                action TEXT NOT NULL,      -- 'approve', 'reject', 'modify'
                reviewer TEXT,
                notes TEXT,
                old_values TEXT,           -- JSON
                new_values TEXT,           -- JSON
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        conn.commit()
        conn.close()
    
    def save_entity(self, entity: ExtractedEntity):
        """保存实体"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT OR REPLACE INTO entities 
            (id, entity_type, name, attributes, confidence, source_quote, 
             source_session, status, reviewer_notes, modified_attributes,
             first_seen, last_seen, frequency, source_sessions)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            entity.id,
            entity.entity_type,
            entity.name,
            json.dumps(entity.attributes, ensure_ascii=False),
            entity.confidence,
            entity.source_quote,
            entity.source_session,
            entity.status,
            entity.reviewer_notes,
            json.dumps(entity.modified_attributes, ensure_ascii=False) if entity.modified_attributes else None,
            # Phase 1: 时序字段
            datetime.now().isoformat(),  # first_seen
            datetime.now().isoformat(),  # last_seen
            1,  # frequency
            json.dumps([entity.source_session], ensure_ascii=False)  # source_sessions
        ))
        
        conn.commit()
        conn.close()
    
    def save_entity_source(self, entity_id: str, session_id: str, attributes: Dict):
        """保存实体来源关联 (Phase 1)"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT OR REPLACE INTO entity_sources 
            (entity_id, session_id, extracted_at, attributes_snapshot)
            VALUES (?, ?, ?, ?)
        ''', (
            entity_id,
            session_id,
            datetime.now().isoformat(),
            json.dumps(attributes, ensure_ascii=False)
        ))
        
        conn.commit()
        conn.close()
    
    def get_entity_sources(self, entity_id: str) -> List[Dict]:
        """获取实体的所有来源 (Phase 1)"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM entity_sources WHERE entity_id = ?
            ORDER BY extracted_at DESC
        ''', (entity_id,))
        
        rows = cursor.fetchall()
        conn.close()
        
        return [{
            "entity_id": row[0],
            "session_id": row[1],
            "extracted_at": row[2],
            "attributes_snapshot": json.loads(row[3]) if row[3] else {}
        } for row in rows]
    
    def save_relation(self, relation: ExtractedRelation):
        """保存关系"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT OR REPLACE INTO relations 
            (id, relation_type, source_entity, target_entity, attributes, 
             fact_statement, confidence, source_session, status, reviewer_notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            relation.id,
            relation.relation_type,
            relation.source_entity,
            relation.target_entity,
            json.dumps(relation.attributes, ensure_ascii=False),
            relation.fact_statement,
            relation.confidence,
            relation.source_session,
            relation.status,
            relation.reviewer_notes
        ))
        
        conn.commit()
        conn.close()
    
    def get_pending_entities(self, entity_type: Optional[str] = None, 
                            limit: int = 50, offset: int = 0) -> List[Dict]:
        """获取待审核实体"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        if entity_type:
            cursor.execute('''
                SELECT * FROM entities 
                WHERE status = 'pending' AND entity_type = ?
                ORDER BY confidence DESC
                LIMIT ? OFFSET ?
            ''', (entity_type, limit, offset))
        else:
            cursor.execute('''
                SELECT * FROM entities 
                WHERE status = 'pending'
                ORDER BY confidence DESC
                LIMIT ? OFFSET ?
            ''', (limit, offset))
        
        rows = cursor.fetchall()
        conn.close()
        
        return [self._row_to_entity_dict(row) for row in rows]
    
    def update_entity_timeline(self, entity_id: str, session_id: str):
        """更新实体时序信息 (Phase 3)"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 获取当前实体信息
        cursor.execute('SELECT frequency, source_sessions, last_seen FROM entities WHERE id = ?', (entity_id,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            return
        
        frequency = row[0] or 1
        source_sessions = json.loads(row[1]) if row[1] else []
        
        # 更新频率和来源
        if session_id not in source_sessions:
            source_sessions.append(session_id)
            frequency += 1
        
        # 更新last_seen
        cursor.execute('''
            UPDATE entities 
            SET last_seen = ?,
                frequency = ?,
                source_sessions = ?,
                updated_at = ?
            WHERE id = ?
        ''', (
            datetime.now().isoformat(),
            frequency,
            json.dumps(source_sessions, ensure_ascii=False),
            datetime.now().isoformat(),
            entity_id
        ))
        
        conn.commit()
        conn.close()
    
    def get_expired_entities(self, days: int = 90) -> List[Dict]:
        """获取过期实体 (Phase 3)"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM entities 
            WHERE status = 'approved' 
            AND last_seen < datetime('now', '-{} days')
            ORDER BY last_seen ASC
        '''.format(days))
        
        rows = cursor.fetchall()
        conn.close()
        
        return [self._row_to_entity_dict(row) for row in rows]
    
    def get_entity_timeline(self, entity_id: str) -> Dict:
        """获取实体时序信息 (Phase 3)"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 获取实体基本信息
        cursor.execute('SELECT * FROM entities WHERE id = ?', (entity_id,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            return {}
        
        entity = self._row_to_entity_dict(row)
        
        # 获取来源历史
        cursor.execute('''
            SELECT * FROM entity_sources 
            WHERE entity_id = ? 
            ORDER BY extracted_at ASC
        ''', (entity_id,))
        
        sources = cursor.fetchall()
        conn.close()
        
        timeline = {
            "entity_id": entity_id,
            "name": entity["name"],
            "first_seen": entity.get("first_seen"),
            "last_seen": entity.get("last_seen"),
            "frequency": entity.get("frequency", 1),
            "days_since_last_seen": self._days_since(entity.get("last_seen")),
            "is_expired": self._days_since(entity.get("last_seen")) > 90,
            "source_history": [
                {
                    "session_id": s[1],
                    "extracted_at": s[2],
                    "attributes": json.loads(s[3]) if s[3] else {}
                } for s in sources
            ]
        }
        
        return timeline
    
    def _days_since(self, timestamp_str: str) -> int:
        """计算距离今天的天数"""
        if not timestamp_str:
            return 999
        try:
            dt = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
            return (datetime.now() - dt).days
        except:
            return 999
    
    def get_entity_stats(self) -> Dict:
        """获取实体统计"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 按状态统计
        cursor.execute('''
            SELECT status, COUNT(*) FROM entities GROUP BY status
        ''')
        status_counts = dict(cursor.fetchall())
        
        # 按类型统计
        cursor.execute('''
            SELECT entity_type, COUNT(*) FROM entities GROUP BY entity_type
        ''')
        type_counts = dict(cursor.fetchall())
        
        # 平均置信度
        cursor.execute('''
            SELECT AVG(confidence) FROM entities WHERE status = 'pending'
        ''')
        avg_confidence = cursor.fetchone()[0] or 0
        
        # 过期实体数量 (Phase 3)
        cursor.execute('''
            SELECT COUNT(*) FROM entities 
            WHERE status = 'approved' 
            AND last_seen < datetime('now', '-90 days')
        ''')
        expired_count = cursor.fetchone()[0]
        
        conn.close()
        
        return {
            "by_status": status_counts,
            "by_type": type_counts,
            "avg_pending_confidence": round(avg_confidence, 3),
            "expired_count": expired_count  # Phase 3
        }
    
    def _row_to_entity_dict(self, row) -> Dict:
        """将数据库行转换为实体字典"""
        return {
            "id": row[0],
            "entity_type": row[1],
            "name": row[2],
            "attributes": json.loads(row[3]) if row[3] else {},
            "confidence": row[4],
            "source_quote": row[5],
            "source_session": row[6],
            "status": row[7],
            "reviewer_notes": row[8],
            "modified_attributes": json.loads(row[9]) if row[9] else None,
            # Phase 1: 时序字段
            "first_seen": row[10],
            "last_seen": row[11],
            "frequency": row[12],
            "source_sessions": json.loads(row[13]) if row[13] else [],
            "created_at": row[14],
            "updated_at": row[15]
        }

# ============ Phase 2: 实体合并功能 ============

class EntityMerger:
    """实体合并器"""
    
    def __init__(self, store: KnowledgeStore):
        self.store = store
    
    def find_similar_entities(self, entity_id: str, threshold: float = 0.9) -> List[Dict]:
        """查找相似实体"""
        conn = sqlite3.connect(self.store.db_path)
        cursor = conn.cursor()
        
        # 获取当前实体
        cursor.execute('SELECT * FROM entities WHERE id = ?', (entity_id,))
        current = cursor.fetchone()
        if not current:
            conn.close()
            return []
        
        current_name = current[2]  # name
        current_type = current[1]   # entity_type
        
        # 查找同类型的其他实体
        cursor.execute('''
            SELECT * FROM entities 
            WHERE entity_type = ? AND id != ?
        ''', (current_type, entity_id))
        
        candidates = cursor.fetchall()
        conn.close()
        
        similar = []
        for candidate in candidates:
            candidate_name = candidate[2]
            similarity = self._calculate_similarity(current_name, candidate_name)
            
            if similarity >= threshold:
                similar.append({
                    "entity": self.store._row_to_entity_dict(candidate),
                    "similarity": similarity
                })
        
        return sorted(similar, key=lambda x: x["similarity"], reverse=True)
    
    def _calculate_similarity(self, name1: str, name2: str) -> float:
        """计算实体名称相似度"""
        # 完全匹配
        if name1 == name2:
            return 1.0
        
        # 包含匹配
        if name1 in name2 or name2 in name1:
            return 0.95
        
        # 编辑距离匹配
        return self._edit_distance_similarity(name1, name2)
    
    def _edit_distance_similarity(self, s1: str, s2: str) -> float:
        """基于编辑距离的相似度"""
        if len(s1) == 0 and len(s2) == 0:
            return 1.0
        
        # 简化的编辑距离计算
        max_len = max(len(s1), len(s2))
        if max_len == 0:
            return 1.0
        
        # 计算公共子串长度
        common_len = self._longest_common_substring(s1, s2)
        return common_len / max_len
    
    def _longest_common_substring(self, s1: str, s2: str) -> int:
        """计算最长公共子串长度"""
        if not s1 or not s2:
            return 0
        
        m, n = len(s1), len(s2)
        dp = [[0] * (n + 1) for _ in range(m + 1)]
        max_len = 0
        
        for i in range(1, m + 1):
            for j in range(1, n + 1):
                if s1[i-1] == s2[j-1]:
                    dp[i][j] = dp[i-1][j-1] + 1
                    max_len = max(max_len, dp[i][j])
                else:
                    dp[i][j] = 0
        
        return max_len
    
    def merge_entities(self, main_entity_id: str, merge_entity_ids: List[str], 
                      conflict_resolution: str = "keep_all") -> Dict:
        """合并多个实体"""
        conn = sqlite3.connect(self.store.db_path)
        cursor = conn.cursor()
        
        # 获取主实体
        cursor.execute('SELECT * FROM entities WHERE id = ?', (main_entity_id,))
        main_row = cursor.fetchone()
        if not main_row:
            conn.close()
            return {"error": "Main entity not found"}
        
        main_entity = self.store._row_to_entity_dict(main_row)
        merged_attributes = dict(main_entity["attributes"])
        conflicts = []
        all_sources = set(main_entity.get("source_sessions", []))
        
        # 合并其他实体
        for merge_id in merge_entity_ids:
            cursor.execute('SELECT * FROM entities WHERE id = ?', (merge_id,))
            merge_row = cursor.fetchone()
            if not merge_row:
                continue
            
            merge_entity = self.store._row_to_entity_dict(merge_row)
            merge_attributes = merge_entity.get("attributes", {})
            
            # 合并属性
            for key, value in merge_attributes.items():
                if key not in merged_attributes:
                    # 新属性，直接添加
                    merged_attributes[key] = value
                elif merged_attributes[key] == value:
                    # 相同值，无需处理
                    pass
                else:
                    # 冲突
                    conflicts.append({
                        "attribute": key,
                        "main_value": merged_attributes[key],
                        "merge_value": value,
                        "main_source": main_entity["source_session"],
                        "merge_source": merge_entity["source_session"]
                    })
                    
                    if conflict_resolution == "keep_all":
                        # 保留所有值（用列表存储）
                        if not isinstance(merged_attributes[key], list):
                            merged_attributes[key] = [merged_attributes[key]]
                        if value not in merged_attributes[key]:
                            merged_attributes[key].append(value)
            
            # 合并来源
            merge_sources = merge_entity.get("source_sessions", [])
            all_sources.update(merge_sources)
            
            # 更新来源关联表
            cursor.execute('''
                UPDATE entity_sources 
                SET entity_id = ? 
                WHERE entity_id = ?
            ''', (main_entity_id, merge_id))
            
            # 删除被合并的实体
            cursor.execute('DELETE FROM entities WHERE id = ?', (merge_id,))
        
        # 更新主实体
        cursor.execute('''
            UPDATE entities 
            SET attributes = ?,
                source_sessions = ?,
                frequency = frequency + ?,
                last_seen = ?,
                updated_at = ?
            WHERE id = ?
        ''', (
            json.dumps(merged_attributes, ensure_ascii=False),
            json.dumps(list(all_sources), ensure_ascii=False),
            len(merge_entity_ids),
            datetime.now().isoformat(),
            datetime.now().isoformat(),
            main_entity_id
        ))
        
        conn.commit()
        conn.close()
        
        return {
            "success": True,
            "main_entity_id": main_entity_id,
            "merged_count": len(merge_entity_ids),
            "conflicts": conflicts,
            "merged_attributes": merged_attributes
        }

# ============ 审核工作流 ============

class ReviewWorkflow:
    """审核工作流"""
    
    def __init__(self, store: KnowledgeStore):
        self.store = store
    
    def approve_entity(self, entity_id: str, reviewer: str = "", notes: str = ""):
        """通过实体"""
        conn = sqlite3.connect(self.store.db_path)
        cursor = conn.cursor()
        
        # 更新实体状态
        cursor.execute('''
            UPDATE entities 
            SET status = 'approved', reviewer_notes = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (notes, entity_id))
        
        # 记录日志
        cursor.execute('''
            INSERT INTO review_logs (item_id, item_type, action, reviewer, notes)
            VALUES (?, 'entity', 'approve', ?, ?)
        ''', (entity_id, reviewer, notes))
        
        conn.commit()
        conn.close()
    
    def reject_entity(self, entity_id: str, reviewer: str = "", notes: str = ""):
        """拒绝实体"""
        conn = sqlite3.connect(self.store.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE entities 
            SET status = 'rejected', reviewer_notes = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (notes, entity_id))
        
        cursor.execute('''
            INSERT INTO review_logs (item_id, item_type, action, reviewer, notes)
            VALUES (?, 'entity', 'reject', ?, ?)
        ''', (entity_id, reviewer, notes))
        
        conn.commit()
        conn.close()
    
    def approve_entities_batch(self, entity_ids: List[str], reviewer: str = "", notes: str = ""):
        """批量通过实体"""
        conn = sqlite3.connect(self.store.db_path)
        cursor = conn.cursor()
        
        approved_count = 0
        for entity_id in entity_ids:
            # 更新实体状态
            cursor.execute('''
                UPDATE entities 
                SET status = 'approved', reviewer_notes = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = 'pending'
            ''', (notes, entity_id))
            
            if cursor.rowcount > 0:
                approved_count += 1
                # 记录日志
                cursor.execute('''
                    INSERT INTO review_logs (item_id, item_type, action, reviewer, notes)
                    VALUES (?, 'entity', 'approve', ?, ?)
                ''', (entity_id, reviewer, notes))
        
        conn.commit()
        conn.close()
        
        return {"approved_count": approved_count, "total_requested": len(entity_ids)}
    
    def reject_entities_batch(self, entity_ids: List[str], reviewer: str = "", notes: str = ""):
        """批量拒绝实体"""
        conn = sqlite3.connect(self.store.db_path)
        cursor = conn.cursor()
        
        rejected_count = 0
        for entity_id in entity_ids:
            cursor.execute('''
                UPDATE entities 
                SET status = 'rejected', reviewer_notes = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = 'pending'
            ''', (notes, entity_id))
            
            if cursor.rowcount > 0:
                rejected_count += 1
                cursor.execute('''
                    INSERT INTO review_logs (item_id, item_type, action, reviewer, notes)
                    VALUES (?, 'entity', 'reject', ?, ?)
                ''', (entity_id, reviewer, notes))
        
        conn.commit()
        conn.close()
        
        return {"rejected_count": rejected_count, "total_requested": len(entity_ids)}
    
    def get_review_history(self, entity_id: str) -> List[Dict]:
        """获取实体的审核历史"""
        conn = sqlite3.connect(self.store.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM review_logs 
            WHERE item_id = ? AND item_type = 'entity'
            ORDER BY created_at DESC
        ''', (entity_id,))
        
        rows = cursor.fetchall()
        conn.close()
        
        return [{
            "id": row[0],
            "action": row[3],
            "reviewer": row[4],
            "notes": row[5],
            "old_values": json.loads(row[6]) if row[6] else None,
            "new_values": json.loads(row[7]) if row[7] else None,
            "created_at": row[8]
        } for row in rows]
    
    def get_entities_by_session(self, session_id: str, status: Optional[str] = None) -> List[Dict]:
        """获取指定会话的实体（用于融合版页面）"""
        conn = sqlite3.connect(self.store.db_path)
        cursor = conn.cursor()
        
        if status:
            cursor.execute('''
                SELECT * FROM entities 
                WHERE source_session = ? AND status = ?
                ORDER BY confidence DESC
            ''', (session_id, status))
        else:
            cursor.execute('''
                SELECT * FROM entities 
                WHERE source_session = ?
                ORDER BY confidence DESC
            ''', (session_id,))
        
        rows = cursor.fetchall()
        conn.close()
        
        return [self.store._row_to_entity_dict(row) for row in rows]
    
    def get_entities_with_conflicts(self, limit: int = 50) -> List[Dict]:
        """获取有冲突属性的实体（用于优先审核）"""
        conn = sqlite3.connect(self.store.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM entities 
            WHERE status = 'pending'
            AND modified_attributes IS NOT NULL
            ORDER BY confidence DESC
            LIMIT ?
        ''', (limit,))
        
        rows = cursor.fetchall()
        conn.close()
        
        entities = []
        for row in rows:
            entity = self.store._row_to_entity_dict(row)
            # 检查是否有冲突标记
            attrs = entity.get("attributes", {})
            has_conflict = any(isinstance(v, list) and len(v) > 1 for v in attrs.values())
            entity["has_conflict"] = has_conflict
            entities.append(entity)
        
        return entities

    def auto_extract_from_session(self, session_id: str, session_messages: List[Dict], 
                                   session_analysis: Dict) -> Dict:
        """从会话自动提取知识并入库（B方案：100%进入待审核池）
        
        Args:
            session_id: 会话ID
            session_messages: 会话消息列表
            session_analysis: AI分析结果（含评分、意图等）
            
        Returns:
            {"entities": [...], "relations": [...], "status": "pending"}
        """
        # TODO: 调用LLM提取实体和关系（当前用模拟数据演示）
        # 实际实现需要接入提取模型
        
        extracted_entities = []
        extracted_relations = []
        
        # 从分析结果中提取关键信息
        # 示例：从产品提及中提取ProductEntity
        mentioned_products = session_analysis.get("mentioned_products", [])
        for i, product in enumerate(mentioned_products):
            entity = ExtractedEntity(
                id=f"{session_id}_product_{i}",
                entity_type="ProductEntity",
                name=product.get("name", "Unknown"),
                attributes={
                    "model": product.get("model", ""),
                    "category": product.get("category", ""),
                    "features": product.get("features", [])
                },
                confidence=product.get("confidence", 0.8),  # 当前不限阈值，全部进池
                source_quote=product.get("quote", ""),
                source_session=session_id
            )
            
            # 检查相似实体并合并
            similar = self.merger.find_similar_entities(entity.id, threshold=0.9)
            if similar:
                merge_ids = [s["entity"]["id"] for s in similar]
                merge_result = self.merger.merge_entities(
                    entity.id, merge_ids, conflict_resolution="keep_all"
                )
                if merge_result.get("success"):
                    # 合并后更新时序
                    self.update_entity_timeline(entity.id, session_id)
            else:
                self.save_entity(entity)
                self.save_entity_source(entity.id, session_id, entity.attributes)
            
            extracted_entities.append(entity.to_dict())
        
        # 从问题提及中提取FaultProblemEntity
        mentioned_issues = session_analysis.get("mentioned_issues", [])
        for i, issue in enumerate(mentioned_issues):
            entity = ExtractedEntity(
                id=f"{session_id}_issue_{i}",
                entity_type="FaultProblemEntity",
                name=issue.get("description", "Unknown")[:50],  # 限制长度
                attributes={
                    "severity": issue.get("severity", "medium"),
                    "category": issue.get("category", ""),
                    "solution_hint": issue.get("solution_hint", "")
                },
                confidence=issue.get("confidence", 0.7),
                source_quote=issue.get("quote", ""),
                source_session=session_id
            )
            self.save_entity(entity)
            self.save_entity_source(entity.id, session_id, entity.attributes)
            extracted_entities.append(entity.to_dict())
        
        return {
            "session_id": session_id,
            "entities": extracted_entities,
            "relations": extracted_relations,
            "status": "pending",  # 全部进入待审核池
            "total_extracted": len(extracted_entities)
        }

# ============ 数据导入 ============

class DataImporter:
    """数据导入器 - 从批量测试结果导入"""
    
    def __init__(self, store: KnowledgeStore):
        self.store = store
        self.merger = EntityMerger(store)
    
    def import_from_batch_results(self, results_file: str):
        """从批量测试结果导入"""
        with open(results_file, 'r', encoding='utf-8') as f:
            results = [json.loads(line) for line in f if line.strip()]
        
        imported_entities = 0
        imported_relations = 0
        merged_entities = 0
        
        for result in results:
            if result.get('status') != 'success':
                continue
            
            session_id = result.get('session_id', 'unknown')
            extraction = result.get('extraction', {})
            
            # 导入实体
            for entity_data in extraction.get('entities', []):
                entity = ExtractedEntity(
                    id=f"{session_id}_{entity_data.get('name', 'unknown')}_{imported_entities}",
                    entity_type=entity_data.get('entity_type', 'Unknown'),
                    name=entity_data.get('name', 'Unknown'),
                    attributes=entity_data.get('attributes', {}),
                    confidence=entity_data.get('confidence', 0.5),
                    source_quote=entity_data.get('source_quote', ''),
                    source_session=session_id
                )
                
                # 检查是否有相似实体（Phase 2）
                similar = self.merger.find_similar_entities(entity.id, threshold=0.9)
                if similar:
                    # 找到相似实体，合并
                    merge_ids = [s["entity"]["id"] for s in similar]
                    merge_result = self.merger.merge_entities(
                        entity.id, merge_ids, conflict_resolution="keep_all"
                    )
                    if merge_result.get("success"):
                        merged_entities += 1
                else:
                    # 没有相似实体，直接保存
                    self.store.save_entity(entity)
                
                # 保存来源关联
                self.store.save_entity_source(
                    entity.id, session_id, entity_data.get('attributes', {})
                )
                
                imported_entities += 1
            
            # 导入关系
            for relation_data in extraction.get('relations', []):
                relation = ExtractedRelation(
                    id=f"{session_id}_{relation_data.get('source_entity', 'unknown')}_{relation_data.get('target_entity', 'unknown')}_{imported_relations}",
                    relation_type=relation_data.get('relation_type', 'RELATES_TO'),
                    source_entity=relation_data.get('source_entity', ''),
                    target_entity=relation_data.get('target_entity', ''),
                    attributes=relation_data.get('attributes', {}),
                    fact_statement=relation_data.get('fact_statement', ''),
                    confidence=relation_data.get('confidence', 0.5),
                    source_session=session_id
                )
                self.store.save_relation(relation)
                imported_relations += 1
        
        return {
            "imported_entities": imported_entities,
            "imported_relations": imported_relations,
            "merged_entities": merged_entities,
            "total_sessions": len(results)
        }

# ============ 主入口 ============

if __name__ == "__main__":
    # 初始化存储
    store = KnowledgeStore("knowledge_store.db")
    
    # 导入100条测试结果
    importer = DataImporter(store)
    stats = importer.import_from_batch_results("../batch_test_100_results.jsonl")
    
    print("=" * 60)
    print("数据导入完成")
    print("=" * 60)
    print(f"导入实体: {stats['imported_entities']}")
    print(f"导入关系: {stats['imported_relations']}")
    print(f"合并实体: {stats['merged_entities']}")
    print(f"来源会话: {stats['total_sessions']}")
    
    # 显示统计
    entity_stats = store.get_entity_stats()
    print(f"\n实体统计:")
    print(f"  按状态: {entity_stats['by_status']}")
    print(f"  按类型: {entity_stats['by_type']}")
    print(f"  待审核平均置信度: {entity_stats['avg_pending_confidence']}")
    
    # 显示前5个待审核实体
    pending = store.get_pending_entities(limit=5)
    print(f"\n前5个待审核实体:")
    for e in pending:
        print(f"  - [{e['entity_type']}] {e['name']} (置信度: {e['confidence']})")
        print(f"    首次发现: {e.get('first_seen', 'N/A')}")
        print(f"    最近验证: {e.get('last_seen', 'N/A')}")
        print(f"    出现次数: {e.get('frequency', 1)}")
        print(f"    来源会话: {e.get('source_sessions', [])}")
