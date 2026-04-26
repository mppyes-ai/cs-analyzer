"""Ollama客户端适配器 - 已迁移到oMLX

注意：此模块现在作为oMLX的适配器使用，不再直接连接Ollama或LM Studio。
所有本地模型调用统一通过oMLX的OpenAI兼容API。

作者: 小虾米
更新: 2026-04-26 (迁移到oMLX)
"""

import json
import time
import logging
import os
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
from config import OLLAMA_CONFIG

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('ollama_client')


@dataclass
class OllamaConfig:
    """oMLX客户端配置 - 从集中配置读取"""
    base_url: str = OLLAMA_CONFIG["url"]
    model: str = OLLAMA_CONFIG["model"]
    api_key: str = os.getenv('LOCAL_API_KEY', '1234567890')
    
    # 超时配置（秒）
    connect_timeout: float = 5.0      # TCP连接建立
    read_timeout: float = float(OLLAMA_CONFIG["timeout"])  # 读取响应（生成超时）
    total_timeout: float = 60.0       # 总超时兜底
    
    # 重试配置
    max_retries: int = OLLAMA_CONFIG["max_retries"]   # 最大重试次数
    retry_backoff: float = 1.0        # 退避基数（秒）
    retry_max_delay: float = 10.0     # 最大退避延迟
    
    # 连接池配置
    pool_connections: int = 10        # 连接池大小
    pool_maxsize: int = 10            # 最大连接数
    pool_block: bool = False          # 连接池满时是否阻塞
    
    # 健康检查
    health_check_interval: int = 300  # 健康检查间隔（秒）


class OllamaClient:
    """oMLX OpenAI兼容客户端（原Ollama适配器）"""
    
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
        
        # 设置默认headers（keep-alive + API Key）
        session.headers.update({
            'Connection': 'keep-alive',
            'Accept': 'application/json',
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {self.config.api_key}',
        })
        
        return session
    
    def health_check(self, force: bool = False) -> bool:
        """健康检查"""
        now = time.time()
        
        # 检查缓存
        if not force and (now - self._last_health_check) < self.config.health_check_interval:
            return self._is_healthy
        
        try:
            # oMLX使用/models端点
            response = self.session.get(
                f"{self.config.base_url}/models",
                timeout=(self.config.connect_timeout, 5.0)
            )
            
            if response.status_code != 200:
                logger.warning(f"oMLX健康检查失败: HTTP {response.status_code}")
                self._is_healthy = False
                return False
            
            # 检查模型是否可用
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
            
            logger.info(f"oMLX健康检查通过，模型 {self.config.model} 已就绪")
            return True
            
        except requests.exceptions.ConnectionError as e:
            logger.error(f"oMLX连接失败: {e}")
            self._is_healthy = False
            return False
        except Exception as e:
            logger.error(f"oMLX健康检查异常: {e}")
            self._is_healthy = False
            return False
    
    def generate(self, 
                 prompt: str,
                 system: Optional[str] = None,
                 options: Optional[Dict] = None,
                 timeout: Optional[tuple] = None) -> Optional[Dict]:
        """生成文本（OpenAI兼容格式）"""
        # 使用配置超时或自定义超时
        if timeout is None:
            timeout = (self.config.connect_timeout, self.config.read_timeout)
        
        # 快速健康检查（非阻塞）
        if not self.health_check():
            logger.warning("oMLX服务不健康，跳过生成")
            return None
        
        # 构建OpenAI兼容的messages
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        
        payload = {
            "model": self.config.model,
            "messages": messages,
            "temperature": (options or {}).get('temperature', 0.3),
            "max_tokens": (options or {}).get('num_predict', 200),
            "stream": False
        }
        
        # 带重试的生成请求
        for attempt in range(self.config.max_retries + 1):
            try:
                logger.debug(f"生成请求 (尝试 {attempt + 1}/{self.config.max_retries + 1})")
                
                response = self.session.post(
                    f"{self.config.base_url}/chat/completions",
                    json=payload,
                    timeout=timeout
                )
                
                # 处理限流
                if response.status_code == 429:
                    retry_after = int(response.headers.get('Retry-After', 1))
                    logger.warning(f"触发限流，等待 {retry_after} 秒后重试")
                    time.sleep(retry_after)
                    continue
                
                # 处理其他错误
                if response.status_code != 200:
                    logger.error(f"生成失败: HTTP {response.status_code}, {response.text[:200]}")
                    if attempt < self.config.max_retries:
                        time.sleep(self._calculate_backoff(attempt))
                        continue
                    return None
                
                # 解析OpenAI兼容响应
                data = response.json()
                return {
                    'response': data['choices'][0]['message']['content'],
                    'model': data.get('model', self.config.model),
                    'usage': data.get('usage', {})
                }
                
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
        """提取响应内容"""
        if not result:
            return ''
        
        # OpenAI兼容格式直接使用response字段
        response = result.get('response', '').strip()
        if response:
            return response
        
        return ''
    
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
        print("❌ oMLX服务不可用")
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
    print("🧪 oMLX健壮性测试")
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
