#!/usr/bin/env python3
"""批量分析控制器 - 支持前台/后台模式

核心功能:
1. 解析日志并批量提交任务
2. 支持前台阻塞模式和后台子代理模式
3. 子代理无限超时轮询 + 进度推送
4. 队列幂等性检查（避免重复分析）

作者: 小虾米
更新: 2026-04-01
"""

import os
import sys
import time
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional

sys.path.insert(0, os.path.dirname(__file__))

from log_parser import parse_log_file
from task_queue import submit_task, get_queue_stats, get_task_detail, init_queue_tables

# 加载环境变量
def load_env():
    env_path = Path(__file__).parent / '.env'
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    os.environ.setdefault(key, value)

load_env()

# 配置项（从环境变量读取）
CONFIG = {
    'worker_mode': os.getenv('WORKER_MODE', 'grouped'),
    'max_groups': int(os.getenv('WORKER_MAX_GROUPS', '4')),
    'max_workers': int(os.getenv('WORKER_MAX_WORKERS', '3')),
    'batch_size': int(os.getenv('WORKER_BATCH_SIZE', '50')),
    'poll_interval': float(os.getenv('WORKER_POLL_INTERVAL', '2.0')),
    'merge_window': int(os.getenv('MERGE_WINDOW_MINUTES', '30')),
    'monitor_timeout': int(os.getenv('MONITOR_SELF_TIMEOUT_MINUTES', '240')),
    'progress_interval': int(os.getenv('PROGRESS_INTERVAL_PERCENT', '10')),
    'progress_min_interval': int(os.getenv('PROGRESS_MIN_INTERVAL_SECONDS', '60')),
}


class BatchAnalyzer:
    """批量分析控制器"""
    
    def __init__(self):
        self.queue_db_path = Path(__file__).parent / 'data' / 'task_queue.db'
        init_queue_tables()
    
    def is_already_analyzed(self, session_id: str) -> bool:
        """检查会话是否已分析完成"""
        conn = sqlite3.connect(self.queue_db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT status FROM analysis_tasks WHERE session_id = ? AND status = 'completed'",
            (session_id,)
        )
        result = cursor.fetchone()
        conn.close()
        return result is not None
    
    def check_worker_running(self) -> bool:
        """检查Worker是否在运行"""
        import socket
        import errno
        
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.bind('/tmp/cs_analyzer_worker.sock')
            sock.close()
            return False
        except socket.error as e:
            if e.args[0] == errno.EADDRINUSE or "Address already in use" in str(e):
                return True
            return False
    
    def start_worker(self) -> bool:
        """启动Worker进程"""
        import subprocess
        
        cmd = [
            sys.executable,
            str(Path(__file__).parent / 'worker.py'),
            f'--{CONFIG["worker_mode"]}',
            '--once',
            f'--max-groups={CONFIG["max_groups"]}',
            f'--batch-size={CONFIG["batch_size"]}',
        ]
        
        try:
            subprocess.Popen(
                cmd,
                stdout=open('/tmp/worker.log', 'a'),
                stderr=subprocess.STDOUT,
                start_new_session=True
            )
            return True
        except Exception as e:
            print(f"❌ 启动Worker失败: {e}")
            return False
    
    def submit_sessions(self, sessions: List[Dict]) -> Dict:
        """批量提交会话，带幂等性检查"""
        submitted = 0
        skipped = 0
        task_ids = []
        
        for session in sessions:
            session_id = session['session_id']
            
            # 幂等性检查
            if self.is_already_analyzed(session_id):
                skipped += 1
                continue
            
            # 提交任务
            task_id = submit_task(session_id=session_id, session_data=session)
            task_ids.append(task_id)
            submitted += 1
            
            # 每100个打印进度
            if submitted % 100 == 0:
                print(f"  已提交 {submitted} 个任务...")
        
        return {
            'submitted': submitted,
            'skipped': skipped,
            'total': len(sessions),
            'task_ids': task_ids
        }
    
    def run_foreground(self, log_file: str) -> str:
        """前台模式：阻塞等待完成"""
        # 1. 解析日志
        print(f"📂 解析日志: {log_file}")
        sessions = parse_log_file(log_file)
        print(f"✅ 解析完成: {len(sessions)} 通会话")
        
        # 2. 检查/启动Worker
        if not self.check_worker_running():
            print("🚀 启动Worker...")
            self.start_worker()
            time.sleep(2)  # 等待Worker启动
        
        # 3. 提交任务
        print("📤 提交任务到队列...")
        result = self.submit_sessions(sessions)
        print(f"✅ 提交完成: {result['submitted']} 个新任务, {result['skipped']} 个已存在")
        
        # 4. 轮询等待
        print("⏳ 等待分析完成...")
        total = result['submitted']
        last_progress = -1
        
        while True:
            stats = get_queue_stats()
            completed = stats.get('completed', 0)
            pending = stats.get('pending', 0)
            processing = stats.get('processing', 0)
            
            # 计算进度（基于已完成的任务数）
            if total > 0:
                progress = int((completed / total) * 100)
            else:
                progress = 100
            
            # 打印进度
            if progress != last_progress and progress % 10 == 0:
                print(f"  进度: {progress}% ({completed}/{total})")
                last_progress = progress
            
            # 完成检查
            if pending == 0 and processing == 0:
                print(f"\n✅ 分析完成!")
                break
            
            time.sleep(5)
        
        # 5. 生成报告
        return self.generate_report(sessions)
    
    def run_background(self, log_file: str) -> str:
        """后台模式：启动子代理，立即返回"""
        # 1. 解析日志
        sessions = parse_log_file(log_file)
        total = len(sessions)
        
        # 2. 检查/启动Worker
        worker_started = False
        if not self.check_worker_running():
            self.start_worker()
            worker_started = True
            time.sleep(2)
        
        # 3. 提交任务
        result = self.submit_sessions(sessions)
        
        # 4. 启动子代理（后台监控）
        self.spawn_monitor_agent(
            total_tasks=result['submitted'],
            log_file=log_file
        )
        
        # 5. 立即返回
        return f"""📊 CS-Analyzer 批量分析已启动

├─ 日志文件: {Path(log_file).name}
├─ 总会话: {total}通
├─ 新提交: {result['submitted']}个任务
├─ 已存在: {result['skipped']}个（跳过）
├─ Worker: {'已启动' if worker_started else '运行中'}
├─ 预计耗时: 约{result['submitted'] * 1.2 / 60:.0f}分钟
└─ 监控代理: 已启动

💡 后台分析进行中，进度将推送到飞书。
   您可以关闭窗口，完成后将收到完整报告。"""
    
    def spawn_monitor_agent(self, total_tasks: int, log_file: str):
        """启动后台监控子代理"""
        import subprocess
        
        # 构建子代理命令
        monitor_script = Path(__file__).parent / 'monitor_agent.py'
        
        cmd = [
            sys.executable,
            str(monitor_script),
            str(total_tasks),
            log_file
        ]
        
        # 启动子代理（独立进程，无限超时）
        subprocess.Popen(
            cmd,
            stdout=open('/tmp/monitor.log', 'a'),
            stderr=subprocess.STDOUT,
            start_new_session=True
        )
    
    def generate_report(self, sessions: List[Dict]) -> str:
        """生成分析报告"""
        # TODO: 从数据库读取结果，生成完整报告
        return "分析报告生成完成"


if __name__ == '__main__':
    analyzer = BatchAnalyzer()
    # 测试用
    result = analyzer.run_foreground('/Users/jinlu/Desktop/小虾米专属文档/客服聊天记录/客服聊天记录(10).log')
    print(result)
