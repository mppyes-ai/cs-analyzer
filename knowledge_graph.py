import sqlite3
import json
import os
import re
from typing import Dict, List, Optional, Any

class KnowledgeGraph:
    """轻量级知识图谱（SQLite实现）
    
    兼容Neo4j属性图模型，未来可无缝迁移
    """
    
    def __init__(self, db_path=None):
        if db_path is None:
            db_path = os.path.join(os.path.dirname(__file__), "data", "cs_analyzer_new.db")
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.cursor = self.conn.cursor()
        self._init_tables()
    
    def _init_tables(self):
        """初始化知识图谱表"""
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS kg_entities (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                name TEXT,
                attributes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS kg_relations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                from_id TEXT NOT NULL,
                to_id TEXT NOT NULL,
                type TEXT NOT NULL,
                attributes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # 创建索引
        self.cursor.execute("CREATE INDEX IF NOT EXISTS idx_kg_rel_from ON kg_relations(from_id)")
        self.cursor.execute("CREATE INDEX IF NOT EXISTS idx_kg_rel_to ON kg_relations(to_id)")
        self.cursor.execute("CREATE INDEX IF NOT EXISTS idx_kg_rel_type ON kg_relations(type)")
        
        self.conn.commit()
    
    def add_entity(self, entity_id, entity_type, name, attributes):
        """添加实体"""
        self.cursor.execute("""
            INSERT OR REPLACE INTO kg_entities (id, type, name, attributes)
            VALUES (?, ?, ?, ?)
        """, (entity_id, entity_type, name, json.dumps(attributes, ensure_ascii=False)))
        self.conn.commit()
    
    def add_relation(self, from_id, to_id, relation_type, attributes=None):
        """添加关系"""
        self.cursor.execute("""
            INSERT INTO kg_relations (from_id, to_id, type, attributes)
            VALUES (?, ?, ?, ?)
        """, (from_id, to_id, relation_type, json.dumps(attributes or {}, ensure_ascii=False)))
        self.conn.commit()
    
    def get_entity(self, entity_id):
        """获取实体"""
        self.cursor.execute("SELECT * FROM kg_entities WHERE id = ?", (entity_id,))
        row = self.cursor.fetchone()
        if row:
            return {
                "id": row[0],
                "type": row[1],
                "name": row[2],
                "attributes": json.loads(row[3]) if row[3] else {}
            }
        return None
    
    def get_relations(self, entity_id, relation_type=None, direction="both"):
        """获取实体关系"""
        relations = []
        
        if direction in ["out", "both"]:
            if relation_type:
                self.cursor.execute("""
                    SELECT * FROM kg_relations WHERE from_id = ? AND type = ?
                """, (entity_id, relation_type))
            else:
                self.cursor.execute("""
                    SELECT * FROM kg_relations WHERE from_id = ?
                """, (entity_id,))
            
            for row in self.cursor.fetchall():
                relations.append({
                    "id": row[0], "from_id": row[1], "to_id": row[2],
                    "type": row[3], "attributes": json.loads(row[4]) if row[4] else {},
                    "direction": "out"
                })
        
        if direction in ["in", "both"]:
            if relation_type:
                self.cursor.execute("""
                    SELECT * FROM kg_relations WHERE to_id = ? AND type = ?
                """, (entity_id, relation_type))
            else:
                self.cursor.execute("""
                    SELECT * FROM kg_relations WHERE to_id = ?
                """, (entity_id,))
            
            for row in self.cursor.fetchall():
                relations.append({
                    "id": row[0], "from_id": row[1], "to_id": row[2],
                    "type": row[3], "attributes": json.loads(row[4]) if row[4] else {},
                    "direction": "in"
                })
        
        return relations
    
    def query_graph(self, start_entity, relation_path, max_depth=3):
        """图遍历查询"""
        results = []
        current_entities = [start_entity]
        
        for depth, relation_type in enumerate(relation_path):
            if depth >= max_depth:
                break
            
            next_entities = []
            for entity_id in current_entities:
                relations = self.get_relations(entity_id, relation_type, "out")
                for rel in relations:
                    entity = self.get_entity(rel["to_id"])
                    if entity:
                        results.append({
                            "path": relation_path[:depth+1],
                            "entity": entity,
                            "relation": rel
                        })
                        next_entities.append(rel["to_id"])
            
            current_entities = next_entities
        
        return results
    
    def find_paths(self, from_id, to_id, max_depth=3):
        """查找两个实体间的路径（BFS）"""
        visited = {from_id}
        queue = [(from_id, [])]
        
        while queue:
            current_id, path = queue.pop(0)
            
            if current_id == to_id:
                return path
            
            if len(path) >= max_depth:
                continue
            
            relations = self.get_relations(current_id, direction="out")
            for rel in relations:
                if rel["to_id"] not in visited:
                    visited.add(rel["to_id"])
                    queue.append((rel["to_id"], path + [rel]))
        
        return None
    
    def update_entity(self, entity_id, attributes):
        """更新实体属性"""
        self.cursor.execute("""
            UPDATE kg_entities 
            SET attributes = ?
            WHERE id = ?
        """, (json.dumps(attributes, ensure_ascii=False), entity_id))
        self.conn.commit()
    
    def close(self):
        """关闭连接"""
        self.conn.close()


class SessionExtractor:
    """从客服会话提取知识图谱实体"""
    
    def __init__(self, kg: KnowledgeGraph):
        self.kg = kg
    
    def extract_from_session(self, session_data, analysis_result=None):
        """从会话数据提取知识图谱实体和关系"""
        messages = session_data.get('messages', [])
        if isinstance(messages, str):
            messages = json.loads(messages)
        
        # 1. 提取产品实体
        products = self._extract_products(messages)
        for product in products:
            entity_id = f"product_林内_{product['model']}"
            self.kg.add_entity(
                entity_id,
                "Product",
                f"林内{product['model']}",
                {
                    "brand": "林内",
                    "model": product['model'],
                    "category": "燃气热水器",
                    "source": "session_extraction"
                }
            )
        
        # 2. 提取场景实体
        scene = self._extract_scene(messages, analysis_result)
        scene_id = f"scene_{scene['category']}_{scene['sub_category']}"
        self.kg.add_entity(
            scene_id,
            "Scene",
            f"{scene['category']}-{scene['sub_category']}",
            scene
        )
        
        # 3. 建立关系
        for product in products:
            product_id = f"product_林内_{product['model']}"
            self.kg.add_relation(
                scene_id,
                product_id,
                "涉及产品",
                {"confidence": 0.95, "source": "session_extraction"}
            )
        
        return {
            "entities": [scene_id] + [f"product_林内_{p['model']}" for p in products],
            "relations": len(products)
        }
    
    def _extract_products(self, messages):
        """从消息中提取产品信息"""
        products = []
        seen_models = set()
        
        # 处理字符串格式的messages
        if isinstance(messages, str):
            try:
                messages = json.loads(messages)
            except:
                return []
        
        for msg in messages:
            if isinstance(msg, dict):
                content = msg.get('content', '')
            elif isinstance(msg, str):
                content = msg
            else:
                continue
                
            models = re.findall(r'GD\d+', content)
            for model in models:
                if model not in seen_models:
                    seen_models.add(model)
                    products.append({
                        "model": model,
                        "brand": "林内",
                        "category": "燃气热水器"
                    })
        
        return products
    
    def _extract_scene(self, messages, analysis_result=None):
        """从消息中提取场景信息"""
        # 处理字符串格式的messages
        if isinstance(messages, str):
            try:
                messages = json.loads(messages)
            except:
                messages = []
        
        if analysis_result and 'session_analysis' in analysis_result:
            sa = analysis_result['session_analysis']
            # 简化场景名称
            scene_category = sa.get('scene_category', '其他')
            user_intent = sa.get('user_intent', '一般咨询')
            
            # 映射到标准场景
            scene_mapping = {
                '售前咨询': {
                    '价格': '价格决策',
                    '安装': '安装咨询',
                    '选型': '产品选型',
                    '优惠': '优惠咨询',
                    '对比': '产品对比'
                },
                '售中服务': {
                    '订单': '订单确认',
                    '物流': '物流查询',
                    '发货': '发货咨询'
                },
                '售后维修': {
                    '故障': '故障处理',
                    '维修': '维修申请',
                    '保修': '保修咨询'
                },
                '客诉处理': {
                    '投诉': '投诉处理',
                    '退货': '退货申请',
                    '退款': '退款咨询'
                }
            }
            
            # 确定子类别
            sub_category = '一般咨询'
            if scene_category in scene_mapping:
                for keyword, mapped_scene in scene_mapping[scene_category].items():
                    if keyword in user_intent:
                        sub_category = mapped_scene
                        break
            
            return {
                "category": scene_category,
                "sub_category": sub_category,
                "triggers": self._extract_keywords(messages),
                "sentiment": sa.get('user_sentiment', 'neutral')
            }
        
        # 基于关键词的默认分类（优化版 - 仅分析用户消息，增强关键词库）
        user_messages = []
        for m in messages:
            if isinstance(m, dict) and m.get('role') == 'user':
                user_messages.append(m.get('content', ''))
            elif isinstance(m, str):
                user_messages.append(m)
        
        combined = ' '.join(user_messages)
        
        # 售中服务（优先判断 - 订单/物流相关）
        if any(kw in combined for kw in ['发货', '延迟发货', '物流', '快递', '什么时候到', '订单', '配送', '送达']):
            return {"category": "售中服务", "sub_category": "订单确认", "triggers": self._extract_keywords(messages)}
        
        # 售前咨询 - 安装（增强关键词库）
        elif any(kw in combined for kw in ['安装', '尺寸', '预留', '辅材', '孔', '烟管', '吊顶', '预埋', '打孔', '开孔', '墙体', '蜂窝大板', '石膏板', '距离']):
            return {"category": "售前咨询", "sub_category": "安装咨询", "triggers": self._extract_keywords(messages)}
        
        # 售前咨询 - 容量/选型（增强关键词库）
        elif any(kw in combined for kw in ['几升', '多少升', '够用吗', '一厨', '两卫', '三口人', '四口人', '五口人', '选型', '推荐', '哪款', '主推款', '选择']):
            return {"category": "售前咨询", "sub_category": "产品选型", "triggers": self._extract_keywords(messages)}
        
        # 售前咨询 - 价格
        elif any(kw in combined for kw in ['多少钱', '价格', '优惠', '活动', '券', '打折', '立减']):
            return {"category": "售前咨询", "sub_category": "价格决策", "triggers": self._extract_keywords(messages)}
        
        # 售前咨询 - 对比
        elif any(kw in combined for kw in ['对比', '区别', '差异', '哪个好', '比较', '差别']):
            return {"category": "售前咨询", "sub_category": "产品对比", "triggers": self._extract_keywords(messages)}
        
        # 售后维修
        elif any(kw in combined for kw in ['故障', '维修', '保修', '售后', '坏了', '报错', '不出热水', '漏水']):
            return {"category": "售后维修", "sub_category": "故障处理", "triggers": self._extract_keywords(messages)}
        
        # 客诉处理
        elif any(kw in combined for kw in ['投诉', '退货', '退款', '不满', '差评', '欺骗', '虚假宣传']):
            return {"category": "客诉处理", "sub_category": "投诉处理", "triggers": self._extract_keywords(messages)}
        
        else:
            return {"category": "其他", "sub_category": "一般咨询", "triggers": self._extract_keywords(messages)}
    
    def _extract_keywords(self, messages):
        """提取关键词"""
        keywords = []
        
        # 处理字符串格式的messages
        if isinstance(messages, str):
            try:
                messages = json.loads(messages)
            except:
                return []
        
        for msg in messages:
            if isinstance(msg, dict):
                content = msg.get('content', '')
            elif isinstance(msg, str):
                content = msg
            else:
                continue
                
            # 提取产品型号
            models = re.findall(r'GD\d+', content)
            keywords.extend(models)
            
            # 提取关键名词
            if '价格' in content or '多少钱' in content:
                keywords.append('价格')
            if '安装' in content:
                keywords.append('安装')
            if '维修' in content:
                keywords.append('维修')
            if '优惠' in content or '活动' in content:
                keywords.append('优惠')
        
        return list(set(keywords))