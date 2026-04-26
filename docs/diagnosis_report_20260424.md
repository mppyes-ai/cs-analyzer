# CS-Analyzer 系统诊断报告

**生成时间**: 2026-04-24 09:49  
**报告人**: 小虾米  
**目标**: 提供给 Opus 4.6 进行联合诊断

---

## 一、系统概述

CS-Analyzer 是一个基于大模型的客服会话智能分析系统，核心功能包括：
- **意图分类**: 识别客服会话场景（售前/售中/售后/客诉）
- **情绪分析**: 检测用户情绪状态
- **智能评分**: 4维度评分（专业性/标准化/政策执行/转化能力）
- **规则检索**: 基于向量检索匹配服务规范
- **报告生成**: 输出分析结果到飞书

### 当前架构

| 组件 | 技术栈 | 版本 |
|------|--------|------|
| 评分引擎 | smart_scoring_v2.py | v2.6.4 |
| 意图分类 | intent_classifier_v3.py | 漏斗式分类器 |
| 情绪分析 | sentiment_analyzer.py | 基于LLM |
| 向量检索 | knowledge_base_v2.py | LanceDB + 混合检索 |
| 任务队列 | task_queue.py | SQLite异步队列 |
| 工作进程 | worker.py | 异步批量模式 |

---

## 二、今日系统改造记录

### 2.1 大模型架构迁移（已完成）

**改造前**:
- 评分引擎: Ollama (qwen2.5-32b)
- 意图分类: Ollama (qwen2.5-7b)
- Embedding: HuggingFace (MiniLM-L12-v2, 384维)

**改造后**:
- 评分引擎: LM Studio (qwen3.6-35b-a3b@4bit)
- 意图分类: LM Studio (qwen2.5-7b)
- 情绪分析: LM Studio (qwen2.5-7b)
- Embedding: LM Studio (Qwen3-Embedding-4B, 2560维)

**改造文件**:
- `config.py`: 添加双模式LLM配置 (cloud/local)
- `smart_scoring_v2.py`: 替换 `_call_kimi_async` → `_call_llm_async`
- `embedding_utils.py`: 添加 `LMStudioEmbeddingModel` 适配器
- `knowledge_base_v2.py`: 向量维度 384 → 2560

### 2.2 双模式切换机制

```python
# 环境变量控制
LLM_MODE=local  # 或 cloud
LOCAL_MODEL=qwen3.6-35b-a3b@4bit
LOCAL_MODEL_URL=http://localhost:1234/v1
```

### 2.3 Embedding模型切换

| 模型 | 维度 | 来源 | 状态 |
|------|------|------|------|
| MiniLM-L12-v2 | 384 | HuggingFace | 已弃用 |
| nomic-embed-text | 768 | LM Studio | 已弃用 |
| **Qwen3-Embedding-4B** | **2560** | **LM Studio** | **当前使用** |

---

## 三、当前问题详细描述

### 3.1 核心问题: 评分结果JSON格式不稳定

**现象**:
- 批次1: 10/10 成功
- 批次2: 0/10 失败（评分无效）
- 批次3: 0/10 失败（评分无效）
- 批次4: 10/10 成功
- 批次5: 部分成功

**错误信息**:
```
⚠️ 任务 576... 评分无效: 评分结果不完整（缺少dimension_scores或summary）
⚠️ 任务 577... 评分无效: Invalid JSON
```

**根因分析**:
1. qwen3.6-35b-a3b@4bit 模型输出JSON格式不一致
2. 有时完整返回，有时缺少关键字段
3. 与并发批次数量无关（单批次也会失败）

### 3.2 次要问题: LM Studio 500错误

**现象**:
```
HTTP/1.1 500 Internal Server Error
```

**发生时机**:
- 多批次并发时更容易出现
- 长时间运行后出现
- 模型可能过载或崩溃

### 3.3 向量检索维度不匹配（已修复）

**修复前**:
```
lance error: query dim(768) doesn't match column vector dim(384)
```

**修复方案**:
- 重建LanceDB向量库为2560维
- 当前状态: 0条规则（需后续入库）

---

## 四、测试记录

### 4.1 端到端测试

**测试文件**: 客服聊天记录(50).log（50通会话）

**测试结果**:
| 轮次 | 成功 | 失败 | 状态 |
|------|------|------|------|
| 第1轮 | 10 | 0 | ✅ |
| 第2轮 | 5 | 5 | ⚠️ |
| 第3轮 | 10 | 0 | ✅ |
| 第4轮 | 0 | 10 | ❌ |
| 第5轮 | 10 | 0 | ✅ |
| 第6轮 | 0 | 10 | ❌ |
| 第7轮 | 5 | 5 | ⚠️ |
| 第8轮 | 0 | 10 | ❌ |

**结论**: 成功率约50%，JSON格式不稳定是主要问题

### 4.2 模型响应时间

| 批次大小 | 平均耗时 | 成功率 |
|----------|----------|--------|
| 10通 | 13-118秒 | 不稳定 |
| 5通 | 23-63秒 | 较稳定 |

---

## 五、环境信息

### 5.1 硬件

| 项目 | 配置 |
|------|------|
| 设备 | MacBook Pro |
| 芯片 | Apple Silicon (M系列) |
| 内存 | 36GB+ |
| 存储 | SSD |

### 5.2 软件

| 项目 | 版本 |
|------|------|
| macOS | 15.1 |
| Python | 3.14 |
| LM Studio | 0.4.12 |
| LanceDB | 2.0.0 |

### 5.3 当前加载模型

| 模型 | 量化 | 用途 |
|------|------|------|
| qwen3.6-35b-a3b@4bit | 4bit | 评分引擎 |
| qwen3.6-35b-a3b@8bit | 8bit | 备用 |
| qwen2.5-7b | 8bit | 意图分类 |
| Qwen3-Embedding-4B | - | Embedding |
| nomic-embed-text | - | 备用 |
| Jina retrieval | - | 不兼容 |
| gemma-4-31b | - | 未使用 |

---

## 六、需要Opus诊断的问题

### 6.1 优先级P0: JSON格式不稳定

**问题描述**:
qwen3.6-35b-a3b@4bit 在LM Studio中输出JSON格式不一致，导致评分解析失败。

**期望**:
- 分析是模型问题、提示词问题、还是解析逻辑问题
- 提供修复方案

### 6.2 优先级P1: 500 Internal Server Error

**问题描述**:
LM Studio在多批次并发时出现500错误。

**期望**:
- 分析是LM Studio限制、模型过载、还是配置问题
- 提供优化建议

### 6.3 优先级P2: 架构优化建议

**问题描述**:
当前双模式架构（云端/本地）是否需要调整。

**期望**:
- 评估当前架构合理性
- 提供长期优化建议

---

## 七、附件

### 7.1 关键代码片段

**评分引擎调用**:
```python
async def _call_llm_async(self, prompt, expected_count, pre_analyses):
    """调用LLM进行评分"""
    response = await client.chat.completions.create(
        model=self.model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
        max_tokens=8000,
        timeout=400
    )
    return self._parse_scoring_results(response.choices[0].message.content)
```

**JSON解析逻辑**:
```python
def _parse_scoring_results(self, content):
    """解析评分结果JSON"""
    try:
        data = json.loads(content)
        if "dimension_scores" not in data or "summary" not in data:
            raise ValueError("评分结果不完整")
        return data
    except Exception as e:
        logger.error(f"解析失败: {e}")
        return None
```

### 7.2 日志文件

- `logs/worker_20260424_092915.log` - 最新分析日志
- `logs/worker.log` - 历史日志

---

## 八、联系方式

**系统维护**: 小虾米  
**使用方**: 金璐（璞康集团）  
**协作诊断**: Opus 4.6

---

**报告结束**
