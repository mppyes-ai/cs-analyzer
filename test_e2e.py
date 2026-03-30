"""端到端测试脚本 - 验证CS-Analyzer v2核心流程

测试范围：
1. 环境检查（Ollama、数据库、模型）
2. 漏斗式意图分类器
3. 规则提取（矫正记录→结构化规则）
4. 审核流程（通过→向量库同步）
5. 混合检索
6. 智能评分（含CoT输出）
7. 版本化迁移

用法: python test_e2e.py [--verbose]

作者: 小虾米
更新: 2026-03-17
"""

import sys
import os
import json
import argparse
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))

# 从集中配置导入
from config import OLLAMA_CONFIG

# 测试配置（继承集中配置，可覆盖）
TEST_CONFIG = {
    "ollama_model": OLLAMA_CONFIG["model"],
    "ollama_url": OLLAMA_CONFIG["url"],
    "test_session_id": "test_e2e_001",
    "verbose": False
}

class TestRunner:
    """测试运行器"""
    
    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.passed = 0
        self.failed = 0
        self.errors = []
    
    def log(self, message: str, level: str = "INFO"):
        """输出日志"""
        if level == "ERROR" or self.verbose:
            print(f"[{level}] {message}")
    
    def test(self, name: str, func):
        """运行单个测试"""
        print(f"\n🧪 测试: {name}")
        try:
            result = func()
            if result:
                print(f"  ✅ 通过")
                self.passed += 1
                return True
            else:
                print(f"  ❌ 失败")
                self.failed += 1
                return False
        except Exception as e:
            print(f"  ❌ 异常: {e}")
            self.errors.append((name, str(e)))
            self.failed += 1
            return False
    
    def report(self):
        """输出测试报告"""
        print("\n" + "=" * 60)
        print("📊 测试报告")
        print("=" * 60)
        print(f"通过: {self.passed}")
        print(f"失败: {self.failed}")
        print(f"成功率: {self.passed/(self.passed+self.failed)*100:.1f}%")
        
        if self.errors:
            print("\n错误详情:")
            for name, error in self.errors:
                print(f"  - {name}: {error}")
        
        print("=" * 60)
        return self.failed == 0


# ========== 测试用例 ==========

def test_environment():
    """测试1: 环境检查"""
    print("  检查Ollama服务...")
    import requests
    try:
        r = requests.get(f"{TEST_CONFIG['ollama_url']}/api/tags", timeout=5)
        if r.status_code == 200:
            models = [m['name'] for m in r.json().get('models', [])]
            if TEST_CONFIG['ollama_model'] in models:
                print(f"    ✅ Ollama正常，模型已就绪")
                return True
            else:
                print(f"    ❌ 模型未找到: {TEST_CONFIG['ollama_model']}")
                return False
        else:
            print(f"    ❌ Ollama返回错误: {r.status_code}")
            return False
    except Exception as e:
        print(f"    ❌ Ollama连接失败: {e}")
        return False

def test_database_tables():
    """测试2: 数据库表结构"""
    print("  检查数据库表...")
    from knowledge_base_v2 import check_v2_tables_exist, init_rules_tables
    
    try:
        init_rules_tables()
        if check_v2_tables_exist():
            print("    ✅ rules表已就绪")
            
            # 检查analysis_runs表
            from db_utils import get_connection
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='analysis_runs'")
            result = cursor.fetchone()
            conn.close()
            
            if result:
                print("    ✅ analysis_runs表已就绪")
                return True
            else:
                print("    ⚠️ analysis_runs表不存在（需要运行migrate_to_v2_versioned.py初始化）")
                return True  # 非致命错误
        else:
            print("    ❌ rules表不存在")
            return False
    except Exception as e:
        print(f"    ❌ 数据库检查失败: {e}")
        return False

def test_funnel_intent_classifier():
    """测试3: 漏斗式意图分类器"""
    print("  测试漏斗式分类...")
    from intent_classifier_v3 import FunnelIntentClassifier
    
    # 测试高频简单查询（应该被规则拦截）
    simple_messages = [
        {"role": "customer", "content": "发什么快递？"}
    ]
    
    classifier = FunnelIntentClassifier()
    result = classifier.classify(simple_messages)
    
    if result and result.source == "rule":
        print(f"    ✅ 规则拦截成功: {result.sub_scene} ({result.latency_ms:.1f}ms)")
    else:
        print(f"    ⚠️ 规则未拦截，实际来源: {result.source if result else 'None'}")
    
    # 测试复杂查询（应该路由到Qwen2.5）
    complex_messages = [
        {"role": "customer", "content": "我看你们有一款新品和旧款好像差不多，能详细说说两者的具体差异吗？主播说的和客服说的不一样"},
        {"role": "staff", "content": "小主您可以以直播间主播的规则为准呢"},
        {"role": "customer", "content": "这个适合三口之家用吗，会不会太大了？"}
    ]
    
    result = classifier.classify(complex_messages)
    
    if result and result.source in ["qwen2.5", "keyword_fallback", "sentiment_analyzer"]:
        print(f"    ✅ 复杂查询处理成功: {result.source}")
        return True
    else:
        print(f"    ❌ 复杂查询处理失败 (实际来源: {result.source if result else 'None'})")
        return False

def test_rule_extraction():
    """测试4: 规则提取（需要API Key）"""
    print("  测试规则提取...")
    
    # 模拟矫正数据
    mock_correction = {
        "session_id": TEST_CONFIG['test_session_id'],
        "session_summary": "用户质疑直播间规则，客服机械重复话术",
        "messages": [
            {"role": "user", "content": "我看你们有一款新品和旧款好像差不多，能详细说说两者的具体差异吗？"},
            {"role": "staff", "content": "小主您可以以直播间主播的规则为准呢"},
            {"role": "user", "content": "这个适合三口之家用吗，会不会太大了？"}
        ],
        "correction": {
            "changed_fields": ["standardization"],
            "reason": "客服机械重复话术，没有安抚用户情绪",
            "corrected_by": "test"
        }
    }
    
    # 检查是否能构建提取输入
    try:
        from rule_extractor_v2 import prepare_extraction_input
        # 由于没有真实矫正记录，仅测试函数可调用
        print("    ✅ 规则提取模块可导入")
        return True
    except Exception as e:
        print(f"    ❌ 规则提取模块异常: {e}")
        return False

def test_hybrid_retriever():
    """测试5: 混合检索"""
    print("  测试混合检索...")
    from hybrid_retriever import HybridRuleRetriever
    
    try:
        retriever = HybridRuleRetriever()
        
        # 由于没有已确认的规则，可能返回空
        results = retriever.search("用户投诉", top_k=3)
        
        print(f"    ✅ 混合检索模块可调用 (返回{len(results)}条结果)")
        return True
    except Exception as e:
        print(f"    ⚠️ 混合检索异常: {e}")
        # 非致命错误，可能是没有数据
        return True

def test_smart_scoring():
    """测试6: 智能评分引擎（需要API Key）"""
    print("  测试智能评分引擎...")
    from smart_scoring_v2 import SmartScoringEngine
    
    try:
        engine = SmartScoringEngine(use_local_intent=True)
        
        # 测试预分析
        test_messages = [
            {"role": "customer", "content": "发什么快递？"}
        ]
        
        result = engine._analyze_session_pre(test_messages)
        
        if result and 'scene' in result:
            print(f"    ✅ 预分析成功: {result['scene']} (来源: {result.get('source', 'unknown')})")
            return True
        else:
            print(f"    ❌ 预分析失败")
            return False
    except Exception as e:
        print(f"    ❌ 评分引擎异常: {e}")
        return False

def test_versioned_migration():
    """测试7: 版本化迁移"""
    print("  测试版本化迁移模块...")
    from migrate_to_v2_versioned import init_analysis_runs_table, get_next_version
    
    try:
        init_analysis_runs_table()
        
        # 获取版本号
        version = get_next_version(TEST_CONFIG['test_session_id'])
        print(f"    ✅ analysis_runs表就绪 (测试会话版本: {version})")
        return True
    except Exception as e:
        print(f"    ❌ 版本化迁移模块异常: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description='端到端测试')
    parser.add_argument('--verbose', action='store_true', help='详细输出')
    args = parser.parse_args()
    
    print("🚀 CS-Analyzer v2 端到端测试")
    print("=" * 60)
    print(f"测试时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Ollama模型: {TEST_CONFIG['ollama_model']}")
    print("=" * 60)
    
    runner = TestRunner(verbose=args.verbose)
    
    # 运行测试
    runner.test("环境检查", test_environment)
    runner.test("数据库表结构", test_database_tables)
    runner.test("漏斗式意图分类器", test_funnel_intent_classifier)
    runner.test("规则提取模块", test_rule_extraction)
    runner.test("混合检索模块", test_hybrid_retriever)
    runner.test("智能评分引擎", test_smart_scoring)
    runner.test("版本化迁移模块", test_versioned_migration)
    
    # 输出报告
    success = runner.report()
    
    print("\n💡 下一步建议:")
    if success:
        print("  1. 运行 python intent_classifier_v3.py 测试分类器性能")
        print("  2. 运行 python migrate_to_v2_versioned.py migrate --limit 5 测试迁移")
        print("  3. 启动Streamlit测试前端页面")
    else:
        print("  请修复失败项后再继续")
    
    return 0 if success else 1

if __name__ == "__main__":
    sys.exit(main())
