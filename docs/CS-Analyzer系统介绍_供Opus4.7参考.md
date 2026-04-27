# CS-Analyzer 系统介绍文档

## 一、系统设计背景

### 1.1 业务场景
CS-Analyzer（客服会话质量分析系统）是为璞康集团（POOK Group）林内品牌电商客服团队设计的智能质检系统。

**核心痛点**：
- 客服团队日均处理数千通会话，人工质检效率低下
- 缺乏标准化的评分体系，质检结果主观性强
- 无法及时发现高风险会话，导致客户投诉升级
- 传统质检只能覆盖5-10%的会话，存在大量盲区

### 1.2 设计目标
1. **自动化**：实现100%会话覆盖的自动质检
2. **标准化**：建立4维度评分体系（专业性、标准化、政策执行、转化能力）
3. **实时性**：批量处理大数据时保证输出质量的同时提升速度
4. **可解释性**：评分结果包含判定依据和引用规则

### 1.3 技术演进
| 版本 | 时间 | 核心特性 |
|------|------|----------|
| v1.0 | 2025-Q4 | 基础评分，云端Kimi API |
| v2.0 | 2026-01 | 规则知识库，向量检索 |
| v2.5 | 2026-03 | 本地模型支持（LM Studio） |
| v2.6 | 2026-04 | oMLX迁移，批量评分优化 |
| v2.6.2 | 2026-04 | 异步批量模式，Worker队列 |

---

## 二、系统架构

### 2.1 整体架构

```
┌─────────────────────────────────────────────┐
│              CS-Analyzer v2.6.2               │
├─────────────────────────────────────────────┤
│  入口层                                        │
│  ├─ cs_analyzer_batch.py    (批量分析入口)    │
│  ├─ batch_analyzer.py       (分析控制器)      │
│  └─ streamlit_app.py        (Web前端)         │
├─────────────────────────────────────────────┤
│  核心引擎                                      │
│  ├─ smart_scoring_v2.py     (智能评分引擎)    │
│  ├─ batch_scoring.py        (批量评分)        │
│  ├─ knowledge_base_v2.py    (规则知识库)    │
│  └─ hybrid_retriever.py     (混合检索)        │
├─────────────────────────────────────────────┤
│  预处理层                                      │
│  ├─ intent_classifier_v3.py (意图分类)        │
│  ├─ embedding_utils.py      (向量模型)        │
│  └─ parse_log.py            (日志解析)        │
├─────────────────────────────────────────────┤
│  基础设施                                      │
│  ├─ worker.py               (异步Worker)    │
│  ├─ task_queue.py           (任务队列)        │
│  ├─ db_utils.py             (数据库工具)      │
│  └─ message_poller.py       (消息推送)        │
├─────────────────────────────────────────────┤
│  外部服务                                      │
│  ├─ oMLX (本地LLM服务)      http://localhost:8000 │
│  ├─ Feishu (飞书推送)                          │
│  └─ SQLite (数据存储)                          │
└─────────────────────────────────────────────┘
```

### 2.2 核心模块详解

#### A. 智能评分引擎（smart_scoring_v2.py）

**职责**：对单通或批量会话进行4维度质量评分

**评分流程**：
```
1. 会话预分析
   ├─ 场景识别（售前/售中/售后/客诉）
   ├─ 意图识别（咨询/投诉/退款/维修）
   └─ 情绪识别（positive/neutral/negative/urgent）

2. 规则检索
   ├─ 元数据过滤（按场景获取已审批规则）
   ├─ 向量检索（LanceDB相似度搜索）
   └─ 混合检索（结合两者，top-5）

3. Prompt构建
   ├─ 评分标准（固定模板）
   ├─ 检索到的规则（动态）
   └─ 会话内容（动态）

4. LLM调用
   ├─ System prompt: "你是专业的客服质检专家..."
   └─ User prompt: 评分要求 + 规则 + 会话内容

5. 结果解析
   ├─ JSON解析（容错处理）
   ├─ 分数截断（1-5分范围）
   └─ 元数据补充（评分时间、模型、规则ID）
```

**评分维度**：
| 维度 | 权重 | 5分标准 | 1分标准 |
|------|------|---------|---------|
| 专业性 | 25% | 回答准确，超出预期 | 事实错误，误导用户 |
| 标准化 | 25% | 礼貌规范，响应及时 | 态度恶劣，答非所问 |
| 政策执行 | 25% | 政策传达准确及时 | 编造政策，推诿责任 |
| 转化能力 | 25% | 主动挖掘，成功转化 | 被动应答，错失机会 |

#### B. 批量评分（batch_scoring.py）

**职责**：高效处理大量会话的批量评分

**工作模式**：
```
async-batch模式（v2.6.2推荐）
├─ 会话分组（按场景或混合）
├─ 并发控制（KIMI_MAX_CONCURRENT信号量）
├─ 自适应批量（BATCH_SCORE_SIZE: 2-5通/批）
└─ 失败重试（单通降级评分）
```

**关键配置**：
```python
BATCH_SCORE_SIZE = 5          # 每批评分会话数
MAX_TOKENS_PER_BATCH = 8000  # Token安全上限
ADAPTIVE_BATCH_MIN = 2       # 最小批量
ADAPTIVE_BATCH_MAX = 5       # 最大批量
```

#### C. Worker系统（worker.py）

**职责**：异步处理评分任务队列

**架构**：
```
Worker进程
├─ 主循环：从SQLite队列获取pending任务
├─ 任务分组：按批次ID分组
├─ 并发执行：asyncio.gather同时处理多组
└─ 状态更新：completed / failed / retry
```

**并发模型**：
```python
WORKER_MAX_GROUPS = 10       # 同时处理的任务组数
WORKER_MAX_WORKERS = 3       # 每组的工作线程数
# 总并发 = 10 × 3 = 30
```

### 2.3 数据流

```
输入：客服聊天记录.log
  ↓
parse_log.py → 解析为结构化会话（session_id, messages, metadata）
  ↓
batch_analyzer.submit_sessions() → 写入SQLite任务队列
  ↓
worker.py → 从队列获取任务，调用smart_scoring_v2评分
  ↓
smart_scoring_v2 → 构建prompt，调用oMLX API
  ↓
oMLX → Qwen3.6-35B-A3B-4bit模型推理
  ↓
结果解析 → 写入SQLite结果数据库
  ↓
message_poller.py → 推送进度和报告到飞书
```

---

## 三、文件清单

### 3.1 核心代码文件（/skills/cs-analyzer/）

| 文件 | 行数 | 职责 | 关键类/函数 |
|------|------|------|------------|
| `cs_analyzer_batch.py` | 200+ | 批量分析入口 | `main()`, `run_analysis()` |
| `batch_analyzer.py` | 800+ | 分析控制器 | `BatchAnalyzer`, `submit_sessions()`, `run_background()` |
| `smart_scoring_v2.py` | 1200+ | 智能评分引擎 | `SmartScoringEngine`, `score_session()`, `score_sessions_batch_async()` |
| `batch_scoring.py` | 400+ | 批量评分 | `BatchScorer`, `_batch_score_with_limit_v2()` |
| `worker.py` | 600+ | 异步Worker | `run_async_batch_worker()`, `process_batch()` |
| `task_queue.py` | 300+ | 任务队列 | `create_task()`, `get_pending_tasks()`, `update_task_status()` |
| `db_utils.py` | 400+ | 数据库工具 | `init_database()`, `save_session()`, `get_stats()` |
| `knowledge_base_v2.py` | 500+ | 规则知识库 | `get_approved_rules()`, `search_rules_by_vector()` |
| `hybrid_retriever.py` | 300+ | 混合检索 | `HybridRuleRetriever` |
| `intent_classifier_v3.py` | 400+ | 意图分类 | `FunnelIntentClassifier`, `RobustIntentClassifier` |
| `embedding_utils.py` | 200+ | 向量模型 | `get_embedding_model()` |
| `parse_log.py` | 300+ | 日志解析 | `parse_log_file()`, `extract_sessions()` |
| `message_poller.py` | 400+ | 消息推送 | `MessagePoller`, `send_progress()`, `send_report()` |
| `monitor_agent.py` | 300+ | 监控代理 | `MonitorAgent`, `check_progress()` |
| `config.py` | 200+ | 配置管理 | `LLM_CONFIG`, `DB_PATH` |
| `test_tracker.py` | 300+ | 测试追踪 | `TestTracker`, `record_test()` |

### 3.2 配置文件

| 文件 | 职责 |
|------|------|
| `.env` | 环境变量（Worker并发、KIMI配置、本地模型URL） |
| `config.yaml` | oMLX桥接配置 |
| `data/task_queue.db` | SQLite任务队列数据库 |
| `data/cs_analyzer_new.db` | SQLite分析结果数据库 |

### 3.3 文档文件

| 文件 | 内容 |
|------|------|
| `docs/failure_analysis_report.md` | 失败分析报告 |
| `docs/local_model_failure_analysis_20260424.md` | 本地模型故障分析 |
| `docs/ollama_vs_lmstudio_evaluation.md` | Ollama vs LM Studio评估 |
| `docs/omlx_vs_lmstudio_evaluation.md` | oMLX vs LM Studio评估 |
| `docs/triple_diagnosis_final_report.md` | 三重诊断最终报告 |

---

## 四、关键配置参数

### 4.1 oMLX配置（~/.omlx/settings.json）

```json
{
  "scheduler": {
    "max_concurrent_requests": 32    // oMLX最大并发请求数
  },
  "cache": {
    "enabled": true,
    "ssd_cache_dir": "/Users/jinlu/.omlx/cache",
    "ssd_cache_max_size": "20%",      // 冷缓存（SSD）限制
    "hot_cache_max_size": "50%",      // 热缓存（内存）限制
    "initial_cache_blocks": 256
  }
}
```

### 4.2 CS-Analyzer配置（.env）

```bash
# Worker并发配置
WORKER_MAX_GROUPS=10              // 任务组数
WORKER_MAX_WORKERS=3              // 每组工作线程数
WORKER_MAX_BATCH_SIZE=150         // 最大批次大小

# 自适应批量配置
BATCH_SCORE_SIZE=5                // 每批评分会话数
MAX_TOKENS_PER_BATCH=8000         // Token安全上限
ADAPTIVE_BATCH_MIN=2              // 最小批量
ADAPTIVE_BATCH_MAX=5              // 最大批量

# Kimi API配置（本地模式复用为oMLX配置）
KIMI_MAX_CONCURRENT=90            // 评分API信号量
KIMI_API_TIMEOUT=400              // API超时（秒）
KIMI_RPM_LIMIT=450                // 每分钟请求限制

# 本地模型配置
LOCAL_MODEL_URL=http://localhost:8000/v1
LOCAL_MODEL=Qwen3.6-35B-A3B-4bit
LOCAL_API_KEY=1234567890
```

---

## 五、运行模式

### 5.1 前台模式
```bash
python3.14 cs_analyzer_batch.py "/path/to/chat.log" --foreground
```
- 阻塞等待，实时显示进度
- 适合调试和小批量测试

### 5.2 后台模式（推荐）
```bash
python3.14 cs_analyzer_batch.py "/path/to/chat.log"
```
- 启动Worker子进程，立即返回
- 监控代理自动推送进度到飞书
- 适合大批量生产环境

### 5.3 Web前端
```bash
streamlit run streamlit_app.py
```
- 查看历史分析结果
- 可视化统计图表
- 单通会话详情查看

---

## 六、外部依赖

### 6.1 oMLX服务
- **地址**：http://localhost:8000/v1
- **模型**：Qwen3.6-35B-A3B-4bit（20GB权重，MoE架构）
- **硬件**：M5 Max 128GB（614 GB/s内存带宽）
- **状态**：必须预先启动

### 6.2 飞书推送
- **用途**：进度通知、完成报告
- **配置**：FEISHU_CHAT_ID环境变量
- **频率**：每10%进度推送

---

## 七、常见问题

### Q1: Worker启动失败？
- 检查oMLX服务是否运行：`curl http://localhost:8000/v1/models`
- 检查依赖安装：`pip install -r requirements.txt`

### Q2: 评分结果为空？
- 检查模型是否加载：oMLX日志中查找"loaded model"
- 检查prompt是否超长：日志中查找"context length exceeded"

### Q3: 速度太慢？
- 参考oMLX优化测试报告调整配置
- 检查缓存设置：热缓存50%+冷缓存20%
- 避免并发数过高：oMLX 32并发最优

---

**文档版本**：v1.0
**更新日期**：2026-04-27
**作者**：小虾米（CS-Analyzer维护者）
