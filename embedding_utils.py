"""Embedding模型统一单例管理器

解决多处重复加载Embedding模型导致的内存浪费问题
"""

import os
import logging
import requests

logger = logging.getLogger('embedding_utils')

# 全局单例
_embedding_model = None

def get_embedding_model():
    """获取Embedding模型单例（延迟加载）"""
    global _embedding_model
    
    if _embedding_model is None:
        logger.info("🔄 首次加载Embedding模型...")
        try:
            # 优先使用LM Studio的嵌入模型
            lm_studio_url = os.getenv('LOCAL_MODEL_URL', 'http://localhost:1234/v1')
            import requests
            
            # 检查LM Studio可用嵌入模型
            try:
                resp = requests.get(f"{lm_studio_url}/models", timeout=5)
                if resp.status_code == 200:
                    models = [m['id'] for m in resp.json().get('data', [])]
                    # 优先使用Qwen3-Embedding，其次nomic-embed-text
                    embed_models = [m for m in models if 'qwen3-embed' in m.lower()]
                    if not embed_models:
                        embed_models = [m for m in models if 'nomic-embed' in m.lower()]
                    if embed_models:
                        model_name = embed_models[0]
                        logger.info(f"✅ 使用LM Studio嵌入模型: {model_name}")
                        _embedding_model = LMStudioEmbeddingModel(lm_studio_url, model_name)
                        return _embedding_model
            except Exception as e:
                logger.warning(f"LM Studio嵌入模型检查失败: {e}")
            
            # 回退到HuggingFace
            from sentence_transformers import SentenceTransformer
            model_name = os.getenv('EMBEDDING_MODEL', 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2')
            _embedding_model = SentenceTransformer(model_name)
            logger.info(f"✅ HuggingFace Embedding模型加载完成: {model_name}")
        except Exception as e:
            logger.error(f"❌ Embedding模型加载失败: {e}")
            raise
    
    return _embedding_model

class LMStudioEmbeddingModel:
    """LM Studio嵌入模型适配器"""
    
    def __init__(self, base_url: str, model: str):
        self.base_url = base_url
        self.model = model
        self.session = requests.Session()
    
    def encode(self, texts, **kwargs):
        """编码文本（兼容SentenceTransformer接口）"""
        import numpy as np
        
        if isinstance(texts, str):
            texts = [texts]
        
        embeddings = []
        for text in texts:
            resp = self.session.post(
                f"{self.base_url}/embeddings",
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
