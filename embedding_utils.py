"""Embedding模型统一单例管理器

支持LM Studio和oMLX两种本地部署方案
"""

import os
import logging
import requests
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

logger = logging.getLogger('embedding_utils')

# 全局单例
_embedding_model = None

def get_embedding_model():
    """获取Embedding模型单例（延迟加载）"""
    global _embedding_model
    
    if _embedding_model is None:
        logger.info("🔄 首次加载Embedding模型...")
        try:
            # 获取配置
            base_url = os.getenv('LOCAL_MODEL_URL', 'http://localhost:8000/v1')
            api_key = os.getenv('LOCAL_API_KEY', '')
            
            # 检查可用嵌入模型
            try:
                headers = {}
                if api_key:
                    headers['Authorization'] = f'Bearer {api_key}'
                
                resp = requests.get(f"{base_url}/models", headers=headers, timeout=5)
                if resp.status_code == 200:
                    models = [m['id'] for m in resp.json().get('data', [])]
                    # 优先使用jina-embeddings，其次Qwen3-Embedding
                    embed_models = [m for m in models if 'qwen3-embed' in m.lower() or 'qwen3-embedding' in m.lower()]
                    if not embed_models:
                        embed_models = [m for m in models if 'jina-embed' in m.lower()]
                    if not embed_models:
                        embed_models = [m for m in models if 'embed' in m.lower()]
                    if embed_models:
                        model_name = embed_models[0]
                        logger.info(f"✅ 使用本地嵌入模型: {model_name} (服务: {base_url})")
                        _embedding_model = LocalEmbeddingModel(base_url, model_name, api_key)
                        return _embedding_model
            except Exception as e:
                logger.warning(f"本地嵌入模型检查失败: {e}")
            
            # 回退到HuggingFace
            from sentence_transformers import SentenceTransformer
            model_name = os.getenv('EMBEDDING_MODEL', 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2')
            _embedding_model = SentenceTransformer(model_name)
            logger.info(f"✅ HuggingFace Embedding模型加载完成: {model_name}")
        except Exception as e:
            logger.error(f"❌ Embedding模型加载失败: {e}")
            raise
    
    return _embedding_model

class LocalEmbeddingModel:
    """本地嵌入模型适配器（支持LM Studio和oMLX）"""
    
    def __init__(self, base_url: str, model: str, api_key: str = ''):
        self.base_url = base_url
        self.model = model
        self.api_key = api_key
        self.session = requests.Session()
    
    def encode(self, texts, **kwargs):
        """编码文本（兼容SentenceTransformer接口）"""
        import numpy as np
        
        if isinstance(texts, str):
            texts = [texts]
        
        headers = {}
        if self.api_key:
            headers['Authorization'] = f'Bearer {self.api_key}'
        
        embeddings = []
        for text in texts:
            resp = self.session.post(
                f"{self.base_url}/embeddings",
                headers=headers,
                json={
                    "model": self.model,
                    "input": text
                },
                timeout=30
            )
            resp.raise_for_status()
            data = resp.json()
            embedding = data['data'][0]['embedding']
            embeddings.append(embedding)
        
        return np.array(embeddings)

def encode_texts(texts, **kwargs):
    """编码文本（使用单例模型）"""
    model = get_embedding_model()
    return model.encode(texts, **kwargs)

def reset_model():
    """重置模型（用于测试或内存回收）"""
    global _embedding_model
    _embedding_model = None
    logger.info("🔄 Embedding模型已重置")
