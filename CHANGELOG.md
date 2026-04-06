# CS-Analyzer 更新日志

## v2.5 (2026-04-04)

### 🚀 新特性

#### 1. API超时保护
- **同步API调用**：`score_session()` 添加120秒超时
- **异步API调用**：`_call_kimi_async()` 添加 `asyncio.timeout(120)`
- **超时处理**：超时后自动标记失败，触发重试机制
- **防止Worker僵死**：API调用hang住时不会永久阻塞

#### 2. Worker常驻模式支持
- **常驻运行**：不带 `--once` 参数，Worker永远运行
- **队列监听**：队列为空时睡眠2秒，不退出
- **实时响应**：新任务提交后立即处理（最多2秒延迟）
- **防止积压**：处理完一批后检查队列，有任务继续处理

### 📁 文件变更

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `smart_scoring_v2.py` | 修改 | 添加API超时保护（同步+异步） |
| `worker.py` | 优化 | 已支持常驻模式（不带--once） |
| `SKILL.md` | 更新 | 版本号同步至v2.5 |
| `SKILL_DEVELOPER.md` | 更新 | 添加API超时保护文档 |

### ⚙️ 配置变更

**启动方式**:
```bash
# 常驻模式（推荐用于高频分析）
python worker.py --async-batch

# 单次模式（推荐用于低频分析）
python worker.py --async-batch --once
```

### 🔧 使用建议

| 场景 | 推荐模式 | 内存占用 |
|------|---------|---------|
| 每天分析1-2次 | `--once` | 临时占用 |
| 随时可能提交 | 常驻模式 | ~700MB常驻 |
| 16GB+内存 | 常驻模式 | 无压力 |
| 8GB内存 | `--once` | 避免长期占用 |

### 🐛 修复问题

- **Worker僵死风险**：API调用超时保护，防止网络问题导致Worker永久阻塞
- **任务积压风险**：常驻模式下，新任务提交后立即处理，避免积压

### ⚠️ 注意事项

1. **常驻模式内存占用**：~700MB（Embedding模型450MB + 其他）
2. **API超时时间**：固定120秒，暂不可配置
3. **重试机制**：超时任务会自动重试（最多3次）

---

## v2.4 (2026-04-03)

### 🚀 新特性

#### 1. 异步批量模式（async-batch）
- **组间并行 + 组内异步 + 批量评分**：综合性能最优的工作模式
- **默认启用**：`.env` 中 `WORKER_MODE=async-batch`
- **并发控制**：`KIMI_MAX_CONCURRENT=5` 避免触发API限流
- **批量评分**：`BATCH_SCORE_SIZE=3` 减少API调用次数

#### 2. 性能提升
| 场景 | v2.3 | v2.4 | 提升 |
|------|------|------|------|
| 20通会话 | ~80s | ~15s | **5x** |
| API调用 | 20次 | 7次 | **3x** |

#### 3. 架构改进
- **smart_scoring_v2.py**: 新增异步批量评分方法
  - `score_sessions_batch_async()` - 批量评分入口
  - `_score_batch_same_scene()` - 同场景批量处理
  - `_call_kimi_async()` - 异步Kimi API调用
  - `_parse_batch_response()` - 批量响应解析

- **worker.py**: 新增异步批量Worker
  - `run_async_batch_worker()` - 异步批量主循环
  - `process_group_async()` - 异步组处理
  - `_batch_score_with_limit()` - 带限流的批量评分
  - `kimi_semaphore` - 全局并发控制信号量

- **batch_analyzer.py**: 支持启动async-batch Worker
  - 检测 `.env` 中的 `WORKER_MODE`
  - 自动传递 `--async-batch` 参数

### 📁 文件变更

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `smart_scoring_v2.py` | 新增 | 批量评分方法，异步API调用 |
| `worker.py` | 重构 | 新增async-batch模式， asyncio架构 |
| `batch_analyzer.py` | 修改 | 支持启动async-batch Worker |
| `.env` | 修改 | 默认启用async-batch，新增配置项 |
| `SKILL.md` | 更新 | v2.4使用文档 |
| `SKILL_DEVELOPER.md` | 更新 | v2.4开发者文档 |

### ⚙️ 配置变更

**新增配置项（`.env`）**:
```bash
# 工作模式改为async-batch（推荐）
WORKER_MODE=async-batch

# 【新增】异步批量配置
KIMI_MAX_CONCURRENT=5       # Kimi API最大并发数
BATCH_SCORE_SIZE=3          # 批量评分会话数（3-5通/批）
```

**默认变更**:
- `WORKER_MODE`: `grouped` → `async-batch`
- `WORKER_BATCH_SIZE`: `50` → `20`

### 🔧 使用方式

```bash
# 方式1: 默认使用v2.4异步批量模式
python cs_analyzer_batch.py chat.log

# 方式2: 手动启动Worker（async-batch模式）
python worker.py --async-batch --once

# 方式3: 切换回原有模式
WORKER_MODE=grouped python cs_analyzer_batch.py chat.log
```

### 🐛 修复问题

- 修复 `.env` 注释被解析的问题（去除注释中的中文）

### ⚠️ 注意事项

1. **需要 openai>=1.0** - 支持 AsyncOpenAI
2. **首次运行** - 建议先用小批量（5-10通）测试
3. **回滚方案** - 修改 `.env` 中 `WORKER_MODE=grouped` 可回退

---

## v2.3 (2026-04-02)

### 主要更新
- 分层文档架构（使用者/开发者分离）
- 修复 H-5 ~ H-10 已知Bug
- 预分组并行模式优化

---

## v2.2 (2026-04-01)

### 主要更新
- Worker模式锁机制修复（H-5）
- PID文件+进程存在性双重检测

---

## 历史版本

详见 `docs/bugs/index.md` 中的修复记录。
