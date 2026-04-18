#!/usr/bin/env python3
"""后台监控子代理 v2.3 - 无限超时轮询 + 进度推送 + 发送端去重

功能:
1. 无限超时轮询队列状态（内部4小时自我保护）
2. 每10%进度推送消息到飞书
3. 完成后生成并推送完整报告
4. 支持外部取消信号
5. 【v2.3】发送端消息去重（防止同一内容多次入队）

用法:
    python3 monitor_agent.py <total_tasks> <log_file>

环境变量:
    MONITOR_SELF_TIMEOUT_MINUTES=240  # 自我保护超时
    PROGRESS_INTERVAL_PERCENT=10       # 进度推送间隔
    PROGRESS_MIN_INTERVAL_SECONDS=60   # 最小推送间隔

作者: 小虾米
更新: 2026-04-06 (v2.3 发送端去重)
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

# 日志目录配置
LOGS_DIR = Path(os.path.join(os.path.dirname(__file__), 'logs'))
LOGS_DIR.mkdir(exist_ok=True)

# 取消信号文件
CANCEL_FILE = LOGS_DIR / 'cs_analyzer_cancel'
MONITOR_PID_FILE = LOGS_DIR / 'cs_analyzer_monitor.pid'


class MonitorAgent:
    """监控子代理（增强版：带消息服务健康检查）"""
    
    def __init__(self, total_tasks: int, log_file: str, batch_id: str = ''):
        self.total_tasks = total_tasks
        self.log_file = log_file
        self.log_name = Path(log_file).name
        self.batch_id = batch_id
        self.start_time = datetime.now()
        self.last_progress = -1
        self.last_push_time = datetime.now()
        self.running = True
        self.message_poller_restart_count = 0  # 重启计数
        
        # 【v2.3新增】发送端去重：记录已发送的消息内容指纹
        self._sent_message_fingerprints: set = set()
        
        # 注册信号处理
        signal.signal(signal.SIGTERM, self.handle_signal)
        signal.signal(signal.SIGINT, self.handle_signal)
    
    def check_message_poller_health(self) -> bool:
        """检查消息轮询服务健康状态"""
        PID_FILE = LOGS_DIR / 'cs_analyzer_message_poller.pid'
        
        if not PID_FILE.exists():
            return False
        
        try:
            with open(PID_FILE, 'r') as f:
                pid = int(f.read().strip())
            
            # 检查进程是否存在
            os.kill(pid, 0)
            return True
        except (ValueError, ProcessLookupError, OSError):
            # 进程不存在，清理残留文件
            try:
                PID_FILE.unlink(missing_ok=True)
            except:
                pass
            return False
    
    def restart_message_poller(self):
        """重启消息轮询服务（带单例检查）"""
        import subprocess
        from pathlib import Path
        
        poller_script = Path(__file__).parent / 'message_poller.py'
        
        # 【P0修复】使用文件锁方式严格检查是否已有实例
        PID_FILE = LOGS_DIR / 'cs_analyzer_message_poller.pid'
        if PID_FILE.exists():
            try:
                with open(PID_FILE, 'r') as f:
                    old_pid = int(f.read().strip())
                os.kill(old_pid, 0)  # 检查进程是否存在
                print(f"  ⚠️ 消息服务已在运行 (PID: {old_pid})，跳过重启")
                return True
            except (ValueError, ProcessLookupError, OSError):
                # 进程不存在，清理残留文件后继续启动
                print(f"  🧹 清理残留PID文件")
                PID_FILE.unlink(missing_ok=True)
        
        cmd = [
            sys.executable,
            str(poller_script),
            str(os.getpid())  # 传递当前monitor的PID
        ]
        
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=open(LOGS_DIR / 'message_poller.log', 'a'),
                stderr=subprocess.STDOUT,
                start_new_session=True
            )
            self.message_poller_restart_count += 1
            print(f"  🔄 消息服务已重启 (PID: {proc.pid}, 第{self.message_poller_restart_count}次)")
            return True
        except Exception as e:
            print(f"  ❌ 消息服务重启失败: {e}")
            return False
    
    def check_existing_monitor(self) -> bool:
        """检查是否已有monitor在运行"""
        if MONITOR_PID_FILE.exists():
            try:
                with open(MONITOR_PID_FILE, 'r') as f:
                    pid = int(f.read().strip())
                os.kill(pid, 0)  # 检查进程是否存在
                print(f"⚠️ 已有monitor在运行 (PID: {pid})，本次不启动新monitor")
                return True
            except (ValueError, ProcessLookupError, OSError):
                # 进程不存在，清理残留文件
                MONITOR_PID_FILE.unlink(missing_ok=True)
        return False
    
    def write_pid_file(self):
        """写入PID文件"""
        MONITOR_PID_FILE.write_text(str(os.getpid()))
    
    def remove_pid_file(self):
        """清理PID文件"""
        MONITOR_PID_FILE.unlink(missing_ok=True)
    
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
    
    def send_progress(self, progress: int, completed: int, elapsed_minutes: float) -> bool:
        """发送进度消息到飞书
        
        Returns:
            bool: 是否成功发送（时间间隔满足且写入成功）
        """
        # 检查最小间隔
        now = datetime.now()
        if (now - self.last_push_time).total_seconds() < MIN_INTERVAL_SECONDS:
            return False
        
        # 确保进度只增不减，且按PROGRESS_INTERVAL阈值推送
        # 将进度向下取整到最近的PROGRESS_INTERVAL倍数
        report_progress = (progress // PROGRESS_INTERVAL) * PROGRESS_INTERVAL
        
        # 如果已经报告过这个进度，跳过
        if report_progress <= self.last_progress:
            return False
        
        # 计算预计剩余时间
        if progress > 0:
            total_estimated = elapsed_minutes / (progress / 100)
            remaining = total_estimated - elapsed_minutes
        else:
            remaining = 0
        
        # 构建进度条
        filled = int(report_progress / 10)
        bar = '█' * filled + '░' * (10 - filled)
        
        message = f"""📈 CS-Analyzer 进度更新

├─ 文件: {self.log_name}
├─ 进度: {report_progress}% {bar}
├─ 已完成: {completed}/{self.total_tasks} 通
├─ 已耗时: {elapsed_minutes:.1f}分钟
└─ 预计剩余: {remaining:.1f}分钟"""
        
        # 发送到飞书（通过OpenClaw message工具）
        success = self.send_feishu_message(message)
        if success:
            self.last_push_time = now
            self.last_progress = report_progress
            print(f"  推送进度: {report_progress}%")
        return success
    
    def send_completion_report(self, stats: dict):
        """发送完成报告（包含详细统计）"""
        elapsed = (datetime.now() - self.start_time).total_seconds() / 60
        
        # 从数据库读取详细统计
        try:
            detailed_stats = self._get_analysis_stats()
        except Exception as e:
            print(f"  ⚠️ 读取详细统计失败: {e}")
            detailed_stats = None
        
        if detailed_stats:
            # 构建详细报告
            risk_distribution = detailed_stats.get('risk_distribution', {})
            avg_scores = detailed_stats.get('avg_scores', {})
            
            message = f"""✅ CS-Analyzer 分析完成！

┌────────────────────────────────────────┐
│  客服会话质量分析报告                   │
├────────────────────────────────────────┤
│  文件: {self.log_name}
│  总会话: {self.total_tasks}通
│  分析时长: {elapsed:.1f}分钟
│  成功率: {stats.get('success_rate', 100):.1f}%
├────────────────────────────────────────┤
│  📊 四维度平均分                        │
│  • 专业性: {avg_scores.get('prof', 0):.1f}/5
│  • 标准化: {avg_scores.get('std', 0):.1f}/5
│  • 政策执行: {avg_scores.get('policy', 0):.1f}/5
│  • 转化能力: {avg_scores.get('conv', 0):.1f}/5
├────────────────────────────────────────┤
│  🎯 风险分布                            │
│  🔴 高风险(≤8分): {risk_distribution.get('high_risk', 0)}通
│  🟡 中风险(9-12分): {risk_distribution.get('med_risk', 0)}通
│  🟢 正常(≥13分): {risk_distribution.get('normal', 0)}通
└────────────────────────────────────────┘

📎 详细数据已保存到数据库
   可通过 Streamlit 前端查看完整报告"""
        else:
            # 简化报告（数据库读取失败时）
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
    
    def _get_analysis_stats(self) -> dict:
        """从数据库获取分析统计"""
        import sqlite3
        from pathlib import Path
        
        db_path = Path(__file__).parent / 'data' / 'cs_analyzer_new.db'
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        
        # 获取最近分析的一批数据（按created_at倒序，限制为本次分析的数量）
        cursor.execute('''
            SELECT 
                professionalism_score,
                standardization_score,
                policy_execution_score,
                conversion_score,
                total_score
            FROM sessions
            ORDER BY created_at DESC
            LIMIT ?
        ''', (self.total_tasks,))
        
        rows = cursor.fetchall()
        conn.close()
        
        if not rows:
            return None
        
        # 计算统计
        prof_scores = [r[0] for r in rows if r[0] is not None]
        std_scores = [r[1] for r in rows if r[1] is not None]
        policy_scores = [r[2] for r in rows if r[2] is not None]
        conv_scores = [r[3] for r in rows if r[3] is not None]
        total_scores = [r[4] for r in rows if r[4] is not None]
        
        # 风险分布
        high_risk = sum(1 for s in total_scores if s <= 8)
        med_risk = sum(1 for s in total_scores if 9 <= s <= 12)
        normal = sum(1 for s in total_scores if s >= 13)
        
        return {
            'avg_scores': {
                'prof': sum(prof_scores) / len(prof_scores) if prof_scores else 0,
                'std': sum(std_scores) / len(std_scores) if std_scores else 0,
                'policy': sum(policy_scores) / len(policy_scores) if policy_scores else 0,
                'conv': sum(conv_scores) / len(conv_scores) if conv_scores else 0,
            },
            'risk_distribution': {
                'high_risk': high_risk,
                'med_risk': med_risk,
                'normal': normal
            }
        }
    
    def send_feishu_message(self, message: str) -> bool:
        """发送飞书消息
        
        Returns:
            bool: 是否成功写入消息文件
        """
        # 通过OpenClaw的message工具发送
        # 这里使用环境变量或配置文件获取目标chat_id
        try:
            import hashlib
            import re
            
            # 【v2.3.1修复】使用纯进度值作为指纹，避免时间戳导致去重失效
            # 提取进度百分比（如"20%"）
            progress_match = re.search(r'进度:\s*(\d+)%', message)
            if progress_match and 'CS-Analyzer 进度更新' in message:
                # 进度消息：使用 log_name + progress 作为指纹
                progress_val = progress_match.group(1)
                msg_fingerprint = f"{self.log_name}:{progress_val}"
            else:
                # 其他消息（如完成报告）：使用内容MD5
                msg_fingerprint = hashlib.md5(message.encode()).hexdigest()[:16]
            
            if msg_fingerprint in self._sent_message_fingerprints:
                print(f"  [去重] 跳过重复消息: {message[:30]}...")
                return True  # 返回True表示已处理（跳过）
            
            # 尝试使用OpenClaw的CLI发送
            import subprocess
            chat_id = os.getenv('FEISHU_CHAT_ID', 'ou_7a8de0e44d0870581478030fb08b1021')
            
            # 写入消息到文件，由主进程读取发送
            msg_file = LOGS_DIR / 'cs_analyzer_messages.jsonl'
            with open(msg_file, 'a') as f:
                f.write(json.dumps({
                    'timestamp': datetime.now().isoformat(),
                    'chat_id': chat_id,
                    'message': message
                }, ensure_ascii=False) + '\n')
            
            # 【v2.3新增】记录已发送指纹
            self._sent_message_fingerprints.add(msg_fingerprint)
            return True
        except Exception as e:
            print(f"  消息发送失败: {e}")
            return False
    
    def get_queue_stats(self) -> dict:
        """获取队列统计（使用batch_id过滤）"""
        from task_queue import get_queue_stats
        return get_queue_stats(self.batch_id)
    
    def run(self):
        """主循环"""
        # 检查是否已有monitor在运行
        if self.check_existing_monitor():
            return
        
        # 写入PID文件
        self.write_pid_file()
        
        print(f"🚀 监控子代理启动")
        print(f"   任务数: {self.total_tasks}")
        print(f"   批次ID: {self.batch_id}")
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
            
            # 4. 计算进度（直接使用 completed，不再减去 initial_completed）
            # 修复原因：initial_completed 机制在任务快速完成时会导致 current_completed=0
            # 使得完成检测 current_completed >= total_tasks 永远失败
            if self.total_tasks > 0:
                progress = min(100, int((completed / self.total_tasks) * 100))
            else:
                progress = 100
            
            # 5. 检查是否需要推送进度
            # 修复：使用向下取整的阈值，确保每个阈值只推送一次
            target_progress = (progress // PROGRESS_INTERVAL) * PROGRESS_INTERVAL
            if target_progress > self.last_progress:
                self.send_progress(progress, completed, elapsed)
            
            # 6. 健康检查：每5次循环检查一次消息服务
            loop_count = getattr(self, '_loop_count', 0)
            self._loop_count = loop_count + 1
            
            if self._loop_count % 5 == 0:  # 每5次循环（约50秒）
                if not self.check_message_poller_health():
                    print("⚠️ 消息服务异常，准备重启...")
                    self.restart_message_poller()
            
            # 7. 完成检查
            if pending == 0 and processing == 0:
                print(f"\n✅ 所有任务完成")
                self.send_completion_report(stats)
                break
            
            # 8. 等待
            time.sleep(10)
        
        # 注意：循环内已完成报告发送并break，此处不再重复发送
        # 原重复发送代码已移除（Bug H-MSG-011）
        
        print("👋 监控子代理退出")
        self.remove_pid_file()


def main():
    if len(sys.argv) < 3:
        print("用法: python3 monitor_agent.py <total_tasks> <log_file> [batch_id]")
        sys.exit(1)
    
    total_tasks = int(sys.argv[1])
    log_file = sys.argv[2]
    batch_id = sys.argv[3] if len(sys.argv) > 3 else ''
    
    agent = MonitorAgent(total_tasks, log_file, batch_id)
    agent.run()


if __name__ == '__main__':
    main()
