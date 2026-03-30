"""CS-Analyzer 全局配置 - 单点配置源

所有可变参数集中在此，避免多处硬编码导致的不一致。

使用方式:
    from config import OLLAMA_CONFIG, DB_PATH, MODEL_CONFIG
    model = OLLAMA_CONFIG["model"]

环境变量覆盖（生产环境推荐）:
    export OLLAMA_MODEL=qwen2.5:7b
    export OLLAMA_URL=http://localhost:11434
    export MOONSHOT_API_KEY=your_key_here
"""

import os
from pathlib import Path

# ========== 基础路径 ==========
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "cs_analyzer_new.db"

# 确保数据目录存在
DATA_DIR.mkdir(exist_ok=True)

# ========== Ollama 配置 ==========
OLLAMA_CONFIG = {
    "model": os.getenv("OLLAMA_MODEL", "qwen2.5:7b"),
    "url": os.getenv("OLLAMA_URL", "http://localhost:11434"),
    "timeout": int(os.getenv("OLLAMA_TIMEOUT", "30")),
    "max_retries": int(os.getenv("OLLAMA_MAX_RETRIES", "3")),
}

# ========== Moonshot API 配置 ==========
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

MOONSHOT_CONFIG = {
    "api_key": get_moonshot_api_key(),
    "base_url": os.getenv("MOONSHOT_BASE_URL", "https://api.moonshot.cn/v1"),
    "model": os.getenv("MOONSHOT_MODEL", "kimi-k2.5"),
    "temperature": 1,  # Kimi 2.5 只支持 temperature=1
    "max_tokens": int(os.getenv("MOONSHOT_MAX_TOKENS", "2000")),
}

# ========== 模型版本配置 ==========
MODEL_CONFIG = {
    "intent_classifier": OLLAMA_CONFIG["model"],  # 意图分类使用 Ollama 本地模型
    "scoring_engine": MOONSHOT_CONFIG["model"],   # 评分引擎使用 Kimi API
    "rule_extractor": MOONSHOT_CONFIG["model"],   # 规则提取使用 Kimi API
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
    
    # 1. 检查 Ollama 服务
    try:
        import requests
        r = requests.get(f"{OLLAMA_CONFIG['url']}/api/tags", timeout=5)
        if r.status_code == 200:
            models = [m['name'] for m in r.json().get('models', [])]
            if OLLAMA_CONFIG['model'] not in models:
                errors.append(f"Ollama 模型未找到: {OLLAMA_CONFIG['model']}")
        else:
            errors.append(f"Ollama 服务异常: HTTP {r.status_code}")
    except Exception as e:
        errors.append(f"Ollama 连接失败: {e}")
    
    # 2. 检查 Moonshot API Key
    if not MOONSHOT_CONFIG["api_key"]:
        errors.append("Moonshot API Key 未设置")
    
    # 3. 检查数据库目录
    if not DATA_DIR.exists():
        errors.append(f"数据目录不存在: {DATA_DIR}")
    
    return len(errors) == 0, errors


def print_config():
    """打印当前配置（用于调试）"""
    print("=" * 60)
    print("CS-Analyzer 配置")
    print("=" * 60)
    print(f"\n【Ollama】")
    print(f"  模型: {OLLAMA_CONFIG['model']}")
    print(f"  URL: {OLLAMA_CONFIG['url']}")
    print(f"  超时: {OLLAMA_CONFIG['timeout']}s")
    print(f"\n【Moonshot】")
    print(f"  模型: {MOONSHOT_CONFIG['model']}")
    print(f"  URL: {MOONSHOT_CONFIG['base_url']}")
    print(f"  API Key: {'已设置' if MOONSHOT_CONFIG['api_key'] else '未设置'}")
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
