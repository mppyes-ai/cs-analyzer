"""CS-Analyzer 全局配置 - 单点配置源

所有可变参数集中在此，避免多处硬编码导致的不一致。

使用方式:
    from config import LLM_CONFIG, DB_PATH, MODEL_CONFIG
    model = LLM_CONFIG["model"]

环境变量覆盖（生产环境推荐）:
    export LLM_MODE=local  # 或 cloud
    export LOCAL_MODEL_URL=http://localhost:1234/v1
    export MOONSHOT_API_KEY=your_key_here
"""

import os
from pathlib import Path

# ========== 基础路径 ==========
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"
DB_PATH = DATA_DIR / "cs_analyzer_new.db"

# 确保数据目录和日志目录存在
DATA_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)

# ========== LLM 模式配置 ==========
# 支持两种模式: "cloud" (云端) 或 "local" (本地)
LLM_MODE = os.getenv("LLM_MODE", "cloud").lower()

# ========== 本地模型配置 (LM Studio) ==========
LOCAL_LLM_CONFIG = {
    "enabled": LLM_MODE == "local",
    "base_url": os.getenv("LOCAL_MODEL_URL", "http://localhost:1234/v1"),
    "model": os.getenv("LOCAL_MODEL", "qwen3.6-35b-a3b"),
    "temperature": float(os.getenv("LOCAL_TEMPERATURE", "0.1")),
    "max_tokens": int(os.getenv("LOCAL_MAX_TOKENS", "32000")),
    "timeout": int(os.getenv("LOCAL_TIMEOUT", "1200")),  # 本地模型可能需要更长时间
    "api_key": os.getenv("LOCAL_API_KEY", "not-needed"),  # LM Studio 通常不需要 API Key
}

# ========== 云端模型配置 (Moonshot) ==========
def get_moonshot_api_key():
    """获取 Moonshot API Key（环境变量优先）"""
    # 1. 环境变量
    api_key = os.getenv("MOONSHOT_API_KEY")
    if api_key:
        return api_key
    
    # 2. OpenClaw 配置文件
    config_path = Path.home() / ".openclaw" / "config.yaml"
    if config_path.exists():
        try:
            import yaml
            with open(config_path) as f:
                config = yaml.safe_load(f)
                return config.get("moonshot", {}).get("apiKey")
        except Exception:
            pass
    
    return None

CLOUD_LLM_CONFIG = {
    "enabled": LLM_MODE == "cloud",
    "api_key": get_moonshot_api_key(),
    "base_url": os.getenv("MOONSHOT_BASE_URL", "https://api.moonshot.cn/v1"),
    "model": os.getenv("MOONSHOT_MODEL", "kimi-k2.5"),
    "temperature": 1,  # Kimi 2.5 只支持 temperature=1
    "max_tokens": int(os.getenv("MOONSHOT_MAX_TOKENS", "2000")),
    "timeout": int(os.getenv("MOONSHOT_TIMEOUT", "400")),
}

# ========== 统一 LLM 配置接口 ==========
def get_llm_config():
    """获取当前激活的LLM配置
    
    Returns:
        dict: 包含当前模式的所有配置参数
    """
    if LLM_MODE == "local":
        return {
            "mode": "local",
            **LOCAL_LLM_CONFIG,
            "scoring_model": LOCAL_LLM_CONFIG["model"],
        }
    else:
        return {
            "mode": "cloud",
            **CLOUD_LLM_CONFIG,
            "scoring_model": CLOUD_LLM_CONFIG["model"],
        }

# 向后兼容的LLM_CONFIG
LLM_CONFIG = get_llm_config()

# ========== Ollama 配置 (已迁移到 LM Studio) ==========
# 注意：所有本地模型现已统一使用 LM Studio，不再使用 Ollama
OLLAMA_CONFIG = {
    "model": os.getenv("OLLAMA_MODEL", "qwen2.5-7b"),  # 默认使用 LM Studio 的 qwen2.5-7b
    "url": os.getenv("OLLAMA_URL", "http://localhost:1234/v1"),  # LM Studio API 地址
    "timeout": int(os.getenv("OLLAMA_TIMEOUT", "30")),
    "max_retries": int(os.getenv("OLLAMA_MAX_RETRIES", "3")),
}

# ========== 模型版本配置 ==========
MODEL_CONFIG = {
    "intent_classifier": OLLAMA_CONFIG["model"],  # 意图分类使用 LM Studio 的 qwen2.5-7b
    "scoring_engine": LLM_CONFIG["scoring_model"],   # 评分引擎使用配置的LLM
    "rule_extractor": LLM_CONFIG["scoring_model"],   # 规则提取使用配置的LLM
}

# ========== 评分维度配置 ==========
SCORING_DIMENSIONS = {
    "professionalism": {
        "name": "专业性",
        "description": "产品知识准确性",
        "max_score": 5,
    },
    "standardization": {
        "name": "标准化",
        "description": "服务规范遵守",
        "max_score": 5,
    },
    "policy_execution": {
        "name": "政策执行",
        "description": "促销/售后政策传达",
        "max_score": 5,
    },
    "conversion": {
        "name": "转化能力",
        "description": "销售引导能力",
        "max_score": 5,
    },
}

# ========== 会话合并配置 ==========
MERGE_CONFIG = {
    "window_minutes": int(os.getenv("MERGE_WINDOW_MINUTES", "30")),
    "enabled": os.getenv("MERGE_ENABLED", "true").lower() == "true",
}

# ========== 验证函数 ==========
def validate_config():
    """验证配置有效性
    
    Returns:
        (is_valid, errors)
    """
    errors = []
    
    # 1. 检查 LLM 配置
    if LLM_MODE == "local":
        # 检查本地模型连接
        try:
            import requests
            r = requests.get(f"{LOCAL_LLM_CONFIG['base_url']}/models", timeout=5)
            if r.status_code != 200:
                errors.append(f"本地模型服务异常: HTTP {r.status_code}")
        except Exception as e:
            errors.append(f"本地模型连接失败: {e}")
    else:
        # 检查 Moonshot API Key
        if not CLOUD_LLM_CONFIG["api_key"]:
            errors.append("Moonshot API Key 未设置")
    
    # 2. 检查 Ollama 服务 (已迁移到 LM Studio)
    try:
        import requests
        r = requests.get(f"{OLLAMA_CONFIG['url']}/models", timeout=5)
        if r.status_code == 200:
            models = [m['id'] for m in r.json().get('data', [])]
            if OLLAMA_CONFIG['model'] not in models:
                errors.append(f"LM Studio 模型未找到: {OLLAMA_CONFIG['model']}")
        else:
            errors.append(f"LM Studio 服务异常: HTTP {r.status_code}")
    except Exception as e:
        errors.append(f"LM Studio 连接失败: {e}")
    
    # 3. 检查数据库目录
    if not DATA_DIR.exists():
        errors.append(f"数据目录不存在: {DATA_DIR}")
    
    return len(errors) == 0, errors


def print_config():
    """打印当前配置（用于调试）"""
    print("=" * 60)
    print("CS-Analyzer 配置")
    print("=" * 60)
    print(f"\n【LLM 模式】")
    print(f"  当前模式: {LLM_MODE}")
    print(f"\n【本地模型 (LM Studio)】")
    print(f"  启用: {LOCAL_LLM_CONFIG['enabled']}")
    print(f"  模型: {LOCAL_LLM_CONFIG['model']}")
    print(f"  URL: {LOCAL_LLM_CONFIG['base_url']}")
    print(f"  超时: {LOCAL_LLM_CONFIG['timeout']}s")
    print(f"\n【云端模型 (Moonshot)】")
    print(f"  启用: {CLOUD_LLM_CONFIG['enabled']}")
    print(f"  模型: {CLOUD_LLM_CONFIG['model']}")
    print(f"  URL: {CLOUD_LLM_CONFIG['base_url']}")
    print(f"  API Key: {'已设置' if CLOUD_LLM_CONFIG['api_key'] else '未设置'}")
    print(f"\n【LM Studio (原Ollama)】")
    print(f"  模型: {OLLAMA_CONFIG['model']}")
    print(f"  URL: {OLLAMA_CONFIG['url']}")
    print(f"\n【路径】")
    print(f"  数据库: {DB_PATH}")
    print(f"\n【会话合并】")
    print(f"  启用: {MERGE_CONFIG['enabled']}")
    print(f"  窗口: {MERGE_CONFIG['window_minutes']} 分钟")
    print("=" * 60)


if __name__ == "__main__":
    # 测试配置
    print_config()
    
    print("\n验证配置...")
    is_valid, errors = validate_config()
    if is_valid:
        print("✅ 配置验证通过")
    else:
        print("❌ 配置验证失败:")
        for e in errors:
            print(f"  - {e}")
