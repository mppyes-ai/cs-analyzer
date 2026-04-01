#!/usr/bin/env python3
"""后台监控子代理 - 无限超时轮询 + 进度推送

功能:
1. 无限超时轮询队列状态（内部4小时自我保护）
2. 每10%进度推送消息到飞书
3. 完成后生成并推送完整报告
4. 支持外部取消信号

用法:
    python monitor_agent.py <total_tasks> <log_file>

环境变量:
    MONITOR_SELF_TIMEOUT_MINUTES=240  # 自我保护超时
    PROGRESS_INTERVAL_PERCENT=10       # 进度推送间隔
    PROGRESS_MIN_INTERVAL_SECONDS=60   # 最小推送间隔

作者: 小虾米
更新: 2026-04-01
"""

import os
import sys
import time
import json
import signal
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

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

# 配置
SELF_TIMEOUT_MINUTES = int(os.getenv('MONITOR_SELF_TIMEOUT_MINUTES', '240'))
PROGRESS_INTERVAL = int(os.getenv('PROGRESS_INTERVAL_PERCENT', '10'))
MIN_INTERVAL_SECONDS = int(os.getenv('PROGRESS_MIN_INTERVAL_SECONDS', '60'))

# 取消信号文件
CANCEL_FILE = Path('/tmp/cs_analyzer_cancel')


class MonitorAgent:
    """监控子代理"""
    
    def __init__(self, total_tasks: int, log_file: str):
        self.total_tasks = total_tasks
        self.log_file = log_file
        self.log_name = Path(log_file).name
        self.start_time = datetime.now()
        self.last_progress = -1
        self.last_push_time = datetime.now()
        self.running = True
        
        # 注册信号处理
        signal.signal(signal.SIGTERM, self.handle_signal)
        signal.signal(signal.SIGINT, self.handle_signal)
    
    def handle_signal(self, signum, frame):
        """处理退出信号"""
        print(f"\n⚠️ 收到信号 {signum}，准备退出...")
        self.running = False
    
    def check_cancel(self) -> bool:
        """检查是否收到取消指令"""
        if CANCEL_FILE.exists():
            content = CANCEL_FILE.read_text().strip()
            if content == self.log_name:
                return True
        return False
    
    def send_progress(self, progress: int, completed: int, elapsed_minutes: float):
        """发送进度消息到飞书"""
        # 检查最小间隔
        now = datetime.now()
        if (now - self.last_push_time).total_seconds() < MIN_INTERVAL_SECONDS:
            return
        
        # 计算预计剩余时间
        if progress > 0:
            total_estimated = elapsed_minutes / (progress / 100)
            remaining = total_estimated - elapsed_minutes
        else:
            remaining = 0
        
        # 构建进度条
        filled = int(progress / 10)
        bar = '█' * filled + '░' * (10 - filled)
        
        message = f"""📈 CS-Analyzer 进度更新

├─ 文件: {self.log_name}
├─ 进度: {progress}% {bar}
├─ 已完成: {completed}/{self.total_tasks} 通
├─ 已耗时: {elapsed_minutes:.1f}分钟
└─ 预计剩余: {remaining:.1f}分钟"""
        
        # 发送到飞书（通过OpenClaw message工具）
        self.send_feishu_message(message)
        self.last_push_time = now
        print(f"  推送进度: {progress}%")
    
    def send_completion_report(self, stats: dict):
        """发送完成报告"""
        elapsed = (datetime.now() - self.start_time).total_seconds() / 60
        
        message = f"""✅ CS-Analyzer 分析完成！

┌─────────────────────────────────────┐
│  客服会话质量分析报告                │
├─────────────────────────────────────┤
│  文件: {self.log_name}
│  总会话: {self.total_tasks}通
│  分析时长: {elapsed:.1f}分钟
│  成功率: {stats.get('success_rate', 100):.1f}%
└─────────────────────────────────────┘

📎 详细数据已保存到数据库
   可通过 Streamlit 前端查看完整报告"""
        
        self.send_feishu_message(message)
        print("  推送完成报告")
    
    def send_feishu_message(self, message: str):
        """发送飞书消息"""
        # 通过OpenClaw的message工具发送
        # 这里使用环境变量或配置文件获取目标chat_id
        try:
            # 尝试使用OpenClaw的CLI发送
            import subprocess
            chat_id = os.getenv('FEISHU_CHAT_ID', 'ou_7a8de0e44d0870581478030fb08b1021')
            
            # 写入消息到文件，由主进程读取发送
            msg_file = Path('/tmp/cs_analyzer_messages.jsonl')
            with open(msg_file, 'a') as f:
                f.write(json.dumps({
                    'timestamp': datetime.now().isoformat(),
                    'chat_id': chat_id,
                    'message': message
                }, ensure_ascii=False) + '\n')
        except Exception as e:
            print(f"  消息发送失败: {e}")
    
    def get_queue_stats(self) -> dict:
        """获取队列统计"""
        from task_queue import get_queue_stats
        return get_queue_stats()
    
    def run(self):
        """主循环"""
        print(f"🚀 监控子代理启动")
        print(f"   任务数: {self.total_tasks}")
        print(f"   自我保护超时: {SELF_TIMEOUT_MINUTES}分钟")
        print(f"   进度推送间隔: {PROGRESS_INTERVAL}%")
        
        while self.running:
            # 1. 检查自我保护超时
            elapsed = (datetime.now() - self.start_time).total_seconds() / 60
            if SELF_TIMEOUT_MINUTES > 0 and elapsed > SELF_TIMEOUT_MINUTES:
                print(f"\n⏰ 自我保护超时（{SELF_TIMEOUT_MINUTES}分钟），退出")
                self.send_feishu_message(f"⚠️ 分析超时（{SELF_TIMEOUT_MINUTES}分钟），请检查Worker状态")
                break
            
            # 2. 检查取消信号
            if self.check_cancel():
                print("\n🛑 收到取消指令，退出")
                CANCEL_FILE.unlink(missing_ok=True)
                break
            
            # 3. 获取队列状态
            stats = self.get_queue_stats()
            completed = stats.get('completed', 0)
            pending = stats.get('pending', 0)
            processing = stats.get('processing', 0)
            
            # 4. 计算进度
            if self.total_tasks > 0:
                progress = int((completed / self.total_tasks) * 100)
            else:
                progress = 100
            
            # 5. 检查是否需要推送进度
            if progress >= self.last_progress + PROGRESS_INTERVAL:
                self.send_progress(progress, completed, elapsed)
                self.last_progress = (progress // PROGRESS_INTERVAL) * PROGRESS_INTERVAL
            
            # 6. 完成检查
            if pending == 0 and processing == 0 and completed >= self.total_tasks:
                print(f"\n✅ 所有任务完成")
                self.send_completion_report(stats)
                break
            
            # 7. 等待
            time.sleep(10)
        
        print("👋 监控子代理退出")


def main():
    if len(sys.argv) < 3:
        print("用法: python monitor_agent.py <total_tasks> <log_file>")
        sys.exit(1)
    
    total_tasks = int(sys.argv[1])
    log_file = sys.argv[2]
    
    agent = MonitorAgent(total_tasks, log_file)
    agent.run()


if __name__ == '__main__':
    main()
