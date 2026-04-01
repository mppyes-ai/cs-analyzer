"""Embedding模型统一单例管理器

解决多处重复加载Embedding模型导致的内存浪费问题
"""

import os
import logging

logger = logging.getLogger('embedding_utils')

# 全局单例
_embedding_model = None

def get_embedding_model():
    """获取Embedding模型单例（延迟加载）"""
    global _embedding_model
    
    if _embedding_model is None:
        logger.info("🔄 首次加载Embedding模型...")
        try:
            from sentence_transformers import SentenceTransformer
            model_name = os.getenv('EMBEDDING_MODEL', 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2')
            _embedding_model = SentenceTransformer(model_name)
            logger.info("✅ Embedding模型加载完成")
        except Exception as e:
            logger.error(f"❌ Embedding模型加载失败: {e}")
            raise
    
    return _embedding_model

def encode_texts(texts, **kwargs):
    """编码文本（使用单例模型）"""
    model = get_embedding_model()
    return model.encode(texts, **kwargs)

def reset_model():
    """重置模型（用于测试或内存回收）"""
    global _embedding_model
    _embedding_model = None
    logger.info("🔄 Embedding模型已重置")