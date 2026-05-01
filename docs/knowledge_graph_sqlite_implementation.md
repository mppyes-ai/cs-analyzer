# 璞康知识图谱Phase 1实施计划

## 当前状态

由于环境限制（Java/Neo4j安装受阻），调整实施策略：

## 调整方案：SQLite + Python实现轻量级知识图谱

### 1. 核心思路

```python
# 不依赖Neo4j，用SQLite + Python实现图谱逻辑
# 保留未来迁移到Neo4j的兼容性

# 实体表（节点）
CREATE TABLE kg_entities (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,  -- Product, Scene, Policy, Customer, Competitor, Page
    name TEXT,
    attributes TEXT,  -- JSON格式
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

# 关系表（边）
CREATE TABLE kg_relations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_id TEXT NOT NULL,
    to_id TEXT NOT NULL,
    type TEXT NOT NULL,  -- 升级版本、适用政策、常见咨询、推荐产品、已购买、直接竞争、展示产品、存在问题、涉及产品、对比竞品
    attributes TEXT,  -- JSON格式
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (from_id) REFERENCES kg_entities(id),
    FOREIGN KEY (to_id) REFERENCES kg_entities(id)
);

# 向量索引表（用于语义检索）
CREATE TABLE kg_vectors (
    entity_id TEXT PRIMARY KEY,
    vector TEXT,  -- JSON数组，2560维
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (entity_id) REFERENCES kg_entities(id)
);
```

### 2. 实体定义

```python
# 产品实体示例
product_gd32 = {
    "id": "product_林内_GD32",
    "type": "Product",
    "name": "林内GD32",
    "attributes": {
        "brand": "林内",
        "model": "GD32",
        "category": "燃气热水器",
        "capacity": "16L",
        "price": 3199,
        "features": ["水量伺服器", "恒温精准", "ECO节能"],
        "specs": {
            "size": "380×532×150mm",
            "weight": "15kg",
            "power": "220V/50Hz"
        },
        "installation": {
            "hole_size": "φ65mm",
            "pipe_size": "G1/2",
            "requirements": ["通风", "防水", "承重"]
        }
    }
}

# 场景实体示例
scene_pre_sale = {
    "id": "scene_售前咨询_价格决策",
    "type": "Scene",
    "name": "售前咨询-价格决策",
    "attributes": {
        "category": "售前咨询",
        "sub_category": "价格决策",
        "triggers": ["多少钱", "价格", "优惠", "活动"],
        "intents": ["比价", "选型", "询价"],
        "related_products": ["product_林内_GD31", "product_林内_GD32"]
    }
}

# 政策实体示例
policy_guobu = {
    "id": "policy_国补_2026",
    "type": "Policy",
    "name": "国补政策2026",
    "attributes": {
        "type": "国家补贴",
        "discount_rate": 0.20,
        "max_amount": 2000,
        "valid_from": "2026-01-01",
        "valid_to": "2026-12-31",
        "applicable_products": ["product_林内_GD32"],
        "restrictions": ["每户限1台", "需实名认证"]
    }
}
```

### 3. 关系定义

```python
# 产品关系
relation_upgrade = {
    "from_id": "product_林内_GD32",
    "to_id": "product_林内_GD31",
    "type": "升级版本",
    "attributes": {
        "price_diff": 600,
        "feature_diff": ["水量伺服器"],
        "target_upgrade": True
    }
}

# 政策关系
relation_policy = {
    "from_id": "product_林内_GD32",
    "to_id": "policy_国补_2026",
    "type": "适用政策",
    "attributes": {
        "discount_amount": 639,
        "final_price": 2559,
        "applicable": True
    }
}

# 场景关系
relation_scene = {
    "from_id": "scene_售前咨询_价格决策",
    "to_id": "product_林内_GD32",
    "type": "推荐产品",
    "attributes": {
        "match_score": 0.92,
        "reason": "一厨两卫推荐16L",
        "priority": 1
    }
}
```

### 4. 查询实现

```python
class KnowledgeGraph:
    def __init__(self, db_path):
        self.conn = sqlite3.connect(db_path)
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
        
        self.cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_relations_from 
            ON kg_relations(from_id)
        """)
        
        self.cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_relations_to 
            ON kg_relations(to_id)
        """)
        
        self.cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_relations_type 
            ON kg_relations(type)
        """)
        
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
        self.cursor.execute("""
            SELECT * FROM kg_entities WHERE id = ?
        """, (entity_id,))
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
            #  outgoing relations
            if relation_type:
                self.cursor.execute("""
                    SELECT * FROM kg_relations 
                    WHERE from_id = ? AND type = ?
                """, (entity_id, relation_type))
            else:
                self.cursor.execute("""
                    SELECT * FROM kg_relations 
                    WHERE from_id = ?
                """, (entity_id,))
            
            for row in self.cursor.fetchall():
                relations.append({
                    "id": row[0],
                    "from_id": row[1],
                    "to_id": row[2],
                    "type": row[3],
                    "attributes": json.loads(row[4]) if row[4] else {},
                    "direction": "out"
                })
        
        if direction in ["in", "both"]:
            #  incoming relations
            if relation_type:
                self.cursor.execute("""
                    SELECT * FROM kg_relations 
                    WHERE to_id = ? AND type = ?
                """, (entity_id, relation_type))
            else:
                self.cursor.execute("""
                    SELECT * FROM kg_relations 
                    WHERE to_id = ?
                """, (entity_id,))
            
            for row in self.cursor.fetchall():
                relations.append({
                    "id": row[0],
                    "from_id": row[1],
                    "to_id": row[2],
                    "type": row[3],
                    "attributes": json.loads(row[4]) if row[4] else {},
                    "direction": "in"
                })
        
        return relations
    
    def query_graph(self, start_entity, relation_path, max_depth=3):
        """图遍历查询"""
        """
        示例：从GD32出发，找所有关联的政策和场景
        path: ["适用政策", "涉及场景"]
        """
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
        """查找两个实体间的路径"""
        # BFS实现
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
    
    def close(self):
        """关闭连接"""
        self.conn.close()
```

### 5. 使用示例

```python
# 初始化知识图谱
kg = KnowledgeGraph("data/knowledge_graph.db")

# 添加产品实体
kg.add_entity(
    "product_林内_GD32",
    "Product",
    "林内GD32",
    {
        "brand": "林内",
        "model": "GD32",
        "capacity": "16L",
        "price": 3199,
        "features": ["水量伺服器", "恒温精准"]
    }
)

# 添加场景实体
kg.add_entity(
    "scene_售前咨询_价格决策",
    "Scene",
    "售前咨询-价格决策",
    {
        "category": "售前咨询",
        "triggers": ["多少钱", "价格", "优惠"]
    }
)

# 添加关系
kg.add_relation(
    "scene_售前咨询_价格决策",
    "product_林内_GD32",
    "推荐产品",
    {"match_score": 0.92, "reason": "一厨两卫推荐16L"}
)

# 查询：GD32关联的所有场景
relations = kg.get_relations("product_林内_GD32", "推荐产品", "in")
for rel in relations:
    entity = kg.get_entity(rel["from_id"])
    print(f"场景: {entity['name']}, 匹配度: {rel['attributes']['match_score']}")

# 图遍历查询：从场景找产品再找政策
results = kg.query_graph(
    "scene_售前咨询_价格决策",
    ["推荐产品", "适用政策"]
)
for result in results:
    print(f"路径: {' -> '.join(result['path'])}")
    print(f"实体: {result['entity']['name']}")
    print(f"属性: {result['entity']['attributes']}")

kg.close()
```

### 6. 与现有系统集成

```python
# 从客服会话自动提取实体和关系
class SessionExtractor:
    def __init__(self, kg: KnowledgeGraph):
        self.kg = kg
    
    def extract_from_session(self, session_data):
        """从会话数据提取知识图谱实体"""
        # 1. 提取产品实体
        products = self._extract_products(session_data['messages'])
        for product in products:
            self.kg.add_entity(
                f"product_林内_{product['model']}",
                "Product",
                f"林内{product['model']}",
                product
            )
        
        # 2. 提取场景实体
        scene = self._extract_scene(session_data['messages'])
        scene_id = f"scene_{scene['category']}_{scene['sub_category']}"
        self.kg.add_entity(scene_id, "Scene", f"{scene['category']}-{scene['sub_category']}", scene)
        
        # 3. 建立关系
        for product in products:
            self.kg.add_relation(
                scene_id,
                f"product_林内_{product['model']}",
                "涉及产品",
                {"confidence": 0.95}
            )
    
    def _extract_products(self, messages):
        """从消息中提取产品信息"""
        products = []
        for msg in messages:
            content = msg.get('content', '')
            # 匹配产品型号
            import re
            models = re.findall(r'GD\d+', content)
            for model in models:
                products.append({
                    "model": model,
                    "brand": "林内",
                    "category": "燃气热水器"
                })
        return products
    
    def _extract_scene(self, messages):
        """从消息中提取场景信息"""
        user_messages = [m['content'] for m in messages if m['role'] == 'user']
        combined = ' '.join(user_messages)
        
        # 简单关键词匹配
        if any(kw in combined for kw in ['多少钱', '价格', '优惠']):
            return {"category": "售前咨询", "sub_category": "价格决策"}
        elif any(kw in combined for kw in ['安装', '尺寸', '预留']):
            return {"category": "售前咨询", "sub_category": "安装咨询"}
        elif any(kw in combined for kw in ['故障', '维修', '保修']):
            return {"category": "售后维修", "sub_category": "故障处理"}
        else:
            return {"category": "其他", "sub_category": "一般咨询"}
```

### 7. 优势

| 特性 | SQLite实现 | Neo4j（未来） |
|------|-----------|--------------|
| **部署成本** | 零（内置） | 需安装Java/Neo4j |
| **查询能力** | 基础遍历 | 高级图算法 |
| **扩展性** | 单节点 | 分布式集群 |
| **兼容性** | 保留Neo4j Cypher语法 | 原生支持 |
| **迁移成本** | 低（数据结构一致） | - |

### 8. 下一步

1. **立即实施**：创建SQLite知识图谱表
2. **数据迁移**：将现有规则转为图谱实体
3. **接口封装**：提供与Neo4j兼容的查询接口
4. **应用集成**：客服质检调用知识图谱评分

---

**金总，是否采用SQLite轻量级方案立即启动？后续可无缝迁移到Neo4j。**