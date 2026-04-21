#!/usr/bin/env python3.14
"""Worker 共享配置与全局状态

所有子模块通过 `import worker_config as cfg` 访问共享状态。

⚠️ 重要：不要直接 `from worker_config import scorer`，
这会在导入时创建一个局部引用，后续赋值不会同步。
正确做法：始终通过模块属性访问，如 `cfg.scorer`。

作者: 小虾米
更新: 2026-04-21（v2.6.6: worker.py 拆分 Step 1）
"""

import os
import threading
from dotenv import load_dotenv

load_dotenv()

# ========== v2.6 Phase 2: 自适应批量配置 ==========
TOKENS_PER_CHAR = float(os.getenv('TOKENS_PER_CHAR', '0.67'))
OUTPUT_TOKENS_PER_SESSION = int(os.getenv('OUTPUT_TOKENS_PER_SESSION', '600'))
SYSTEM_PROMPT_TOKENS = int(os.getenv('SYSTEM_PROMPT_TOKENS', '900'))
MAX_TOKENS_PER_BATCH = int(os.getenv('MAX_TOKENS_PER_BATCH', '300000'))
ADAPTIVE_BATCH_MIN = int(os.getenv('ADAPTIVE_BATCH_MIN', '3'))
ADAPTIVE_BATCH_MAX = int(os.getenv('ADAPTIVE_BATCH_MAX', '5'))
MAX_TOKENS_PER_TASK = 10000  # 单任务Token上限

# ========== 全局运行参数 ==========
MERGE_WINDOW_MINUTES = int(os.getenv('MERGE_WINDOW_MINUTES', '0'))
MAX_WORKERS = 3
BATCH_SCORE_SIZE = int(os.getenv('BATCH_SCORE_SIZE', '10'))
KIMI_MAX_CONCURRENT = int(os.getenv('KIMI_MAX_CONCURRENT', '90'))

# ========== 日志目录配置 ==========
LOGS_DIR = os.path.join(os.path.dirname(__file__), 'logs')
os.makedirs(LOGS_DIR, exist_ok=True)
PID_FILE = os.path.join(LOGS_DIR, 'cs_analyzer_worker.pid')

# ========== 全局可变状态（通过模块属性共享）==========
running = True
classifier = None
scorer = None
kimi_semaphore = None
db_lock = threading.Lock()


def estimate_session_tokens(session_data: dict) -> int:
    """估算单通会话的Token数（包含所有开销）
    
    Args:
        session_data: 会话数据字典，包含 messages
        
    Returns:
        估算的Token总数
    """
    messages = session_data.get('messages', [])
    # 会话内容字符数
    content_chars = sum(len(m.get('content', '')) for m in messages)
    # 转换为token + system prompt分摊 + output
    content_tokens = int(content_chars * TOKENS_PER_CHAR)
    # 加上输出开销（评分结果JSON）
    return SYSTEM_PROMPT_TOKENS + content_tokens + OUTPUT_TOKENS_PER_SESSION


def calculate_adaptive_batch_size(sessions: list, base_size: int = 30) -> int:
    """计算自适应批量大小
    
    策略：
    1. 先按base_size估算总token
    2. 如果超过MAX_TOKENS_PER_BATCH，按比例缩减
    3. 始终在[ADAPTIVE_BATCH_MIN, ADAPTIVE_BATCH_MAX]范围内
    
    重要：本函数只负责计算批量大小，不处理超长会话拆分
    超长会话(>100条消息)应在调用本函数前被拆分出去
    
    Args:
        sessions: 会话列表
        base_size: 基础批量大小
        
    Returns:
        优化后的批量大小
    """
    if not sessions:
        return 0
    
    # 估算base_size的token
    base_tokens = sum(estimate_session_tokens(s) for s in sessions[:base_size])
    
    if base_tokens > MAX_TOKENS_PER_BATCH:
        # 超出上限，按比例缩减
        ratio = MAX_TOKENS_PER_BATCH / base_tokens
        adjusted_size = int(base_size * ratio * 0.9)  # 留10%buffer
        result = max(adjusted_size, ADAPTIVE_BATCH_MIN)
        print(f"   📦 BATCH_DECISION|sessions={len(sessions)}|estimated_tokens={base_tokens}|batch_size={result}|reason=超出token上限({MAX_TOKENS_PER_BATCH})")
        return result
    
    # 计算平均单通token
    avg_tokens = base_tokens / max(base_size, 1)
    
    # 如果平均token较低，尝试扩大批量，但不超过ADAPTIVE_BATCH_MAX
    if avg_tokens < 3000:  # 短会话
        potential_size = int(MAX_TOKENS_PER_BATCH / avg_tokens * 0.9)
        result = min(potential_size, ADAPTIVE_BATCH_MAX, len(sessions))
        print(f"   📦 BATCH_DECISION|sessions={len(sessions)}|estimated_tokens={base_tokens}|batch_size={result}|reason=短会话优化(avg={avg_tokens:.0f}tokens)")
        return result
    
    # 默认使用base_size，但不超过ADAPTIVE_BATCH_MAX
    result = min(base_size, ADAPTIVE_BATCH_MAX, len(sessions))
    print(f"   📦 BATCH_DECISION|sessions={len(sessions)}|estimated_tokens={base_tokens}|batch_size={result}|reason=默认策略")
    return result
