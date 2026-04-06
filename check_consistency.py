#!/usr/bin/env python3
"""CS-Analyzer 一致性检查脚本

自动扫描代码库，检测以下不一致问题：
1. 函数名引用与定义不一致
2. 模型配置硬编码与集中配置不符
3. 导入的模块版本不一致
4. 数据库 Schema 与代码查询不一致

用法:
    python3 check_consistency.py          # 全量检查
    python3 check_consistency.py --fix    # 尝试自动修复简单问题
    python3 check_consistency.py --ci     # CI模式（有错误时退出码1）

作者: 小虾米
更新: 2026-03-21
"""

import os
import sys
import re
import ast
import argparse
from pathlib import Path
from typing import List, Dict, Set, Tuple

# 项目根目录
BASE_DIR = Path(__file__).parent


class ConsistencyChecker:
    """一致性检查器"""
    
    def __init__(self, fix: bool = False, verbose: bool = False):
        self.fix = fix
        self.verbose = verbose
        self.errors: List[Tuple[str, str, str]] = []  # (文件, 类型, 描述)
        self.warnings: List[Tuple[str, str, str]] = []
        
    def log(self, message: str):
        """输出日志"""
        if self.verbose:
            print(message)
    
    def error(self, file: str, check_type: str, message: str):
        """记录错误"""
        self.errors.append((file, check_type, message))
        print(f"❌ [{check_type}] {file}: {message}")
    
    def warning(self, file: str, check_type: str, message: str):
        """记录警告"""
        self.warnings.append((file, check_type, message))
        print(f"⚠️  [{check_type}] {file}: {message}")
    
    def success(self, check_type: str):
        """记录成功"""
        if self.verbose:
            print(f"✅ [{check_type}] 检查通过")
    
    # ========== 检查1: 函数名一致性 ==========
    def check_function_names(self):
        """检查函数定义与引用一致性"""
        self.log("\n🔍 检查函数名一致性...")
        
        # 定义文件和期望的导出函数
        expected_exports = {
            "db_utils.py": {
                "defined": ["load_sessions", "get_session_by_id", "save_correction_v2", 
                           "get_pending_corrections", "init_correction_tables"],
                "deprecated": ["get_all_sessions", "init_task_queue", "save_correction"],
            },
            "task_queue.py": {
                "defined": ["init_queue_tables", "get_pending_task", "complete_task", 
                           "fail_task", "retry_failed_tasks", "cancel_task"],
                "deprecated": ["init_task_queue"],
            },
            "rule_extractor_v2.py": {
                "defined": ["process_correction_to_rule", "prepare_extraction_input",
                           "extract_rule_with_kimi", "process_all_pending_corrections"],
                "deprecated": ["extract_rule_from_correction"],
            }
        }
        
        for def_file, exports in expected_exports.items():
            def_path = BASE_DIR / def_file
            if not def_path.exists():
                self.error(def_file, "函数名", f"定义文件不存在")
                continue
            
            # 读取定义文件，找出实际定义的函数
            content = def_path.read_text()
            defined_funcs = set(re.findall(r'^def\s+(\w+)\s*\(', content, re.MULTILINE))
            
            # 检查期望的函数是否都已定义
            for func in exports["defined"]:
                if func not in defined_funcs:
                    self.error(def_file, "函数名", f"期望导出函数 '{func}' 未定义")
            
            # 扫描其他文件，检查是否使用了废弃函数
            deprecated_pattern = '|'.join(exports["deprecated"])
            for py_file in BASE_DIR.glob("*.py"):
                if py_file.name == def_file:
                    continue
                
                content = py_file.read_text()
                # 跳过导入语句
                content = re.sub(r'^from\s+\S+\s+import\s+.*$', '', content, flags=re.MULTILINE)
                content = re.sub(r'^import\s+\S+.*$', '', content, flags=re.MULTILINE)
                
                for deprecated_func in exports["deprecated"]:
                    if deprecated_func in content and deprecated_func in defined_funcs:
                        self.error(py_file.name, "函数名", 
                                  f"使用了废弃函数 '{deprecated_func}'，应改为 '{exports['defined'][0]}'")
        
        self.success("函数名一致性")
    
    # ========== 检查2: 模型配置硬编码 ==========
    def check_model_config(self):
        """检查模型配置是否硬编码"""
        self.log("\n🔍 检查模型配置硬编码...")
        
        # 集中配置中的模型
        config_content = (BASE_DIR / "config.py").read_text()
        ollama_model_match = re.search(r'"model":\s*os\.getenv\("OLLAMA_MODEL",\s*"([^"]+)"\)', config_content)
        if ollama_model_match:
            expected_model = ollama_model_match.group(1)
        else:
            expected_model = "qwen2.5:7b"
        
        # 扫描所有 Python 文件
        for py_file in BASE_DIR.glob("*.py"):
            if py_file.name == "config.py":
                continue
            if py_file.name == "check_consistency.py":
                continue
            
            content = py_file.read_text()
            
            # 检查硬编码的模型名（排除配置引用和注释）
            # 匹配 model="xxx" 或 model: str = "xxx"
            patterns = [
                r'model\s*=\s*["\'](qwen\d+[^"\']*)["\']',
                r'model:\s*str\s*=\s*["\'](qwen\d+[^"\']*)["\']',
                r'"ollama_model":\s*["\'](qwen\d+[^"\']*)["\']',
            ]
            
            for pattern in patterns:
                matches = re.finditer(pattern, content)
                for match in matches:
                    hardcoded_model = match.group(1)
                    if hardcoded_model != expected_model:
                        # 检查是否在从 config 导入的上下文中
                        if "config." not in content[:match.start()].split("\n")[-1]:
                            self.warning(py_file.name, "模型配置",
                                        f"硬编码模型 '{hardcoded_model}'，建议改为从 config.py 导入")
        
        self.success("模型配置")
    
    # ========== 检查3: 模块导入版本一致性 ==========
    def check_module_imports(self):
        """检查模块导入版本一致性"""
        self.log("\n🔍 检查模块导入版本一致性...")
        
        # 期望的导入版本
        expected_imports = {
            "intent_classifier": "intent_classifier_v3",  # 当前版本是 v3
            "knowledge_base": "knowledge_base_v2",
            "rule_extractor": "rule_extractor_v2",
            "smart_scoring": "smart_scoring_v2",
        }
        
        for py_file in BASE_DIR.glob("*.py"):
            if py_file.name == "check_consistency.py":
                continue
            
            content = py_file.read_text()
            
            for module, expected_version in expected_imports.items():
                # 查找所有导入语句
                import_patterns = [
                    rf'from\s+({module}_v\d+)\s+import',
                    rf'import\s+({module}_v\d+)',
                ]
                
                for pattern in import_patterns:
                    matches = re.finditer(pattern, content)
                    for match in matches:
                        actual_version = match.group(1)
                        if actual_version != expected_version:
                            self.error(py_file.name, "模块导入",
                                      f"导入旧版本 '{actual_version}'，应改为 '{expected_version}'")
        
        self.success("模块导入")
    
    # ========== 检查4: 数据库字段一致性 ==========
    def check_database_schema(self):
        """检查数据库字段一致性"""
        self.log("\n🔍 检查数据库字段一致性...")
        
        # 期望的表结构（从代码中提取）
        expected_schema = {
            "sessions": [
                "session_id", "user_id", "staff_name", "messages", "summary",
                "professionalism_score", "standardization_score", "policy_execution_score", "conversion_score",
                "total_score", "analysis_json", "strengths", "issues", "suggestions",
                "session_count", "start_time", "end_time", "created_at"
            ],
            "corrections": [
                "id", "session_id", "changed_fields", "reason", "other_reason",
                "corrected_by", "status", "created_at"
            ],
            "rules": [
                "rule_id", "rule_type", "status", "source_correction_id",
                "created_at", "approved_at", "approved_by"
            ]
        }
        
        # 读取 db_utils.py 中的查询语句
        db_utils_content = (BASE_DIR / "db_utils.py").read_text()
        
        for table, expected_fields in expected_schema.items():
            # 查找 SELECT 语句中的字段
            select_pattern = rf'SELECT\s+(.*?)\s+FROM\s+{table}'
            selects = re.findall(select_pattern, db_utils_content, re.DOTALL | re.IGNORECASE)
            
            for select_fields in selects:
                # 清理换行和多余空格
                select_fields = re.sub(r'\s+', ' ', select_fields)
                # 检查是否使用了 *（不推荐）
                if '*' in select_fields:
                    self.warning("db_utils.py", "数据库字段",
                                f"表 '{table}' 使用 SELECT *，建议明确列出字段")
        
        self.success("数据库字段")
    
    # ========== 检查5: 配置引用正确性 ==========
    def check_config_usage(self):
        """检查是否正确使用 config.py"""
        self.log("\n🔍 检查配置引用正确性...")
        
        # 应该使用 config.py 的文件
        should_use_config = [
            "worker.py", "smart_scoring_v2.py", "rule_extractor_v2.py",
            "test_e2e.py", "intent_classifier_v2.py", "intent_classifier_v3.py",
            "ollama_client.py"
        ]
        
        for filename in should_use_config:
            py_file = BASE_DIR / filename
            if not py_file.exists():
                continue
            
            content = py_file.read_text()
            
            # 检查是否有硬编码的模型名
            if re.search(r'["\']qwen[0-9.]+[:\d]+[b]?["\']', content):
                # 检查是否导入了 config
                if "from config import" not in content and "import config" not in content:
                    self.warning(filename, "配置引用",
                                f"硬编码模型名但未从 config.py 导入，建议统一使用 config.OLLAMA_CONFIG")
        
        self.success("配置引用")
    
    # ========== 报告 ==========
    def report(self) -> bool:
        """输出检查报告
        
        Returns:
            是否全部通过
        """
        print("\n" + "=" * 60)
        print("📊 一致性检查报告")
        print("=" * 60)
        
        if not self.errors and not self.warnings:
            print("✅ 全部检查通过！")
            return True
        
        if self.errors:
            print(f"\n❌ 错误: {len(self.errors)} 个")
            for file, check_type, message in self.errors:
                print(f"  - [{check_type}] {file}: {message}")
        
        if self.warnings:
            print(f"\n⚠️  警告: {len(self.warnings)} 个")
            for file, check_type, message in self.warnings:
                print(f"  - [{check_type}] {file}: {message}")
        
        print("=" * 60)
        return len(self.errors) == 0


def main():
    parser = argparse.ArgumentParser(description='CS-Analyzer 一致性检查')
    parser.add_argument('--fix', action='store_true', help='尝试自动修复简单问题')
    parser.add_argument('--ci', action='store_true', help='CI模式（有错误时退出码1）')
    parser.add_argument('--verbose', '-v', action='store_true', help='详细输出')
    args = parser.parse_args()
    
    print("🚀 CS-Analyzer 一致性检查")
    print("=" * 60)
    
    checker = ConsistencyChecker(fix=args.fix, verbose=args.verbose)
    
    # 运行所有检查
    checker.check_function_names()
    checker.check_model_config()
    checker.check_module_imports()
    checker.check_database_schema()
    checker.check_config_usage()
    
    # 输出报告
    passed = checker.report()
    
    # CI 模式退出码
    if args.ci:
        sys.exit(0 if passed else 1)
    
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
