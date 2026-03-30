---
name: cs-analyzer
description: 客服会话质量分析系统 v2。基于结构化规则知识库的智能质检平台，支持规则自动提取、人工审核闭环、混合检索增强评分、CoT可解释输出。包含漏斗式意图分类（Qwen2.5:7b本地+规则）、版本化分析历史、Golden Set评估体系。
---

# 客服会话质量分析系统 v2 (CS Analyzer v2)

**基于结构化规则知识库的智能质检平台**

## 核心架构 v2

### 混合双擎架构
```
SQLite（主数据层）          LanceDB（向量索引层）
├── 结构化规则              ├── 复合文本向量
├── 审核状态                ├── 语义检索
├── 版本历史                └── metadata过滤
└── 分析记录
```

### 三层知识系统
1. **事实知识层** - 产品参数、安装指南、售后政策
2. **评分规则层** - 场景规则、评分标准、扣分条件
3. **案例示例层** - 正例/反例、Few-shot案例

## 闭环流程

```
客服会话
    ↓
漏斗式意图分类（规则→Qwen3→关键词）
    ↓
混合检索规则（全文+向量+RRF融合）
    ↓
AI评分（Kimi 2.5 + CoT输出）
    ↓
人工矫正
    ↓
规则提取（结构化JSON草案）
    ↓
知识库审核（pending→approved）
    ↓
向量库同步 → 回流新会话评分
```

## 模型配置

| 模块 | 模型 | 位置 | 用途 |
|------|------|------|------|
| **意图分类** | Qwen2.5:7b + 规则 | 本地Ollama | 高频、省60-70%算力 |
| **规则提取** | Kimi 2.5 | 远程API | 结构化规则生成 |
| **智能评分** | Kimi 2.5 | 远程API | CoT推理、可解释输出 |
| **向量化** | paraphrase-multilingual | 本地 | 语义检索 |

## 核心模块

### v2新增模块

| 文件 | 功能 |
|------|------|
| `intent_classifier_v3.py` | 漏斗式意图分类器（三层架构） |
| `knowledge_base_v2.py` | 混合双擎存储（SQLite+LanceDB） |
| `rule_extractor_v2.py` | 结构化规则提取（Kimi 2.5） |
| `smart_scoring_v2.py` | CoT评分引擎（规则命中+判定过程） |
| `hybrid_retriever.py` | 混合检索（全文+向量+RRF） |
| `golden_set_manager.py` | Golden Set管理（标注+MAE计算） |
| `migrate_to_v2_versioned.py` | 版本化数据迁移 |
| `test_e2e.py` | 端到端测试 |

### 页面

- `pages/3_💬_会话明细_v2.py` - 带规则命中的详情页
- `pages/6_📚_规则审核_v2.py` - 结构化规则审核

## 快速开始

### 1. 环境检查
```bash
# 检查Ollama服务
curl http://localhost:11434/api/tags

# 运行端到端测试
python test_e2e.py
```

### 2. 配置API Key
```bash
# 编辑 .env 文件
MOONSHOT_API_KEY=sk-your-key-here
```

### 3. 提交矫正→提取规则
```bash
# 方式1：Streamlit前端（推荐）
streamlit run 🏠_主页.py

# 方式2：命令行直接处理
python rule_extractor_v2.py process <correction_id>  # 提取规则
```

### 4. 审核规则
```bash
# 启动审核页面
streamlit run 🏠_主页.py
# 访问「📚 规则审核_v2」页面
# 点击「通过并生效」
```

### 5. 评分测试

**⚠️ 重要：评分引擎异常处理规则**

评分引擎 (`smart_scoring_v2.py`) 在AI分析失败时会抛出 `ScoringError` 异常，**禁止静默回退**：
- API调用失败（网络错误、超时）
- 限流耗尽（429错误重试后仍失败）
- JSON解析失败

**正确用法（必须走队列）：**
```python
from task_queue import submit_task

# ✅ 正确：提交到队列，Worker异步处理
task_id = submit_task(session_id=session_id, session_data=session_data)
```

**Worker处理结果：**
- 成功：结果保存到数据库，任务标记为 `completed`
- 失败：记录错误原因，任务标记为 `failed`，**不入库任何数据**

```bash
python smart_scoring_v2.py  # 仅用于测试，生产环境请走队列
```

## 4维度评分体系（v2增强）

| 维度 | 5分标准 | 1分标准 | 检查点（Checkpoints） |
|------|---------|---------|---------------------|
| 专业性 | 参数准确、解释清晰 | 错误或无法回答 | 参数正确、解释完整、对比清晰 |
| 标准化 | 礼貌用语、响应及时 | 无礼貌、响应慢 | 首响≤30s、礼貌用语、结束规范 |
| 政策执行 | 政策传达准确完整 | 政策错误或遗漏 | 主动告知、完整传达、无遗漏 |
| 转化能力 | 主动挖掘需求、成功引导 | 无引导、用户流失 | 需求挖掘、产品推荐、催单动作 |

**v2增强**：
- CoT输出判定过程（基于checkpoints逐项检查）
- 引用规则ID，展示命中规则和证据片段
- 可解释性：质检员可查看"为什么扣这分"

## 数据模型

### 规则表（rules）
```sql
rule_id, rule_type
scene_category, scene_sub_category, scene_description
trigger_keywords, trigger_intent, trigger_mood, trigger_valid_from, trigger_valid_to
rule_dimension, rule_priority, rule_criteria, rule_score_guide (JSON)
examples (JSON), reasoning (JSON), tags (JSON)
status (pending/approved/rejected), version, created_at, approved_at
```

### 分析记录表（analysis_runs）
```sql
session_id, run_version
model_version, kb_version, prompt_version
4维度分数, risk_level
retrieved_rule_ids (JSON), pre_analysis (JSON)
is_active, run_at, latency_ms
```

## 命令行工具

### 意图分类测试
```bash
python intent_classifier_v3.py  # 性能测试+统计
```

### 数据迁移（版本化）
```bash
python migrate_to_v2_versioned.py migrate --limit 5   # 测试迁移
python migrate_to_v2_versioned.py migrate              # 批量迁移
python migrate_to_v2_versioned.py versions <session_id>  # 查看版本
python migrate_to_v2_versioned.py compare <session_id>   # 对比版本
```

### Golden Set管理
```bash
python golden_set_manager.py annotate --batch --limit 50  # 交互式标注
python golden_set_manager.py mae                          # 计算MAE
python golden_set_manager.py export                       # 导出JSON
```

### 混合检索测试
```bash
python hybrid_retriever.py  # 检索效果测试
```

## 依赖安装

```bash
# 核心依赖
pip install openai python-dotenv sentence-transformers transformers torch scikit-learn

# 数据库/向量库
pip install sqlite3 lancedb pandas pyarrow

# Streamlit前端
pip install streamlit
```

## 备份恢复

**创建备份**：
```bash
cd ~/.openclaw/skills
tar czf cs-analyzer-backup-$(date +%Y-%m-%d).tar.gz cs-analyzer/
```

**恢复备份**：
```bash
cd ~/.openclaw/skills
tar xzf cs-analyzer-backup-YYYY-MM-DD.tar.gz
```

## 最新备份

- **文件**: `cs-analyzer-backup-2026-03-17.tar.gz`
- **大小**: 103KB
- **位置**: `~/.openclaw/skills/`
- **内容**: 完整v2代码（含15个新模块）

## 参考文档

| 文档 | 内容 |
|------|------|
| `docs/rule-schema-v2.json` | 规则JSON Schema |
| `docs/prompt-rule-extraction.md` | 规则提取Prompt |
| `docs/ui-rule-review-form.md` | 审核表单原型 |
| `references/rules.md` | 评分规则详解 |
| `references/database_schema.md` | 数据库表结构 |

## 状态

**当前版本**: v2.0  
**系统状态**: ✅ 核心架构完成，可用状态  
**已验证**: 端到端测试 7/7 通过，第一条规则全流程跑通  
**待优化**: Golden Set扩展、A/B评估、高风险样本压测
