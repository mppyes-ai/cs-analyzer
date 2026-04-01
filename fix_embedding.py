#!/usr/bin/env python3
import re

# Fix smart_scoring_v2.py
with open('smart_scoring_v2.py', 'r') as f:
    content = f.read()

# Replace the singleton section with import
old_singleton = '''# ========== 全局单例：Embedding模型缓存 ==========
_embedding_model_singleton = None

def get_embedding_model():
    """获取全局单例Embedding模型（延迟加载）"""
    global _embedding_model_singleton
    if _embedding_model_singleton is None:
        print("🔄 首次加载Embedding模型...")
        from sentence_transformers import SentenceTransformer
        _embedding_model_singleton = SentenceTransformer(
            'paraphrase-multilingual-MiniLM-L12-v2',
            device='cpu'  # 使用CPU避免MPS兼容问题
        )
        print("✅ Embedding模型加载完成")
    return _embedding_model_singleton'''

new_singleton = '''# ========== 使用统一Embedding单例 ==========
from embedding_utils import get_embedding_model'''

content = content.replace(old_singleton, new_singleton)

with open('smart_scoring_v2.py', 'w') as f:
    f.write(content)

print("Fixed smart_scoring_v2.py")

# Fix knowledge_base_v2.py
with open('knowledge_base_v2.py', 'r') as f:
    content = f.read()

# Check if it has its own embedding model loading
if 'SentenceTransformer' in content and 'get_embedding_model' in content:
    # Replace the function to use the shared one
    old_func = '''def get_embedding_model():
    """获取Embedding模型（单例）"""
    global _embedding_model
    if _embedding_model is None:
        from sentence_transformers import SentenceTransformer
        _embedding_model = SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2')
    return _embedding_model'''
    
    new_func = '''def get_embedding_model():
    """获取Embedding模型（使用统一单例）"""
    from embedding_utils import get_embedding_model as _get_model
    return _get_model()'''
    
    content = content.replace(old_func, new_func)
    
    with open('knowledge_base_v2.py', 'w') as f:
        f.write(content)
    print("Fixed knowledge_base_v2.py")

# Fix hybrid_retriever.py  
with open('hybrid_retriever.py', 'r') as f:
    content = f.read()

if 'SentenceTransformer' in content:
    # Add import at top and replace the loading
    if 'from embedding_utils' not in content:
        content = content.replace(
            'import numpy as np',
            'import numpy as np\nfrom embedding_utils import get_embedding_model'
        )
    
    # Replace model initialization
    old_init = '''if self.embedding_model is None:
            from sentence_transformers import SentenceTransformer
            self.embedding_model = SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2')'''
    
    new_init = '''if self.embedding_model is None:
            self.embedding_model = get_embedding_model()'''
    
    content = content.replace(old_init, new_init)
    
    with open('hybrid_retriever.py', 'w') as f:
        f.write(content)
    print("Fixed hybrid_retriever.py")

print("All embedding optimizations complete!")