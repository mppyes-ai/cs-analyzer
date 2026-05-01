# 璞康集团知识图谱架构设计 v1.0

## 一、核心实体模型

### 1.1 实体类型定义

```yaml
# 产品实体
Product:
  id: product_{brand}_{model}
  type: 产品
  attributes:
    brand: 林内
    model: GD32
    category: 燃气热水器
    capacity: 16L
    price: 3199
    features: [水量伺服器, 恒温精准, ECO节能]
    specs:
      size: 380×532×150mm
      weight: 15kg
      power: 220V/50Hz
    installation:
      hole_size: φ65mm
      pipe_size: G1/2
      requirements: [通风, 防水, 承重]
    policies:
      warranty: 3年整机保修
      return_policy: 7天无理由退货
  
# 场景实体
Scene:
  id: scene_{category}_{sub_category}
  type: 场景
  attributes:
    category: 售前咨询
    sub_category: 价格决策
    triggers: [多少钱, 价格, 优惠, 活动]
    intents: [比价, 选型, 询价]
    related_products: [product_林内_GD31, product_林内_GD32]
    related_policies: [policy_国补_2026]
  
# 政策实体
Policy:
  id: policy_{type}_{year}
  type: 政策
  attributes:
    name: 国补政策2026
    type: 国家补贴
    discount_rate: 0.20
    max_amount: 2000
    valid_from: 2026-01-01
    valid_to: 2026-12-31
    applicable_products: [product_林内_GD32, product_林内_GD33]
    restrictions: [每户限1台, 需实名认证]
    stackable: true
    stack_with: [coupon_京东_满减]
  
# 客户实体
Customer:
  id: customer_{platform}_{user_id}
  type: 客户
  attributes:
    platform: 京东
    user_id: jd_123456
    consultation_history:
      - session_id: session_001
        topic: GD32价格
        timestamp: 2026-04-15T10:00:00
    purchase_history:
      - product_id: product_林内_GD32
        order_id: order_789
        price: 2559
        timestamp: 2026-04-15T14:00:00
    preferences:
      price_sensitive: true
      brand_loyalty: high
      service_focus: [安装, 售后]
    family_profile:
      house_type: 一厨两卫
      members: 4
      budget: 3000-4000
  
# 竞品实体
Competitor:
  id: competitor_{brand}_{model}
  type: 竞品
  attributes:
    brand: A.O.史密斯
    model: JSQ31-TM5
    category: 燃气热水器
    capacity: 16L
    price: 2699
    features: [智能恒温, 静音运行]
    market_share: 0.25
    target_segment: 中高端
  
# 页面实体
Page:
  id: page_{platform}_{type}_{product_id}
  type: 页面
  attributes:
    platform: 京东
    page_type: 商详页
    product_id: product_林内_GD32
    elements:
      - type: 主图
        content: [产品正面, 功能icon, 场景图]
      - type: 参数表
        content: [容量, 尺寸, 功率]
      - type: 对比模块
        content: [GD31_vs_GD32]
    conversion_rate: 0.035
    bounce_rate: 0.45
    avg_time: 120
  
# 问题实体
Issue:
  id: issue_{category}_{timestamp}
  type: 问题
  attributes:
    category: 价格质疑
    content: 为什么比竞品贵500元
    sentiment: negative
    severity: high
    frequency: 156
    trend: increasing
    related_products: [product_林内_GD32]
    related_competitors: [competitor_AO史密斯_JSQ31TM5]
```

### 1.2 关系类型定义

```yaml
relations:
  # 产品关系
  - from: Product
    to: Product
    type: 升级版本
    attributes:
      price_diff: 600
      feature_diff: [水量伺服器]
      target_upgrade: true
  
  - from: Product
    to: Policy
    type: 适用政策
    attributes:
      discount_amount: 639
      final_price: 2559
      applicable: true
  
  - from: Product
    to: Scene
    type: 常见咨询
    attributes:
      consultation_rate: 0.35
      avg_duration: 8.5
      conversion_rate: 0.42
  
  # 场景关系
  - from: Scene
    to: Product
    type: 推荐产品
    attributes:
      match_score: 0.92
      reason: 一厨两卫推荐16L
      priority: 1
  
  - from: Scene
    to: Policy
    type: 涉及政策
    attributes:
      mention_rate: 0.68
      impact_score: 0.85
  
  # 客户关系
  - from: Customer
    to: Product
    type: 已购买
    attributes:
      purchase_date: 2026-04-15
      price_paid: 2559
      satisfaction: 4.5
  
  - from: Customer
    to: Scene
    type: 咨询过
    attributes:
      consultation_count: 3
      last_date: 2026-04-15
      topics: [价格, 安装, 保修]
  
  # 竞品关系
  - from: Product
    to: Competitor
    type: 直接竞争
    attributes:
      price_gap: 500
      feature_overlap: 0.75
      market_position: 高端vs中端
  
  - from: Competitor
    to: Policy
    type: 同样适用
    attributes:
      applicable: true
      competitive_advantage: false
  
  # 页面关系
  - from: Page
    to: Product
    type: 展示产品
    attributes:
      display_order: 1
      highlight_features: [水量伺服器, 国补优惠]
  
  - from: Page
    to: Issue
    type: 存在问题
    attributes:
      issue_location: 主图
      impact: 转化率下降0.5%
  
  # 问题关系
  - from: Issue
    to: Product
    type: 涉及产品
    attributes:
      affected_scope: 所有16L型号
      urgency: high
  
  - from: Issue
    to: Competitor
    type: 对比竞品
    attributes:
      competitor_advantage: 价格
      our_advantage: 品牌
```

## 二、知识图谱Schema

### 2.1 图数据库Schema（Neo4j）

```cypher
// 创建实体标签
CREATE CONSTRAINT product_id IF NOT EXISTS
FOR (p:Product) REQUIRE p.id IS UNIQUE;

CREATE CONSTRAINT scene_id IF NOT EXISTS
FOR (s:Scene) REQUIRE s.id IS UNIQUE;

CREATE CONSTRAINT policy_id IF NOT EXISTS
FOR (po:Policy) REQUIRE po.id IS UNIQUE;

CREATE CONSTRAINT customer_id IF NOT EXISTS
FOR (c:Customer) REQUIRE c.id IS UNIQUE;

CREATE CONSTRAINT competitor_id IF NOT EXISTS
FOR (co:Competitor) REQUIRE co.id IS UNIQUE;

// 创建关系类型
CREATE CONSTRAINT rel_unique IF NOT EXISTS
FOR ()-[r:升级版本]-() REQUIRE r.from_id + r.to_id IS UNIQUE;

// 创建索引
CREATE INDEX product_brand_idx IF NOT EXISTS
FOR (p:Product) ON (p.brand);

CREATE INDEX product_category_idx IF NOT EXISTS
FOR (p:Product) ON (p.category);

CREATE INDEX scene_category_idx IF NOT EXISTS
FOR (s:Scene) ON (s.category);

CREATE INDEX policy_type_idx IF NOT EXISTS
FOR (po:Policy) ON (po.type);
```

### 2.2 属性图模型

```cypher
// 产品节点
(:Product {
  id: "product_林内_GD32",
  brand: "林内",
  model: "GD32",
  category: "燃气热水器",
  capacity: "16L",
  price: 3199,
  features: ["水量伺服器", "恒温精准", "ECO节能"],
  specs: {
    size: "380×532×150mm",
    weight: "15kg",
    power: "220V/50Hz"
  },
  installation: {
    hole_size: "φ65mm",
    pipe_size: "G1/2",
    requirements: ["通风", "防水", "承重"]
  },
  policies: {
    warranty: "3年整机保修",
    return_policy: "7天无理由退货"
  }
})

// 场景节点
(:Scene {
  id: "scene_售前咨询_价格决策",
  category: "售前咨询",
  sub_category: "价格决策",
  triggers: ["多少钱", "价格", "优惠", "活动"],
  intents: ["比价", "选型", "询价"],
  related_products: ["product_林内_GD31", "product_林内_GD32"],
  related_policies: ["policy_国补_2026"]
})

// 政策节点
(:Policy {
  id: "policy_国补_2026",
  name: "国补政策2026",
  type: "国家补贴",
  discount_rate: 0.20,
  max_amount: 2000,
  valid_from: "2026-01-01",
  valid_to: "2026-12-31",
  applicable_products: ["product_林内_GD32", "product_林内_GD33"],
  restrictions: ["每户限1台", "需实名认证"],
  stackable: true,
  stack_with: ["coupon_京东_满减"]
})
```

## 三、查询示例

### 3.1 客服质检查询

```cypher
// 场景：用户问"GD32和GD31有什么区别？"
// 查询：获取评分标准和参考回答

MATCH (s:Scene {category: "售前咨询", sub_category: "产品对比"})
MATCH (s)-[:推荐产品]->(p:Product)
WHERE p.model IN ["GD32", "GD31"]
RETURN 
  s.triggers AS 触发词,
  s.intents AS 用户意图,
  p.model AS 产品型号,
  p.capacity AS 容量,
  p.features AS 核心功能,
  p.price AS 价格;
```

### 3.2 产品培训查询

```cypher
// 场景：新员工培训GD32
// 查询：生成完整产品知识卡片

MATCH (p:Product {id: "product_林内_GD32"})
OPTIONAL MATCH (p)-[:升级版本]->(p2:Product)
OPTIONAL MATCH (p)-[:适用政策]->(po:Policy)
OPTIONAL MATCH (p)-[:常见咨询]->(s:Scene)
OPTIONAL MATCH (s)-[:涉及政策]->(po2:Policy)
RETURN {
  product: p,
  upgrade_from: p2,
  applicable_policies: collect(DISTINCT po),
  common_scenes: collect(DISTINCT s),
  related_policies: collect(DISTINCT po2)
} AS training_material;
```

### 3.3 经营分析查询

```cypher
// 场景：4月份GD32转化率下降分析
// 查询：关联问题、竞品、情绪分析

MATCH (p:Product {id: "product_林内_GD32"})
MATCH (p)-[:常见咨询]->(s:Scene)
MATCH (s)-[:关联问题]->(i:Issue)
WHERE i.timestamp >= "2026-04-01" 
  AND i.sentiment = "negative"
OPTIONAL MATCH (p)-[:直接竞争]->(c:Competitor)
RETURN 
  p.model AS 产品,
  count(i) AS 问题数量,
  collect(DISTINCT i.category) AS 问题类型,
  avg(i.severity) AS 平均严重度,
  collect(DISTINCT c.brand) AS 竞品品牌,
  c.price AS 竞品价格;
```

### 3.4 项目诊断查询

```cypher
// 场景：京东店铺页面转化率低
// 查询：页面问题关联分析

MATCH (page:Page {platform: "京东", page_type: "商详页"})
MATCH (page)-[:展示产品]->(p:Product)
MATCH (page)-[:存在问题]->(i:Issue)
OPTIONAL MATCH (p)-[:常见咨询]->(s:Scene)
OPTIONAL MATCH (s)-[:关联问题]->(i2:Issue)
WHERE i2.content CONTAINS "页面" OR i2.content CONTAINS "图片"
RETURN 
  page.id AS 页面ID,
  p.model AS 产品,
  page.conversion_rate AS 转化率,
  page.bounce_rate AS 跳出率,
  collect(DISTINCT i.category) AS 页面问题,
  collect(DISTINCT i2.content) AS 用户反馈;
```

## 四、混合存储架构

### 4.1 存储方案

```yaml
存储架构:
  图数据库:
    引擎: Neo4j
    用途: 实体关系存储、复杂查询、推理
    数据: 节点、关系、属性
    
  向量数据库:
    引擎: LanceDB
    用途: 语义检索、相似度搜索
    数据: 文本向量、嵌入表示
    
  关系数据库:
    引擎: SQLite
    用途: 结构化数据、统计报表
    数据: 属性表、日志表、配置表
    
  文档数据库:
    引擎: MongoDB (可选)
    用途: 非结构化数据、大文本
    数据: 培训资料、产品手册、聊天记录
```

### 4.2 数据流

```
客服会话数据
    ↓
[数据清洗] → 提取实体（产品、场景、政策）
    ↓
[关系抽取] → 建立关系（适用、涉及、对比）
    ↓
[知识融合] → 合并到知识图谱
    ↓
[向量编码] → 生成语义向量
    ↓
[存储分发] → Neo4j + LanceDB + SQLite
    ↓
[应用服务] → 质检/培训/分析/诊断
```

## 五、实施路线图

### Phase 1: 核心图谱构建（4-6周）

```yaml
Week 1-2:
  - 定义实体类型和属性Schema
  - 搭建Neo4j图数据库
  - 迁移现有规则数据为图谱节点
  
Week 3-4:
  - 建立基础关系网络
  - 实现客服会话数据自动抽取
  - 构建产品-场景-政策关联
  
Week 5-6:
  - 集成向量数据库
  - 实现语义检索能力
  - 优化查询性能
```

### Phase 2: 多场景应用（6-8周）

```yaml
Week 7-8:
  - 重构客服质检模块
  - 基于图谱的评分标准匹配
  - 实现多维度质检报告
  
Week 9-10:
  - 构建产品培训模块
  - 自动生成培训材料
  - 实现知识问答功能
  
Week 11-12:
  - 开发经营分析模块
  - 关联销售数据
  - 实现问题诊断功能
  
Week 13-14:
  - 构建项目诊断模块
  - 接入页面数据
  - 实现竞品对比分析
```

### Phase 3: 智能化升级（4-6周）

```yaml
Week 15-16:
  - 实现图谱推理引擎
  - 基于路径的推荐算法
  - 自动发现新知识
  
Week 17-18:
  - 构建知识更新机制
  - 实现版本管理
  - 建立知识质量评估
  
Week 19-20:
  - 优化查询性能
  - 实现缓存机制
  - 建立监控告警
```

## 六、技术选型

### 6.1 核心组件

| 组件 | 选型 | 版本 | 用途 |
|------|------|------|------|
| 图数据库 | Neo4j | 5.x | 实体关系存储 |
| 向量数据库 | LanceDB | 0.10+ | 语义检索 |
| 关系数据库 | SQLite | 3.40+ | 结构化数据 |
| 嵌入模型 | Qwen3-Embedding | 4B | 文本向量化 |
| LLM | Qwen3.6 | 35B | 知识抽取、推理 |
| 应用框架 | Streamlit | 1.30+ | 前端界面 |
| 编程语言 | Python | 3.14 | 业务逻辑 |

### 6.2 部署架构

```yaml
开发环境:
  Neo4j: localhost:7687
  LanceDB: ./data/knowledge.lance
  SQLite: ./data/cs_analyzer_new.db
  
生产环境:
  Neo4j: 集群部署（3节点）
  LanceDB: 分布式存储
  SQLite: 主从复制
  
备份策略:
  图数据库: 每日全量备份
  向量库: 增量备份
  关系库: 实时同步
```

## 七、预期效果

### 7.1 知识沉淀效率

| 指标 | 当前 | 目标 | 提升 |
|------|------|------|------|
| 规则提取速度 | 人工审核 | 自动提取+审核 | 10x |
| 知识覆盖率 | 20%场景 | 90%场景 | 4.5x |
| 知识更新周期 | 月度 | 实时 | 30x |

### 7.2 多场景应用效果

| 场景 | 当前 | 目标 |
|------|------|------|
| 客服质检 | 人工评分 | AI自动评分+知识推荐 |
| 产品培训 | 纸质手册 | 智能知识问答 |
| 经营分析 | Excel报表 | 图谱关联分析 |
| 项目诊断 | 经验判断 | 数据驱动诊断 |

---

**金总，这是璞康知识图谱的完整架构设计。是否进入Phase 1实施？**