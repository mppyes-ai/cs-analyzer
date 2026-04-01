#!/usr/bin/env python3
"""CS-Analyzer 批量分析入口 - 支持后台子代理模式

用法:
    python cs_analyzer_batch.py /path/to/logfile.log
    
环境变量配置 (.env):
    WORKER_MODE=grouped              # Worker模式
    WORKER_MAX_GROUPS=4              # 分组数
    WORKER_BATCH_SIZE=50             # 每批任务数
    MONITOR_SELF_TIMEOUT_MINUTES=240 # 子代理自我保护超时
    PROGRESS_INTERVAL_PERCENT=10     # 进度推送间隔

作者: 小虾米
更新: 2026-04-01
"""

import os
import sys
import argparse
from pathlib import Path

# 添加技能目录到路径
sys.path.insert(0, os.path.dirname(__file__))

def load_env():
    """加载.env文件"""
    env_path = Path(__file__).parent / '.env'
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    os.environ.setdefault(key, value)

def main():
    load_env()
    
    parser = argparse.ArgumentParser(description='CS-Analyzer 批量分析')
    parser.add_argument('log_file', help='日志文件路径')
    parser.add_argument('--foreground', action='store_true', help='前台模式（阻塞等待）')
    
    args = parser.parse_args()
    
    # 导入分析模块
    from batch_analyzer import BatchAnalyzer
    
    analyzer = BatchAnalyzer()
    
    if args.foreground:
        # 前台模式：主会话阻塞等待
        analyzer.run_foreground(args.log_file)
    else:
        # 后台模式：启动子代理
        result = analyzer.run_background(args.log_file)
        print(result)

if __name__ == '__main__':
    main()
