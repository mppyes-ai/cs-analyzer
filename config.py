"""CS-Analyzer 全局配置 - v3.0 本地化统一版

所有 LLM 调用统一走 LM Studio 本地服务（OpenAI 兼容 API）。
支持通过 LLM_PROVIDER 环境变量切换回云端模式。

环境变量覆盖（生产环境推荐）:
    export LLM_PROVIDER=local                  # local | moonshot
    export LLM_BASE_URL=http://localhost:1234/v1
    export SCORING_MODEL=qwen3.6-35b-a3b       # 评分/规则提取模型
    export INTENT_MODEL=qwen2.5-7b             # 意图分类模型（可选独立）
"""

import os
from pathlib import Path

# ========== 基础路径 ==========
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "cs_analyzer_new.db"
DATA_DIR.mkdir(exist_ok=True)

# ========== LLM Provider 配置 ==========
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "local")  # local | moonshot

if LLM_PROVIDER == "local":
    # 本地 LM Studio 统一配置
    LLM_CONFIG = {
        "base_url": os.getenv("LLM_BASE_URL", "http://localhost:1234/v1"),
        "api_key": "not-needed",  # LM Studio 不需要 API key
        "scoring_model": os.getenv("SCORING_MODEL", "qwen3.6-35b-a3b@4bit"),
        "intent_model": os.getenv("INTENT_MODEL", "qwen2.5-7b"),
        "temperature": 0.1,
        "max_tokens": int(os.getenv("LLM_MAX_TOKENS", "64000")),
        "timeout": int(os.getenv("LLM_TIMEOUT", "1200")),  # 本地大模型需要更长超时
        "extra_params": {"chat_template_kwargs": {"enable_thinking": False}},
    }
else:
    # 云端 Moonshot 回退配置
    def _get_moonshot_api_key():
        api_key = os.getenv("MOONSHOT_API_KEY")
        if api_key:
            return api_key
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

    LLM_CONFIG = {
        "base_url": os.getenv("MOONSHOT_BASE_URL", "https://api.moonshot.cn/v1"),
        "api_key": _get_moonshot_api_key(),
        "scoring_model": os.getenv("KIMI_MODEL", "kimi-k2.5"),
        "intent_model": os.getenv("KIMI_MODEL", "kimi-k2.5"),
        "temperature": 1,
        "max_tokens": int(os.getenv("LLM_MAX_TOKENS", "20000")),
        "timeout": int(os.getenv("LLM_TIMEOUT", "300")),
        "extra_params": {},
    }

# ========== 向后兼容配置 ==========
OLLAMA_CONFIG = {
    "model": LLM_CONFIG["intent_model"],
    "url": LLM_CONFIG["base_url"].replace("/v1", ""),
    "timeout": LLM_CONFIG["timeout"],
    "max_retries": 3,
}

MOONSHOT_CONFIG = {
    "api_key": LLM_CONFIG["api_key"],
    "base_url": LLM_CONFIG["base_url"],
    "model": LLM_CONFIG["scoring_model"],
    "temperature": LLM_CONFIG["temperature"],
    "max_tokens": LLM_CONFIG["max_tokens"],
}

# ========== 模型版本配置 ==========
MODEL_CONFIG = {
    "intent_classifier": LLM_CONFIG["intent_model"],
    "scoring_engine": LLM_CONFIG["scoring_model"],
    "rule_extractor": LLM_CONFIG["scoring_model"],
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
    
    # 1. 检查 LLM 服务
    try:
        import requests
        # 检查评分模型
        r = requests.get(f"{LLM_CONFIG['base_url']}/models", timeout=5)
        if r.status_code == 200:
            models = [m.get('id', '') for m in r.json().get('data', [])]
            if LLM_CONFIG['scoring_model'] not in models:
                errors.append(f"评分模型未找到: {LLM_CONFIG['scoring_model']}")
            if LLM_CONFIG['intent_model'] not in models:
                errors.append(f"意图模型未找到: {LLM_CONFIG['intent_model']}")
        else:
            errors.append(f"LLM 服务异常: HTTP {r.status_code}")
    except Exception as e:
        errors.append(f"LLM 服务连接失败 ({LLM_CONFIG['base_url']}): {e}")
    
    # 2. 检查数据库目录
    if not DATA_DIR.exists():
        errors.append(f"数据目录不存在: {DATA_DIR}")
    
    return len(errors) == 0, errors


def print_config():
    """打印当前配置（用于调试）"""
    print("=" * 60)
    print(f"CS-Analyzer 配置 (Provider: {LLM_PROVIDER})")
    print("=" * 60)
    print(f"\n【评分引擎】")
    print(f"  模型: {LLM_CONFIG['scoring_model']}")
    print(f"  URL: {LLM_CONFIG['base_url']}")
    print(f"  超时: {LLM_CONFIG['timeout']}s")
    print(f"\n【意图分类】")
    print(f"  模型: {LLM_CONFIG['intent_model']}")
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
