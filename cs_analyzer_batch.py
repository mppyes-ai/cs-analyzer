#!/usr/bin/env python3
"""CS-Analyzer 批量分析入口 - 支持后台子代理模式

用法:
    python3 cs_analyzer_batch.py /path/to/logfile.log
    
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
    parser.add_argument('--foreground', action='store_true', 
                        help='前台模式（阻塞等待，仅调试使用，不推荐生产环境）')
    parser.add_argument('--mode', choices=['auto', 'foreground', 'background'], 
                        default='auto',
                        help='执行模式：auto=根据文件大小自动选择(默认), foreground=前台, background=后台')
    
    args = parser.parse_args()
    
    # 导入分析模块
    from batch_analyzer import BatchAnalyzer
    
    analyzer = BatchAnalyzer()
    
    # 模式决策逻辑
    if args.mode == 'foreground' or args.foreground:
        # 前台模式：显式确认
        print("⚠️  前台模式已选择（--foreground）")
        print("   特点：阻塞等待、控制台输出、适合调试")
        print("   注意：不会接收飞书进度推送")
        analyzer.run_foreground(args.log_file)
    elif args.mode == 'background':
        # 后台模式
        result = analyzer.run_background(args.log_file)
        print(result)
    else:
        # auto模式：根据文件大小自动选择（小文件<10会话可用前台）
        # 但默认推荐后台模式
        result = analyzer.run_background(args.log_file)
        print(result)

if __name__ == '__main__':
    main()
