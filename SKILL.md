# CS-Analyzer 客服会话质量分析系统 v2.6.4

**基于结构化规则知识库的智能质检平台**

自动分析客服聊天记录，输出四维度质检报告（专业性/标准化/政策执行/转化能力）。

---

## 🆕 v2.6.4 新特性

### 消息服务稳定性增强
- **PID文件自监控**：message_poller 写入PID文件，外部可检测存活状态
- **崩溃自动恢复**：检测服务崩溃后自动重启，确保消息不丢失
- **完成报告必达**：关键消息（完成报告等）失败重试3次
- **残留消息恢复**：崩溃时未发送的消息下次启动时自动恢复

---

## 🆕 v2.6.3 新特性

### Worker启动可靠性优化
- **启动前依赖检查**：自动检测必要依赖是否安装，缺失时明确提示
- **启动失败即时通知**：Worker启动失败时立即发送飞书通知，不再空等

---

## 🆕 v2.6.2 新特性

### 智能批量优化
- **智能感知队列数量**：先count pending总数，一次性取完（上限150）
- **限制批量大小**：最大20通/批，避免API返回截断
- **提升Token上限**：KIMI_MAX_TOKENS提升至32000

### 废弃配置项
- `WORKER_BATCH_SIZE` 已废弃，改用 `WORKER_MAX_BATCH_SIZE`（默认150）

---

## 快速开始

### 1. 环境配置

创建 `.env` 文件：

```bash
# Kimi API配置（必填）
MOONSHOT_API_KEY=sk-your-key-here

# Ollama配置（本地意图分类，可选）
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=qwen2.5:1.5b  # 8GB内存Mac推荐

# Worker模式: serial | parallel | grouped | async-batch（推荐）
WORKER_MODE=async-batch
WORKER_MAX_GROUPS=4
WORKER_BATCH_SIZE=20

# 【v2.5】异步批量配置
KIMI_MAX_CONCURRENT=5       # Kimi API最大并发数
BATCH_SCORE_SIZE=3          # 批量评分会话数（3-5通/批）

# 会话合并窗口（分钟）
MERGE_WINDOW_MINUTES=30
```

### 2. 分析日志（后台模式 - 推荐）

```bash
python cs_analyzer_batch.py /path/to/chat.log
```

**流程：**
1. 5秒内完成提交，立即返回状态
2. Worker后台分析（支持异步批量模式）
3. 完成后推送结果通知

### 3. 分析日志（前台模式 - 实时查看）

```bash
python cs_analyzer_batch.py /path/to/chat.log --foreground
```

**流程：**
1. 阻塞等待，实时显示进度
2. 完成后输出统计报告

---

## 工作模式说明

### Worker 处理模式（.env 配置）

| 模式 | 适用场景 | 特点 |
|------|---------|------|
| **async-batch（推荐）** | 大规模分析（100+会话） | 【v2.5】跨用户场景分组+快速分类+批量评分，性能最优 |
| **grouped** | 中等规模（50-100会话） | 按用户分组，组内串行、组间并行 |
| **parallel** | 小规模（<50会话） | 全并行，速度快 |
| **serial** | 调试/测试 | 单线程，最稳定 |

切换方式：修改 `.env` 中的 `WORKER_MODE` 或使用 `--async-batch/--grouped/--parallel/--serial` 参数。

### 执行模式（命令行参数）

**默认模式：后台模式（推荐）**

```bash
python cs_analyzer_batch.py /path/to/chat.log
```

- ✅ 5秒内返回启动状态
- ✅ 后台执行分析
- ✅ 飞书实时推送进度（10%、20%...100%）
- ✅ 完成后推送完整报告
- ✅ 可随时关闭终端

**前台模式（仅调试使用）**

```bash
python cs_analyzer_batch.py /path/to/chat.log --foreground
```

- ⚠️ 阻塞等待，占用当前终端
- ⚠️ 不接收飞书进度推送
- ⚠️ 仅用于小批量调试（<10会话）

**⚠️ 重要：不带 `--foreground` 参数时，系统默认使用后台模式。不要擅自添加该参数。**

---

## 结果查看

### 数据库文件

分析结果保存在 `data/cs_analyzer_new.db`：

```sql
-- 查看分析结果
SELECT session_id, staff_name, total_score, summary 
FROM sessions 
ORDER BY total_score ASC;

-- 查看低分会话（总分≤12）
SELECT * FROM sessions WHERE total_score <= 12;

-- 按客服统计平均分
SELECT staff_name, AVG(total_score) as avg_score 
FROM sessions GROUP BY staff_name;
```

### 评分维度

| 维度 | 5分标准 | 1分标准 |
|------|---------|---------|
| **专业性** | 参数准确、解释清晰 | 错误或无法回答 |
| **标准化** | 礼貌用语、响应及时 | 无礼貌、响应慢 |
| **政策执行** | 政策传达准确完整 | 政策错误或遗漏 |
| **转化能力** | 主动挖掘需求、成功引导 | 无引导、用户流失 |

**总分**：4-20分
- 🔴 高风险：≤8分
- 🟡 中风险：9-12分  
- 🟢 正常：≥13分

---

## 配置调优

### 批量评分大小

编辑 `.env`：

```bash
# 小批量（3通/批）：速度优先
BATCH_SCORE_SIZE=3

# 大批量（5通/批）：成本优先
BATCH_SCORE_SIZE=5
```

### Kimi API 并发

根据您的API限额调整：

```bash
# 保守（避免限流）
KIMI_MAX_CONCURRENT=3

# 标准（推荐）
KIMI_MAX_CONCURRENT=5

# 激进（高限额账号）
KIMI_MAX_CONCURRENT=10
```

---

## 故障排查

### Worker无法启动

```bash
# 检查是否有残留锁
ls -la /tmp/cs_analyzer_worker.pid

# 强制清理后重试
rm -f /tmp/cs_analyzer_worker.pid

# 查看Worker日志
tail -f /tmp/worker.log
```

### 分析卡住/无响应

```bash
# 检查队列状态
python -c "from task_queue import get_queue_stats; print(get_queue_stats())"

# 取消当前分析任务
echo "logfile_name" > /tmp/cs_analyzer_cancel
```

### API认证失败

- 检查 `.env` 中 `MOONSHOT_API_KEY` 是否正确
- 确认API Key未过期

### 结果为空/丢失

- 检查数据库文件 `data/cs_analyzer_new.db` 是否存在
- 使用SQLite浏览器查看表数据

---

## 命令速查

```bash
# 后台分析（推荐）- 使用async-batch模式
python cs_analyzer_batch.py chat.log

# 指定模式分析
python cs_analyzer_batch.py chat.log --async-batch
python cs_analyzer_batch.py chat.log --grouped
python cs_analyzer_batch.py chat.log --serial

# 前台分析（实时）
python cs_analyzer_batch.py chat.log --foreground

# 仅启动Worker（手动模式）
python worker.py --async-batch --once

# 查看队列统计
python -c "from task_queue import get_queue_stats; print(get_queue_stats())"
```

---

## 开发者模式

如需修复Bug、修改系统代码或了解内部架构，请说：**"进入开发者模式"**

**⚠️ 重要：接收到进入开发者模式指令时，必须立即完整读取根目录的 `SKILL_DEVELOPER.md`**

开发者文档包含：
- 完整的系统架构和模块依赖关系
- 已知Bug和禁区列表（修复前必须检查）
- 数据库结构和关键变量命名约定
- 代码修改流程和规范（diff格式、影响分析等）

---

## 版本信息

- **当前版本**: v2.6.4
- **更新日期**: 2026-04-05
- **主要更新**: 
  - 消息服务稳定性增强：PID监控 + 崩溃自动恢复 + 完成报告必达
  - Worker启动可靠性优化：启动前依赖检查 + 启动失败即时通知
  - 智能批量优化（v2.6.2）：智能感知队列、限制20通/批
  - 跨用户场景分组、API调用减少77%
