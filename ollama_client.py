"""通用 LLM 客户端 - v3.0 本地化版

原 Ollama 专用客户端，重写为 OpenAI 兼容 API 客户端。
底层调用 LM Studio 的 /v1/chat/completions 端点。
保持原有接口（generate / extract_response / health_check）不变，上层代码无需修改。

作者: 小虾米
更新: 2026-04-22（v3.0: 统一走 LM Studio OpenAI 兼容 API）
"""

import json
import time
import logging
from typing import Dict, List, Optional, Callable
from dataclasses import dataclass
from datetime import datetime
from functools import wraps
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# 从集中配置导入
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from config import LLM_CONFIG

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('llm_client')


@dataclass
class OllamaConfig:
    """LLM 客户端配置 - 从集中配置读取（向后兼容类名）"""
    base_url: str = LLM_CONFIG["base_url"]
    model: str = LLM_CONFIG["intent_model"]
    
    # 超时配置（秒）
    connect_timeout: float = 5.0      # TCP连接建立
    read_timeout: float = float(LLM_CONFIG.get("timeout", 60))  # 读取响应
    total_timeout: float = 120.0      # 总超时兜底
    
    # 重试配置
    max_retries: int = 3
    retry_backoff: float = 1.0        # 退避基数（秒）
    retry_max_delay: float = 10.0     # 最大退避延迟
    
    # 连接池配置
    pool_connections: int = 10        # 连接池大小
    pool_maxsize: int = 10            # 最大连接数
    pool_block: bool = False          # 连接池满时是否阻塞
    
    # 健康检查
    health_check_interval: int = 300  # 健康检查间隔（秒）


class OllamaClient:
    """健壮的Ollama HTTP客户端"""
    
    def __init__(self, config: Optional[OllamaConfig] = None):
        self.config = config or OllamaConfig()
        self.session = self._create_session()
        self._last_health_check = 0
        self._is_healthy = False
        self._model_loaded = False
    
    def _create_session(self) -> requests.Session:
        """创建带连接池和重试策略的Session"""
        session = requests.Session()
        
        # 配置重试策略
        retry_strategy = Retry(
            total=self.config.max_retries,
            backoff_factor=self.config.retry_backoff,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "POST"],
            raise_on_status=False
        )
        
        # 配置连接池
        adapter = HTTPAdapter(
            pool_connections=self.config.pool_connections,
            pool_maxsize=self.config.pool_maxsize,
            max_retries=retry_strategy,
            pool_block=self.config.pool_block
        )
        
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        
        # 设置默认headers（keep-alive + OpenAI兼容）
        session.headers.update({
            'Connection': 'keep-alive',
            'Accept': 'application/json',
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {LLM_CONFIG.get("api_key", "not-needed")}',
        })
        
        return session
    
    def health_check(self, force: bool = False) -> bool:
        """健康检查
        
        Args:
            force: 强制检查，忽略缓存
            
        Returns:
            是否健康
        """
        now = time.time()
        
        # 检查缓存
        if not force and (now - self._last_health_check) < self.config.health_check_interval:
            return self._is_healthy
        
        try:
            # 检查服务是否响应（OpenAI 兼容格式）
            response = self.session.get(
                f"{self.config.base_url}/models",
                timeout=(self.config.connect_timeout, 5.0)
            )
            
            if response.status_code != 200:
                logger.warning(f"LLM健康检查失败: HTTP {response.status_code}")
                self._is_healthy = False
                return False
            
            # 检查模型是否已加载
            data = response.json()
            models = [m.get('id') for m in data.get('data', [])]
            
            if self.config.model not in models:
                logger.warning(f"模型 {self.config.model} 未加载，已加载模型: {models}")
                self._model_loaded = False
                self._is_healthy = True  # 服务健康但模型未加载
                return True
            
            self._model_loaded = True
            self._is_healthy = True
            self._last_health_check = now
            
            logger.info(f"LLM健康检查通过，模型 {self.config.model} 已就绪")
            return True
            
        except requests.exceptions.ConnectionError as e:
            logger.error(f"LLM连接失败: {e}")
            self._is_healthy = False
            return False
        except Exception as e:
            logger.error(f"LLM健康检查异常: {e}")
            self._is_healthy = False
            return False
    
    def generate(self, 
                 prompt: str,
                 system: Optional[str] = None,
                 options: Optional[Dict] = None,
                 timeout: Optional[tuple] = None) -> Optional[Dict]:
        """生成文本（OpenAI 兼容 API，保持原有接口）
        
        内部调用 /v1/chat/completions，返回格式模拟原 Ollama 的 {"response": "..."}。
        """
        if timeout is None:
            timeout = (self.config.connect_timeout, self.config.read_timeout)
        
        if not self.health_check():
            logger.warning("LLM服务不健康，跳过生成")
            return None
        
        # 构建 messages
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        
        # 解析 options
        temperature = 0.3
        max_tokens = 500
        if options:
            temperature = options.get("temperature", 0.3)
            max_tokens = options.get("num_predict", options.get("max_tokens", 500))
        
        payload = {
            "model": self.config.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        
        # 添加 extra_params（如 enable_thinking=False）
        extra = LLM_CONFIG.get("extra_params", {})
        if extra:
            payload.update(extra)
        
        # 带重试的生成请求
        for attempt in range(self.config.max_retries + 1):
            try:
                logger.debug(f"LLM请求 (尝试 {attempt + 1}/{self.config.max_retries + 1})")
                
                response = self.session.post(
                    f"{self.config.base_url}/chat/completions",
                    json=payload,
                    timeout=timeout
                )
                
                if response.status_code == 429:
                    retry_after = int(response.headers.get('Retry-After', 1))
                    logger.warning(f"触发限流，等待 {retry_after} 秒后重试")
                    time.sleep(retry_after)
                    continue
                
                if response.status_code != 200:
                    logger.error(f"生成失败: HTTP {response.status_code}, {response.text[:200]}")
                    if attempt < self.config.max_retries:
                        time.sleep(self._calculate_backoff(attempt))
                        continue
                    return None
                
                data = response.json()
                # 转换为原 Ollama 格式：{"response": "..."}
                content = data["choices"][0]["message"]["content"] if data.get("choices") else ""
                return {"response": content}
                
            except requests.exceptions.Timeout as e:
                logger.warning(f"生成超时 (尝试 {attempt + 1}): {e}")
                if attempt < self.config.max_retries:
                    time.sleep(self._calculate_backoff(attempt))
                    continue
                return None
                
            except requests.exceptions.ConnectionError as e:
                logger.warning(f"连接错误 (尝试 {attempt + 1}): {e}")
                if attempt < self.config.max_retries:
                    time.sleep(self._calculate_backoff(attempt))
                    continue
                return None
                
            except Exception as e:
                logger.error(f"生成异常 (尝试 {attempt + 1}): {e}")
                if attempt < self.config.max_retries:
                    time.sleep(self._calculate_backoff(attempt))
                    continue
                return None
        
        return None
    
    def extract_response(self, result: Dict) -> str:
        """提取响应内容（兼容Qwen3思考模式）
        
        Qwen3模型使用thinking字段，需要从思考内容中提取最终JSON输出
        """
        if not result:
            return ''
        
        import re
        
        # 优先使用response字段
        response = result.get('response', '').strip()
        if response:
            return response
        
        # 如果response为空，从thinking字段提取
        thinking = result.get('thinking', '')
        if not thinking:
            return ''
        
        # 策略1: JSON代码块 ```json ... ```
        match = re.search(r'```json\s*(.*?)\s*```', thinking, re.DOTALL)
        if match:
            return match.group(1).strip()
        
        # 策略2: 通用代码块 ``` ... ```
        match = re.search(r'```\s*(.*?)\s*```', thinking, re.DOTALL)
        if match:
            return match.group(1).strip()
        
        # 策略3: 找完整的JSON对象（含scene/intent等字段）
        # 匹配最完整的大括号内容
        json_matches = re.findall(r'\{[^{}]*(?:"[^"]*"[^{}]*)*\}', thinking, re.DOTALL)
        if json_matches:
            # 验证并返回最长的有效JSON
            for candidate in sorted(json_matches, key=len, reverse=True):
                try:
                    json.loads(candidate)
                    return candidate
                except:
                    continue
        
        # 策略4: 找最终输出声明后的JSON
        final_match = re.search(r'(?:最终输出|我的输出|我应该输出)["\']?[:：]?\s*["\']?({[^}]+})', thinking, re.IGNORECASE)
        if final_match:
            return final_match.group(1)
        
        # 策略5: 最后10行中找JSON
        lines = [l.strip() for l in thinking.split('\n') if l.strip()]
        for line in reversed(lines[-10:]):
            if '{' in line and '"' in line:
                json_match = re.search(r'({.+})', line, re.DOTALL)
                if json_match:
                    try:
                        json.loads(json_match.group(1))
                        return json_match.group(1)
                    except:
                        continue
        
        return ''
    
    def _warmup_model(self) -> bool:
        """模型预热（发送简单请求加载模型到内存）"""
        try:
            payload = {
                "model": self.config.model,
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 1,
                "stream": False,
            }
            response = self.session.post(
                f"{self.config.base_url}/chat/completions",
                json=payload,
                timeout=(self.config.connect_timeout, 30.0)
            )
            
            if response.status_code == 200:
                self._model_loaded = True
                logger.info(f"模型 {self.config.model} 预热成功")
                return True
            else:
                logger.error(f"模型预热失败: HTTP {response.status_code}")
                return False
                
        except Exception as e:
            logger.error(f"模型预热异常: {e}")
            return False
    
    def _calculate_backoff(self, attempt: int) -> float:
        """计算指数退避延迟"""
        delay = min(
            self.config.retry_backoff * (2 ** attempt),
            self.config.retry_max_delay
        )
        return delay + (hash(str(time.time())) % 100) / 1000  # 添加随机抖动
    
    def close(self):
        """关闭连接池"""
        if self.session:
            self.session.close()
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


# ========== 使用示例 ==========

def example_usage():
    """使用示例"""
    
    # 创建客户端（使用默认配置）
    client = OllamaClient()
    
    # 健康检查
    if not client.health_check():
        print("❌ Ollama服务不可用")
        return
    
    # 生成文本
    result = client.generate(
        prompt="请用一句话总结：客户服务的重要性",
        options={"temperature": 0.5, "num_predict": 100}
    )
    
    if result:
        print(f"✅ 生成成功: {result.get('response', '')[:100]}...")
    else:
        print("❌ 生成失败")
    
    # 关闭连接
    client.close()


def benchmark_robustness():
    """健壮性基准测试"""
    print("🧪 Ollama健壮性测试")
    print("=" * 60)
    
    client = OllamaClient()
    
    # 测试1: 健康检查
    print("\n【测试1】健康检查")
    healthy = client.health_check(force=True)
    print(f"  结果: {'✅ 健康' if healthy else '❌ 不健康'}")
    
    # 测试2: 连续10次生成
    print("\n【测试2】连续10次生成")
    success_count = 0
    latencies = []
    
    for i in range(10):
        start = time.time()
        result = client.generate(
            prompt=f"测试{i}：你好",
            options={"num_predict": 10}
        )
        latency = time.time() - start
        latencies.append(latency)
        
        if result:
            success_count += 1
            print(f"  [{i+1}] ✅ {latency:.2f}s")
        else:
            print(f"  [{i+1}] ❌ 失败")
    
    print(f"\n  成功率: {success_count}/10 ({success_count*10}%)")
    if latencies:
        print(f"  平均延迟: {sum(latencies)/len(latencies):.2f}s")
        print(f"  最大延迟: {max(latencies):.2f}s")
        print(f"  最小延迟: {min(latencies):.2f}s")
    
    client.close()


if __name__ == "__main__":
    # 运行基准测试
    benchmark_robustness()
