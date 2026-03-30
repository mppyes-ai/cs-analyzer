# 数据库Schema说明

## 概述

新数据库采用**单表+JSON存储**设计，简化结构，统一标准。

**数据库文件**：`cs_analyzer_new.db`

**设计原则**：
- 单表存储，避免多表关联
- JSON格式存储可变数据（消息列表、分析详情）
- 独立字段存储常用查询字段（摘要、评分）

---

## 表结构

### 1. sessions - 会话主表

```sql
CREATE TABLE sessions (
    session_id TEXT PRIMARY KEY,           -- 会话唯一ID
    user_id TEXT NOT NULL,                 -- 用户ID
    staff_name TEXT,                       -- 主要接待客服
    session_count INTEGER DEFAULT 1,       -- 合并的原始会话数
    start_time TEXT,                       -- 开始时间
    end_time TEXT,                         -- 结束时间
    created_at TIMESTAMP,                  -- 记录创建时间
    messages TEXT NOT NULL,                -- 消息列表（JSON）
    summary TEXT,                          -- 会话摘要
    professionalism_score INTEGER,         -- 专业性评分 1-5
    standardization_score INTEGER,         -- 标准化评分 1-5
    policy_execution_score INTEGER,        -- 政策执行评分 1-5
    conversion_score INTEGER,              -- 转化能力评分 1-5
    total_score INTEGER,                   -- 总分 4-20
    strengths TEXT,                        -- 亮点/优秀表现（JSON数组）
    issues TEXT,                           -- 问题清单（JSON数组）
    suggestions TEXT,                      -- 改进建议（JSON数组）
    analysis_json TEXT                     -- 详细分析（JSON）
);
```

**字段说明：**

| 字段 | 类型 | 说明 |
|------|------|------|
| session_id | TEXT | 格式：`{user_id}_{timestamp}`，如 `jd_xxx_2026-03-12_235859` |
| user_id | TEXT | 用户ID，如 `jd_4f195a60996b3` |
| staff_name | TEXT | 主要接待客服名称，如 `林内林小立` |
| session_count | INTEGER | 合并的原始会话数量（同用户多会话合并） |
| start_time | TEXT | 会话开始时间 `YYYY-MM-DD HH:MM:SS` |
| end_time | TEXT | 会话结束时间 `YYYY-MM-DD HH:MM:SS` |
| messages | TEXT | 消息列表JSON，见下方格式 |
| summary | TEXT | 会话摘要，如"用户咨询延迟发货，客服准确回答" |
| professionalism_score | INTEGER | 专业性评分（1-5分） |
| standardization_score | INTEGER | 标准化评分（1-5分） |
| policy_execution_score | INTEGER | 政策执行评分（1-5分） |
| conversion_score | INTEGER | 转化能力评分（1-5分） |
| total_score | INTEGER | 总分（4-20分） |
| strengths | TEXT | 亮点/优秀表现（JSON数组），如 `["详细介绍活动", "主动催促下单"]` |
| issues | TEXT | 问题清单（JSON数组），如 `["响应慢", "未主动推荐"]` |
| suggestions | TEXT | 改进建议（JSON数组），如 `["加快首响速度", "挖掘需求"]` |
| analysis_json | TEXT | 详细分析JSON，包含4维度评分理由，见下方格式 |

**messages JSON 格式：**

```json
[
  {
    "sender": "jd_4f195a60996b3",
    "role": "customer",
    "timestamp": "2026-03-12 23:58:59",
    "content": "可以延迟发货吗"
  },
  {
    "sender": "林内林小立",
    "role": "staff",
    "timestamp": "2026-03-12 23:59:09",
    "content": "人工客服【林小立】将为您竭诚服务。"
  }
]
```

**analysis_json JSON 格式：**

```json
{
  "dimensions": {
    "professionalism": {
      "score": 4,
      "reason": "准确回答了延迟发货的操作方式"
    },
    "standardization": {
      "score": 4,
      "reason": "使用了礼貌用语，结束语规范"
    },
    "policy": {
      "score": 3,
      "reason": "说明了延迟发货政策，但未主动提及京东物流配送"
    },
    "conversion": {
      "score": 3,
      "reason": "被动回答用户问题，没有主动引导"
    }
  }
}
```

**注意**：`issues` 和 `suggestions` 已作为独立字段存储，便于查询和筛选。analysis_json 仅保留 dimensions 的详细评分理由。

---

### 2. daily_stats - 每日统计表

```sql
CREATE TABLE daily_stats (
    date TEXT PRIMARY KEY,                 -- 日期
    total_sessions INTEGER,                -- 总会话数
    avg_professionalism REAL,              -- 平均专业性
    avg_standardization REAL,              -- 平均标准化
    avg_policy_execution REAL,             -- 平均政策执行
    avg_conversion REAL,                   -- 平均转化能力
    avg_total REAL,                        -- 平均总分
    created_at TIMESTAMP                   -- 记录创建时间
);
```

---

## 索引

```sql
-- 按用户查询
CREATE INDEX idx_sessions_user_id ON sessions(user_id);

-- 按客服查询
CREATE INDEX idx_sessions_staff_name ON sessions(staff_name);

-- 按时间查询
CREATE INDEX idx_sessions_created_at ON sessions(created_at);
```

---

## 常用查询示例

### 查看所有会话的平均分
```sql
SELECT 
    ROUND(AVG(professionalism_score), 2) as avg_professionalism,
    ROUND(AVG(standardization_score), 2) as avg_standardization,
    ROUND(AVG(policy_execution_score), 2) as avg_policy,
    ROUND(AVG(conversion_score), 2) as avg_conversion,
    ROUND(AVG(total_score), 2) as avg_total
FROM sessions;
```

### 查看低分会话（总分<=12）
```sql
SELECT session_id, user_id, staff_name, total_score, summary
FROM sessions
WHERE total_score <= 12
ORDER BY total_score ASC;
```

### 查看某客服的会话质量
```sql
SELECT 
    staff_name,
    COUNT(*) as session_count,
    ROUND(AVG(professionalism_score), 2) as avg_professionalism,
    ROUND(AVG(standardization_score), 2) as avg_standardization,
    ROUND(AVG(conversion_score), 2) as avg_conversion
FROM sessions
WHERE staff_name = '林内林小立'
GROUP BY staff_name;
```

### 查询某用户的所有会话
```sql
SELECT session_id, summary, total_score, created_at
FROM sessions
WHERE user_id = 'jd_4f195a60996b3'
ORDER BY created_at DESC;
```

### 查看今日新增会话
```sql
SELECT 
    session_id, 
    user_id, 
    staff_name, 
    summary, 
    total_score
FROM sessions
WHERE DATE(created_at) = DATE('now')
ORDER BY created_at DESC;
```

### 查看优秀案例（亮点最多的会话）
```sql
SELECT 
    session_id,
    staff_name,
    total_score,
    strengths
FROM sessions
WHERE json_array_length(strengths) > 0
ORDER BY json_array_length(strengths) DESC, total_score DESC
LIMIT 10;
```

### 查询包含特定亮点的会话
```sql
SELECT 
    session_id,
    staff_name,
    total_score,
    strengths
FROM sessions
WHERE strengths LIKE '%主动催促下单%';
```

### 查看某客服的优秀表现
```sql
SELECT 
    staff_name,
    session_id,
    total_score,
    strengths
FROM sessions
WHERE staff_name = '林内林小朔'
    AND json_array_length(strengths) > 0
ORDER BY total_score DESC;
```

### 从JSON中提取问题列表
```sql
SELECT 
    session_id,
    summary,
    issues
FROM sessions
WHERE json_array_length(issues) > 0;
```

### 查询包含特定问题的会话
```sql
SELECT 
    session_id,
    staff_name,
    summary,
    issues
FROM sessions
WHERE issues LIKE '%转化能力%';
```

### 查询有改进建议的会话
```sql
SELECT 
    session_id,
    staff_name,
    total_score,
    suggestions
FROM sessions
WHERE json_array_length(suggestions) > 0
ORDER BY total_score ASC;
```

### 查看问题最多的客服
```sql
SELECT 
    staff_name,
    COUNT(*) as session_count,
    SUM(json_array_length(issues)) as total_issues
FROM sessions
GROUP BY staff_name
ORDER BY total_issues DESC;
```
