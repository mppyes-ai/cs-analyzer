# CS-Analyzer 系统全景文档

## 一、项目背景

### 1.1 项目目标
CS-Analyzer（客服会话智能分析系统）是一款面向电商客服场景的AI质检工具，主要服务于林内品牌京东自营店的客服团队。系统通过分析客服与客户的聊天记录，对客服服务质量进行多维度量化评分。

### 1.2 核心价值
- **自动化质检**：替代人工抽检，实现100%会话覆盖
- **多维度评估**：专业性、标准化、政策执行、转化能力四个维度
- **实时反馈**：分析完成后立即生成报告，支持人工矫正
- **知识沉淀**：通过矫正记录积累评分规则，形成企业知识库

### 1.3 使用场景
- 每日客服聊天记录批量分析（50-1000通/批次）
- 高风险会话快速识别（评分≤8分）
- 客服培训案例库建设
- 服务质量趋势监控

---

## 二、开发环境与系统版本

### 2.1 硬件环境

| 项目 | 配置 |
|------|------|
| **主机** | MacBook Air (Apple Silicon M-series) |
| **操作系统** | macOS 15.1 (Darwin 24.1.0 arm64) |
| **内存** | 16GB |
| **存储** | SSD |

### 2.2 软件环境

| 项目 | 版本/地址 | 备注 |
|------|------|------|
| **Python** | 3.14.2 | 主运行环境 |
| **OpenClaw** | 2026.4.9 | AI Agent运行框架 |
| **Node.js** | v24.14.0 | OpenClaw依赖 |
| **Shell** | zsh | 默认shell |
| **Github仓库** | https://github.com/mppyes-ai/cs-analyzer | 同步更新本地代码 |

### 2.3 Python依赖包

```bash
# 核心依赖
openai>=1.0.0          # OpenAI API客户端（用于Kimi调用）
pandas>=2.0.0          # 数据处理
sentence-transformers>=2.2.0  # Embedding模型
lancedb>=0.5.0         # 向量数据库
python-dotenv>=1.0.0   # 环境变量管理
streamlit>=1.28.0      # Web前端框架

# 其他依赖
numpy
requests
sqlite3
asyncio
aiohttp
```

### 2.4 外部服务版本

| 服务 | 版本/配置 | 用途 |
|------|----------|------|
| **Kimi API** | kimi-k2.5 | 主评分大模型 |
| **Ollama** | 最新版 | 本地模型服务 |
| **Ollama模型** | qwen2.5:1.5b | 场景分类（适合8GB内存Mac） |
| **飞书API** | OpenAPI v3 | 进度通知推送 |

**注意**：
- qwen2.5:7b 需要16GB+内存，当前环境使用1.5b版本
- 所有API密钥存储在 `.env` 文件中，不提交到GitHub

---

## 三、系统架构总览

### 3.1 整体架构图

```
┌─────────────────────────────────────────────────────────────────┐
│                        CS-Analyzer 系统架构                      │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────┐     ┌──────────────┐     ┌──────────────┐   │
│  │   输入层     │────▶│   处理层     │────▶│   输出层     │   │
│  │              │     │              │     │              │   │
│  │ • 日志文件   │     │ • Worker     │     │ • Web界面    │   │
│  │ • 单条会话   │     │ • 评分器     │     │ • 飞书通知   │   │
│  │ • 批量提交   │     │ • 知识库     │     │ • 数据库     │   │
│  └──────────────┘     └──────────────┘     └──────────────┘   │
│                              │                                   │
│                         ┌────┴────┐                            │
│                         │  数据层  │                            │
│                         │         │                            │
│                         │ SQLite  │  结构化数据                 │
│                         │ LanceDB │  向量索引                   │
│                         └─────────┘                            │
└─────────────────────────────────────────────────────────────────┘
```

### 3.2 核心模块职责

| 模块 | 文件 | 职责 |
|------|------|------|
| **任务调度** | `batch_analyzer.py` | 接收分析请求，提交任务到队列 |
| **工作进程** | `worker.py` | 从队列取任务，驱动分析流程 |
| **智能评分** | `smart_scoring_v2.py` | 调用Kimi API进行四维度评分 |
| **意图分类** | `intent_classifier_v3.py` | 本地Ollama模型快速分类场景 |
| **知识检索** | `hybrid_retriever.py` | 向量检索+关键词检索混合 |
| **数据存储** | `db_utils.py` | SQLite数据库操作封装 |
| **前端界面** | `pages/6_🔍_*.py` | Streamlit可视化界面 |
| **进度通知** | `monitor_agent.py` | 飞书进度推送 |

### 3.3 数据流转

```
原始日志 ──▶ 解析会话 ──▶ 任务队列 ──▶ Worker处理
                                          │
                                          ▼
场景分类 ──▶ 规则检索 ──▶ LLM评分 ──▶ 结果存储
                                          │
                                          ▼
                                    Web展示/飞书通知
```

---

## 四、核心流程详解

### 4.1 完整分析流程

```
1. 提交阶段 (batch_analyzer.py)
   ├── 读取日志文件
   ├── 解析为会话对象
   ├── 检查重复（数据库已有则跳过）
   ├── 提交到任务队列 (task_queue.db)
   └── 启动Worker进程

2. 处理阶段 (worker.py)
   ├── 获取pending任务
   ├── 按user_id分组
   ├── 场景分类（售前/售中/售后）
   ├── 会话合并（30分钟内同用户会话）
   ├── 批量评分（5-50通/批，自适应）
   └── 保存结果到数据库

3. 评分阶段 (smart_scoring_v2.py)
   ├── 检索知识库规则
   ├── 构建评分Prompt
   ├── 调用Kimi API (kimi-k2.5)
   ├── 解析四维度评分结果
   └── 返回结构化数据

4. 输出阶段
   ├── 存储到cs_analyzer_new.db
   ├── 飞书推送进度通知
   └── Web界面可查看/矫正
```

### 4.2 关键算法

**自适应批量大小算法**
```python
def calculate_adaptive_batch_size(sessions, base_size):
    """根据Token估算动态调整批量大小"""
    total_tokens = sum(estimate_tokens(s) for s in sessions)
    optimal = total_tokens // TARGET_TOKENS_PER_SESSION
    return clamp(optimal, ADAPTIVE_BATCH_MIN, ADAPTIVE_BATCH_MAX)
```

**会话合并逻辑**
```python
# 同一用户 + 30分钟内 + 有消息关联 → 合并
if user_id == last_user_id and 
   time_diff < MERGE_WINDOW_MINUTES and
   has_message_overlap():
    merge_sessions()
```

---

## 五、数据模型

### 5.1 数据库设计

**主数据库**: `cs_analyzer_new.db`

| 表名 | 用途 |
|------|------|
| `sessions` | 会话分析结果（主表） |
| `corrections` | 人工矫正记录 |
| `rules` | 知识库规则（目前为空） |
| `analysis_runs` | 分析批次记录 |

**队列数据库**: `task_queue.db`

| 表名 | 用途 |
|------|------|
| `analysis_tasks` | 待处理任务队列 |

### 5.2 核心表结构

**sessions表**
```sql
session_id TEXT PRIMARY KEY,  -- 会话唯一ID
user_id TEXT,                 -- 用户ID
staff_name TEXT,              -- 客服名称
messages TEXT,                -- JSON格式消息列表
summary TEXT,                 -- 会话主题摘要
professionalism_score INT,    -- 专业性评分(1-5)
standardization_score INT,    -- 标准化评分(1-5)
policy_execution_score INT,   -- 政策执行评分(1-5)
conversion_score INT,         -- 转化能力评分(1-5)
total_score INT,              -- 总分(4-20)
analysis_json TEXT,           -- 完整分析结果JSON
is_transfer BOOLEAN,          -- 是否转接会话
related_sessions TEXT,        -- 关联会话列表
```

**analysis_tasks表**
```sql
task_id INTEGER PRIMARY KEY,
session_id TEXT,              -- 关联会话ID
status TEXT,                  -- pending/processing/completed/failed
scene TEXT,                   -- 场景分类（售前/售中/售后）
session_data TEXT,            -- 会话数据JSON
retry_count INT,              -- 重试次数
error TEXT,                   -- 错误信息
```

### 5.3 分析结果JSON结构

```json
{
  "session_analysis": {
    "theme": "会话主题",
    "user_intent": "用户意图",
    "user_sentiment": "用户情绪"
  },
  "dimension_scores": {
    "professionalism": {
      "score": 4,
      "reasoning": "评分理由",
      "evidence": ["证据1", "证据2"],
      "referenced_rules": []  // 命中的规则ID
    },
    "standardization": {...},
    "policy_execution": {...},
    "conversion": {...}
  },
  "summary": {
    "total_score": 16,
    "risk_level": "正常",
    "strengths": [...],
    "issues": [...],
    "suggestions": [...]
  }
}
```

---

## 六、技术栈

| 层级 | 技术 | 版本/配置 | 用途 |
|------|------|----------|------|
| **AI模型** | Kimi-k2.5 | - | 智能评分、质量分析 |
| **本地模型** | Ollama + qwen2.5:1.5b | - | 场景分类、意图识别 |
| **向量库** | LanceDB | ≥0.5.0 | 规则向量索引 |
| **数据库** | SQLite | - | 结构化数据存储 |
| **前端** | Streamlit | ≥1.28.0 | Web可视化界面 |
| **消息** | 飞书 Webhook | OpenAPI v3 | 进度通知推送 |
| **语言** | Python | 3.14.2 | 主要开发语言 |

---

## 七、配置文件

### 7.1 环境变量 (.env)

```bash
# API配置
MOONSHOT_API_KEY=sk-xxxxxxxxxxxxxxxx  # 替换为真实的Kimi API密钥
OLLAMA_HOST=http://localhost:11434   # Ollama服务地址
OLLAMA_MODEL=qwen2.5:1.5b           # 本地模型（适合8GB内存Mac）

# Worker配置
WORKER_MODE=async-batch              # 异步批量模式（推荐）
WORKER_MAX_GROUPS=6                  # 最大并发组数
WORKER_MAX_BATCH_SIZE=150            # 单次处理上限

# 自适应批量配置
BATCH_SCORE_SIZE=10                  # 基础批量评分大小
ADAPTIVE_BATCH_MIN=10                # 最小批量
ADAPTIVE_BATCH_MAX=5                 # 最大批量（安全限制）
MAX_TOKENS_PER_BATCH=200000          # Token安全上限

# Kimi并发配置
KIMI_MAX_CONCURRENT=90               # 最大并发请求数
KIMI_API_TIMEOUT=400                 # API超时时间(秒)
KIMI_MODEL=kimi-k2.5                 # 主评分大模型

# 会话合并
MERGE_WINDOW_MINUTES=30              # 会话合并窗口(分钟)

# 进度推送
PROGRESS_MIN_INTERVAL_SECONDS=5      # 最小推送间隔(秒)
```

### 7.2 关键常量

```python
MAX_TOKENS_PER_SESSION = 8000        # 单会话Token上限
TOKENS_PER_CHAR = 0.67               # 中文字符Token估算
OUTPUT_TOKENS_PER_SESSION = 600      # 输出Token预算
SYSTEM_PROMPT_TOKENS = 900           # 系统Prompt Token
```

---

## 八、运行方式

### 8.1 后台分析（推荐）

```bash
# 批量分析日志文件
python3.14 cs_analyzer_batch.py "/path/to/chat.log" --mode background

# Worker会自动启动，分析完成后自动退出
```

### 8.2 前台分析

```bash
# 实时查看日志输出
python3.14 cs_analyzer_batch.py "/path/to/chat.log" --mode foreground
```

### 8.3 单独启动Worker

```bash
# 异步批量模式（推荐）
python3.14 worker.py --async-batch --once

# 参数说明
--once              # 处理完当前队列后退出
--async-batch       # 异步批量模式
--max-groups=6      # 最大并发组数
--max-batch-size=150 # 单次处理上限
```

### 8.4 启动Web界面

```bash
# 默认端口8501
streamlit run 🏠_主页.py

# 访问 http://localhost:8501
```

---

## 九、今日修复内容（2026-04-17）

### 9.1 问题1: Worker --once 模式分批处理 ⚠️ 已修复

**问题**: `--once`模式下Worker只处理部分任务(受max_batch_size=150限制)就退出，导致大文件需要多次重启

**修复**: `worker.py`
- `fetch_and_group_tasks()`新增`once`参数
- 当`once=True`时取全部任务，不受`max_batch_size`限制

**代码**:
```python
def fetch_and_group_tasks(max_batch_size=150, once=False):
    if once:
        limit = total_pending  # 取全部
    elif total_pending <= max_batch_size:
        limit = total_pending
    else:
        limit = max_batch_size
```

### 9.2 问题2: 前端规则命中显示异常 ⚠️ 已修复

**问题**: 知识库为空时，前端仍显示"基于通用标准"提示

**修复**: `pages/6_🔍_会话分析与矫正.py`
- 增强`is_generic_reasoning`判断逻辑
- 确保`referenced_rules`为空列表时正确显示

### 9.3 问题3: 会话重复分析 ⚠️ 数据问题

**根因**: 原始日志文件中存在重复行，导致同一对话被分析多次

**结论**: 非系统Bug，建议预处理日志去重

---

## 十、已知问题与技术债务

| 问题 | 状态 | 影响 | 优先级 |
|------|------|------|--------|
| 知识库规则表为空 | 待解决 | 评分完全依赖LLM通用能力 | P1 |
| 消息重复推送 | 已修复 | 飞书通知偶尔重复 | - |
| 超时重试机制不完善 | 待优化 | 个别任务失败后需手动重置 | P2 |
| 前端展示细节 | 已修复 | 规则命中显示不准确 | - |

---

## 十一、项目文件结构

```
cs-analyzer/
├── 📄 核心模块
│   ├── worker.py                    # 工作进程（主引擎）
│   ├── batch_analyzer.py            # 批量分析入口
│   ├── smart_scoring_v2.py          # 智能评分器
│   ├── intent_classifier_v3.py      # 意图分类器
│   └── task_queue.py                # 任务队列管理
│
├── 📄 数据与存储
│   ├── db_utils.py                  # 数据库工具
│   ├── knowledge_base_v2.py         # 知识库管理
│   └── hybrid_retriever.py          # 混合检索器
│
├── 📄 监控与通知
│   ├── monitor_agent.py             # 监控代理
│   └── message_poller.py            # 消息轮询
│
├── 📂 pages/                        # Streamlit前端
│   ├── 🏠_主页.py
│   └── 6_🔍_会话分析与矫正.py
│
├── 📂 data/                         # 数据文件
│   ├── cs_analyzer_new.db          # 分析结果数据库
│   └── task_queue.db               # 任务队列数据库
│
├── 📂 docs/                         # 文档目录
│   └── CS-ANALYZER-SYSTEM-OVERVIEW.md  # 本文件
│
└── 📄 配置与文档
    ├── .env                         # 环境变量
    └── SKILL.md                     # 技能说明
```

---

## 十二、后续规划

### 12.1 短期（1-2周）
- 向知识库导入评分规则数据
- 完善异常重试机制
- 添加更多单元测试

### 12.2 中期（1个月）
- 规则提取自动化
- 评分质量A/B测试框架
- 多场景规则差异化评分

### 12.3 长期（3个月+）
- 实时分析能力
- 多品牌支持
- 预测性质量预警

---

## 十三、协作说明

### 13.1 开发团队
- **主力开发**: 小虾米（AI Agent）- 代码实现、Bug修复
- **顾问审查**: Claude Opus 4.6 - 架构建议、代码审查
- **产品负责人**: 金璐 - 需求定义、验收确认

### 13.2 协作流程
```
需求提出 → 小虾米开发 → Opus 4.6审查 → 小虾米修改 → 金璐验收
```

### 13.3 审查重点
- 架构合理性
- 异常处理完整性
- 代码规范与可维护性
- 性能瓶颈识别

---

## 附录：快速参考

### 常用命令

```bash
# 分析日志
python3.14 cs_analyzer_batch.py "/path/to/chat.log" --mode background

# 查看队列状态
python3.14 -c "from task_queue import get_queue_stats; print(get_queue_stats())"

# 启动Web界面
streamlit run 🏠_主页.py

# 手动启动Worker
python3.14 worker.py --async-batch --once --max-groups=6

# 检查Worker状态
ps auxww | grep "worker.py" | grep -v grep
```

### 关键文件位置

```
~/openclaw/workspace/skills/cs-analyzer/
├── worker.py              # 主工作进程
├── docs/                  # 文档目录
├── data/                  # 数据库文件
│   ├── cs_analyzer_new.db
│   └── task_queue.db
└── pages/                 # Streamlit前端
```

---

**文档版本**: 2026-04-17 v1.1  
**最后更新**: 2026-04-17 19:10  
**维护者**: 小虾米