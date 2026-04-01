# CS-Analyzer 客服会话质量分析系统 v2.3

**基于结构化规则知识库的智能质检平台**

## 版本信息
- **当前版本**: v2.3
- **更新日期**: 2026-04-01
- **主要更新**: 后台分析模式 + PID锁机制 + 全量动态配置

---

## 核心架构 v2.3

### 三层架构
```
用户指令
    ↓
主会话（5秒快速提交）
    ├── 解析日志
    ├── 检查/启动Worker
    ├── 批量提交任务（幂等性检查）
    ├── 启动子代理（后台监控）
    └── 立即返回："后台分析中..."
    
子代理（后台轮询，无限超时）
    ├── 每30秒轮询队列
    ├── 每10%推送进度到飞书
    ├── 4小时自我保护超时
    └── 完成后推送完整报告

Worker（本地后台，分组并行）
    ├── 4组并发（可配置）
    ├── 组内串行（避免API限流）
    └── PID文件锁（自动清理残留）
```

### 混合双擎架构
```
SQLite（主数据层）          LanceDB（向量索引层）
├── 结构化规则              ├── 复合文本向量
├── 审核状态                ├── 语义检索
├── 版本历史                └── metadata过滤
└── 分析记录
```

---

## 快速开始

### 1. 环境配置

创建 `.env` 文件：

```bash
# Kimi API配置
MOONSHOT_API_KEY=sk-your-key-here
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=qwen2.5:7b
KIMI_MAX_TOKENS=16000

# Worker模式: serial | parallel | grouped
WORKER_MODE=grouped

# Worker并发配置
WORKER_MAX_GROUPS=4
WORKER_MAX_WORKERS=3
WORKER_BATCH_SIZE=50
WORKER_POLL_INTERVAL=2.0

# 会话合并窗口（分钟）
MERGE_WINDOW_MINUTES=30

# 子代理自我保护超时（分钟），0表示无限
MONITOR_SELF_TIMEOUT_MINUTES=240

# 进度推送间隔（百分比）
PROGRESS_INTERVAL_PERCENT=10

# 进度推送最小间隔（秒）
PROGRESS_MIN_INTERVAL_SECONDS=60
```

### 2. 后台分析（推荐）

```bash
cd ~/.openclaw/workspace/skills/cs-analyzer
python cs_analyzer_batch.py /path/to/logfile.log
```

**流程：**
1. 主会话5秒内完成提交
2. 子代理后台启动（无限超时）
3. 每10%进度推送到飞书
4. 完成后推送完整报告

### 3. 前台分析（阻塞模式）

```bash
python cs_analyzer_batch.py /path/to/logfile.log --foreground
```

---

## 核心模块

### 新增模块 v2.3

| 文件 | 功能 |
|------|------|
| `cs_analyzer_batch.py` | 批量分析入口脚本 |
| `batch_analyzer.py` | 批量分析控制器（前台/后台模式） |
| `monitor_agent.py` | 后台监控子代理（无限超时轮询） |

### 核心模块

| 文件 | 功能 |
|------|------|
| `worker.py` | 异步工作进程（分组并行） |
| `intent_classifier_v3.py` | 漏斗式意图分类器 |
| `smart_scoring_v2.py` | CoT评分引擎 |
| `hybrid_retriever.py` | 混合检索（全文+向量+RRF） |
| `rule_extractor_v2.py` | 结构化规则提取 |

---

## 配置说明

### Worker模式选择

| 模式 | 适用场景 | 命令 |
|------|---------|------|
| `serial` | 小规模，简单 | `--serial` |
| `parallel` | 中规模，快速 | `--parallel` |
| `grouped` | 大规模，推荐 | `--grouped`（默认） |

### 动态配置项

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `WORKER_MAX_GROUPS` | 4 | 分组数（大规模可增至8） |
| `WORKER_BATCH_SIZE` | 50 | 每批任务数 |
| `MONITOR_SELF_TIMEOUT_MINUTES` | 240 | 子代理自我保护超时 |
| `PROGRESS_INTERVAL_PERCENT` | 10 | 进度推送间隔 |

---

## 4维度评分体系

| 维度 | 5分标准 | 1分标准 |
|------|---------|---------|
| 专业性 | 参数准确、解释清晰 | 错误或无法回答 |
| 标准化 | 礼貌用语、响应及时 | 无礼貌、响应慢 |
| 政策执行 | 政策传达准确完整 | 政策错误或遗漏 |
| 转化能力 | 主动挖掘需求、成功引导 | 无引导、用户流失 |

---

## 关键技术特性

### 1. 幂等性保证
- 提交前检查 `session_id` 是否已分析
- 避免重复分析同一会话

### 2. PID文件锁
- 使用 `/tmp/cs_analyzer_worker.pid`
- 自动检测并清理残留锁
- 防止多Worker实例冲突

### 3. 进度追踪
- 子代理每30秒轮询队列
- 每10%推送进度到飞书
- 最小间隔60秒（避免刷屏）

### 4. 自我保护机制
- 子代理4小时自动退出
- Worker单例锁保护
- 失败任务自动重试（最多3次）

---

## 命令行工具

### 批量分析
```bash
# 后台模式
python cs_analyzer_batch.py <log_file>

# 前台模式
python cs_analyzer_batch.py <log_file> --foreground
```

### Worker管理
```bash
# 启动Worker（分组并行）
python worker.py --grouped --once

# 启动Worker（指定组数）
python worker.py --grouped --max-groups 8 --batch-size 100

# 强制使用串行模式
python worker.py --serial
```

### 其他工具
```bash
# 意图分类测试
python intent_classifier_v3.py

# 混合检索测试
python hybrid_retriever.py

# Golden Set管理
python golden_set_manager.py mae
```

---

## 故障排查

### Worker无法启动
```bash
# 检查PID文件
 cat /tmp/cs_analyzer_worker.pid

# 强制清理残留锁
 rm -f /tmp/cs_analyzer_worker.pid

# 查看Worker日志
 tail -f /tmp/worker.log
```

### 子代理无响应
```bash
# 检查子代理日志
 tail -f /tmp/monitor.log

# 取消分析任务
echo "logfile_name" > /tmp/cs_analyzer_cancel
```

### API认证失败
- 检查 `.env` 中 `MOONSHOT_API_KEY` 是否正确
- 确认API Key未过期

---

## 版本历史

### v2.3 (2026-04-01) - 后台分析模式
- ✅ 新增后台分析模式（子代理无限超时轮询）
- ✅ PID文件锁机制（自动清理残留）
- ✅ 全量动态配置（.env环境变量）
- ✅ 进度主动推送到飞书
- ✅ 队列幂等性检查

### v2.2 (2026-04-01) - 锁机制修复
- ✅ 修复锁检查逻辑，区分EADDRINUSE与其他错误
- ✅ 修复结果保存，complete_task传入完整对象
- ✅ 新增cs_analyzer_runner.py标准执行脚本

### v2.1 (2026-04-01) - Worker稳定性
- ✅ Unix Socket单例锁机制
- ✅ 预热机制解决多线程竞争
- ✅ 类型检查修复session_data混淆

### v2.0 (2026-03-17) - 初始版本
- ✅ 核心架构完成
- ✅ 端到端测试7/7通过

---

## 参考文档

| 文档 | 内容 |
|------|------|
| `docs/rule-schema-v2.json` | 规则JSON Schema |
| `docs/prompt-rule-extraction.md` | 规则提取Prompt |
| `docs/batch_architecture.md` | 批量分析架构设计 |
| `references/rules.md` | 评分规则详解 |
| `references/database_schema.md` | 数据库表结构 |

---

## 状态

**当前版本**: v2.3  
**系统状态**: ✅ 后台分析模式就绪，生产可用  
**待优化**: Golden Set扩展、大规模压测、A/B评估
