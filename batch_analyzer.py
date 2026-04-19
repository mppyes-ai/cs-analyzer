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
import subprocess

sys.path.insert(0, os.path.dirname(__file__))

from log_parser import parse_log_file
from task_queue import submit_task, get_queue_stats, get_task_detail, init_queue_tables, get_queue_connection
from scene_utils import classify_scene_by_keywords  # 【P1-1修复】提取到独立模块


# ========== 场景分类函数已提取到 scene_utils.py ==========
# 删除本地的 _fast_scene_classify 函数，使用 scene_utils.classify_scene_by_keywords

# ========== 日志目录配置 ==========
LOGS_DIR = os.path.join(os.path.dirname(__file__), 'logs')
os.makedirs(LOGS_DIR, exist_ok=True)

# 加载.env文件
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
    'worker_mode': os.getenv('WORKER_MODE', 'async-batch'),
    'max_groups': int(os.getenv('WORKER_MAX_GROUPS', '10')),
    'max_workers': int(os.getenv('WORKER_MAX_WORKERS', '3')),
    # 【v2.6.2】废弃 WORKER_BATCH_SIZE，改为 WORKER_MAX_BATCH_SIZE
    'batch_size': int(os.getenv('WORKER_MAX_BATCH_SIZE', 
                                os.getenv('WORKER_BATCH_SIZE', '150'))),  # 兼容旧配置
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
        """检查会话是否已分析或在有效队列中（带超时检测）
        
        防止重复提交，同时处理Worker崩溃导致的processing任务残留
        """
        conn = get_queue_connection()
        cursor = conn.cursor()
        
        # 1. 检查是否已完成
        cursor.execute(
            "SELECT 1 FROM analysis_tasks WHERE session_id = ? AND status = 'completed'",
            (session_id,)
        )
        if cursor.fetchone():
            conn.close()
            return True
        
        # 2. 检查是否有"活跃"的pending任务，或"未超时"的processing任务
        # processing超过15分钟认为超时（Worker可能已崩溃）
        cursor.execute('''
            SELECT 1 FROM analysis_tasks 
            WHERE session_id = ? 
            AND (
                status = 'pending'
                OR (status = 'processing' AND started_at > datetime('now', '-15 minutes'))
            )
        ''', (session_id,))
        
        result = cursor.fetchone()
        conn.close()
        return result is not None
    
    def reset_stale_tasks(self) -> int:
        """重置超时的processing任务为pending（Worker崩溃恢复）
        
        Returns:
            重置的任务数量
        """
        conn = get_queue_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE analysis_tasks
            SET status = 'pending', 
                started_at = NULL,
                error = 'Worker超时重置'
            WHERE status = 'processing'
            AND started_at < datetime('now', '-15 minutes')
        ''')
        
        count = cursor.rowcount
        conn.commit()
        conn.close()
        
        if count > 0:
            print(f"🔄 重置 {count} 个超时任务为pending状态")
        return count
    
    def check_worker_running(self) -> bool:
        """检查Worker是否在运行（PID文件 + 进程存在性双重检测）"""
        import os
        
        PID_FILE = os.path.join(LOGS_DIR, 'cs_analyzer_worker.pid')
        
        if os.path.exists(PID_FILE):
            try:
                with open(PID_FILE, 'r') as f:
                    pid = int(f.read().strip())
                
                # 检查进程是否真的存在
                try:
                    os.kill(pid, 0)
                    return True  # PID存在且进程存在
                except (ProcessLookupError, OSError):
                    # 进程不存在，清理残留
                    print(f"🧹 清理残留锁文件 (PID {pid} 已不存在)")
                    os.unlink(PID_FILE)
                    return False
            except (ValueError, IOError) as e:
                # PID文件损坏
                print(f"🧹 清理损坏的锁文件: {e}")
                try:
                    os.unlink(PID_FILE)
                except:
                    pass
                return False
        
        return False
    
    def _clear_message_files(self):
        """清理旧消息文件，避免残留消息干扰新分析"""
        import os
        msg_file = os.path.join(LOGS_DIR, 'cs_analyzer_messages.jsonl')
        processed_file = os.path.join(LOGS_DIR, 'cs_analyzer_messages_processed.jsonl')
        
        for f in [msg_file, processed_file]:
            if os.path.exists(f):
                os.unlink(f)
                print(f"  🧹 清理旧消息文件: {f}")

    def start_worker(self) -> bool:
        """启动Worker进程，并检测是否成功存活"""
        import subprocess
        import time
        
        worker_mode = CONFIG["worker_mode"]
        
        # 【v2.6.2】支持async-batch模式，使用新的 --max-batch-size 参数
        if worker_mode == 'async-batch':
            cmd = [
                sys.executable,
                str(Path(__file__).parent / 'worker.py'),
                '--async-batch',
                '--once',
                f'--max-groups={CONFIG["max_groups"]}',
                f'--max-batch-size={CONFIG["batch_size"]}',
                f'--score-batch-size={os.getenv("BATCH_SCORE_SIZE", "30")}',
            ]
            print(f"🚀 启动Worker [异步批量模式 v2.6.2]")
        else:
            cmd = [
                sys.executable,
                str(Path(__file__).parent / 'worker.py'),
                f'--{worker_mode}',
                '--once',
                f'--max-groups={CONFIG["max_groups"]}',
                f'--max-batch-size={CONFIG["batch_size"]}',
            ]
            print(f"🚀 启动Worker [{worker_mode}模式]")
        
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=open(os.path.join(LOGS_DIR, 'worker.log'), 'a', buffering=1),
                stderr=subprocess.STDOUT,
                start_new_session=True
            )
            
            # 【v2.6.3新增】等待3秒，检查Worker是否存活
            print("⏳ 等待Worker初始化...")
            time.sleep(3)
            
            # 检查进程是否还在运行
            exit_code = proc.poll()
            if exit_code is not None:
                # 进程已退出，启动失败
                error_msg = f"Worker启动后立即退出（退出码: {exit_code}）"
                print(f"❌ {error_msg}")
                print("   请检查 logs/worker.log 查看详细错误")
                
                # 发送飞书通知
                self._send_failure_notification(error_msg)
                return False
            
            print(f"✅ Worker启动成功（PID: {proc.pid}）")
            return True
            
        except Exception as e:
            print(f"❌ 启动Worker失败: {e}")
            self._send_failure_notification(f"启动异常: {e}")
            return False
    
    def _send_failure_notification(self, error_msg: str):
        """发送启动失败通知到飞书"""
        try:
            msg_file = Path(os.path.join(LOGS_DIR, 'cs_analyzer_messages.jsonl'))
            import json
            from datetime import datetime
            
            message = f"""⚠️ CS-Analyzer 启动失败

错误信息：{error_msg}

请检查：
1. 运行环境依赖是否完整
2. logs/worker.log 中的详细错误
3. .env 文件配置是否正确

建议修复命令：
python3 -m pip install python-dotenv openai pandas sentence-transformers httpx scikit-learn numpy"""
            
            with open(msg_file, 'a') as f:
                f.write(json.dumps({
                    'timestamp': datetime.now().isoformat(),
                    'message': message
                }, ensure_ascii=False) + '\n')
        except Exception:
            pass  # 通知失败不影响主流程
    
    def _filter_existing_sessions(self, sessions: List[Dict]) -> tuple:
        """【优化】批量检查会话是否已存在，返回(待提交列表, 已存在数量)
        
        替代逐条is_already_analyzed检查，将N次查询降为1次
        """
        if not sessions:
            return [], 0
        
        # 提取所有session_id
        session_ids = [s['session_id'] for s in sessions]
        existing_set = set()
        
        conn = get_queue_connection()
        cursor = conn.cursor()
        
        # 分批查询避免SQL参数过多（SQLite限制999个）
        batch_size = 900
        for i in range(0, len(session_ids), batch_size):
            batch = session_ids[i:i+batch_size]
            placeholders = ','.join(['?' for _ in batch])
            
            # 一次性查询：已completed，或活跃pending/processing
            cursor.execute(f'''
                SELECT session_id FROM analysis_tasks 
                WHERE session_id IN ({placeholders})
                AND (
                    status = 'completed'
                    OR status = 'pending'
                    OR (status = 'processing' AND started_at > datetime('now', '-15 minutes'))
                )
            ''', batch)
            
            existing_set.update(row[0] for row in cursor.fetchall())
        
        conn.close()
        
        # 过滤已存在的会话
        new_sessions = [s for s in sessions if s['session_id'] not in existing_set]
        skipped_count = len(existing_set)
        
        return new_sessions, skipped_count

    def submit_sessions(self, sessions: List[Dict], batch_id: str = '') -> Dict:
        """批量提交会话，带幂等性检查、场景分类和前置过滤"""
        submitted = 0
        skipped = 0
        filtered_empty = 0  # 【新增】无用户消息
        filtered_short = 0  # 【新增】超短会话
        task_ids = []
        
        # 【优化】批量幂等检查：N次查询→1次查询
        sessions, skipped = self._filter_existing_sessions(sessions)
        if skipped > 0:
            print(f"   ⏭️ 跳过已存在: {skipped} 通")
        
        for session in sessions:
            session_id = session['session_id']
            messages = session.get('messages', [])
            
            # 【前置过滤】检查用户消息
            user_msgs = [m for m in messages if m.get('role') in ('user', 'customer')]
            
            # 过滤1：无用户消息的会话（纯客服独白）
            if not user_msgs:
                filtered_empty += 1
                print(f"   🚫 过滤: {session_id[:20]}... (无用户消息)")
                continue
            
            # 过滤2：超短会话（总消息数<3且用户消息<2）
            if len(messages) < 3 and len(user_msgs) < 2:
                filtered_short += 1
                print(f"   🚫 过滤: {session_id[:20]}... (超短会话)")
                continue
            
            # 【修复】场景分类并持久化
            scene = classify_scene_by_keywords(messages)
            
            # 提交任务（带场景）
            task_id = submit_task(
                session_id=session_id, 
                session_data=session, 
                batch_id=batch_id,
                scene=scene
            )
            task_ids.append(task_id)
            submitted += 1
            
            # 每100个打印进度
            if submitted % 100 == 0:
                print(f"  已提交 {submitted} 个任务...")
        
        # 【新增】打印过滤统计
        total_filtered = filtered_empty + filtered_short
        if total_filtered > 0:
            print(f"\n📊 前置过滤统计:")
            print(f"   无用户消息: {filtered_empty} 通")
            print(f"   超短会话: {filtered_short} 通")
            print(f"   共计过滤: {total_filtered} 通")
        
        return {
            'submitted': submitted,
            'skipped': skipped,
            'filtered_empty': filtered_empty,
            'filtered_short': filtered_short,
            'total': len(sessions) + skipped,  # 原始总数
            'task_ids': task_ids
        }
    
    def run_foreground(self, log_file: str) -> str:
        """前台模式：阻塞等待完成，同时启动监控代理推送进度"""
        # 0. 清理旧消息文件（避免显示残留进度）
        self._clear_message_files()
        
        # 1. 重置超时任务（Worker崩溃恢复）
        self.reset_stale_tasks()
        
        # 1. 解析日志
        print(f"📂 解析日志: {log_file}")
        sessions = parse_log_file(log_file)
        print(f"✅ 解析完成: {len(sessions)} 通会话")
        
        # 2. 检查/启动Worker
        if not self.check_worker_running():
            print("🚀 启动Worker...")
            if not self.start_worker():
                return "❌ Worker启动失败，无法继续分析。请检查依赖安装。"
            time.sleep(2)  # 等待Worker启动
        
        # 3. 生成批次ID并提交任务（前台模式也需要batch_id）
        import uuid
        batch_id = str(uuid.uuid4())[:8]
        print("📤 提交任务到队列...")
        result = self.submit_sessions(sessions, batch_id=batch_id)
        print(f"✅ 提交完成: {result['submitted']} 个新任务, {result['skipped']} 个已存在")
        
        # 4. 前台模式不启动监控代理（前台自己管理进度显示）
        # 后台模式才需要监控代理推送进度
        pass
        
        # 5. 轮询等待（使用当前批次队列统计）
        print("⏳ 等待分析完成...")
        total = result['submitted']
        last_progress = -1
        start_time = time.time()
        max_wait_seconds = 30 * 60  # 30分钟超时
        
        while True:
            # 获取当前批次统计
            stats = get_queue_stats(batch_id=batch_id)
            completed = stats.get('completed', 0)
            pending = stats.get('pending', 0)
            processing = stats.get('processing', 0)
            
            # 超时保护
            if time.time() - start_time > max_wait_seconds:
                print(f"\n❌ 分析超时（30分钟）")
                print(f"   当前队列状态: pending={pending}, processing={processing}, completed={completed}")
                print(f"   可能原因: Worker未正常运行或任务处理卡住")
                return "❌ 分析超时（30分钟），Worker可能未正常运行。"
            
            # 计算进度（使用当前批次completed）
            if total > 0:
                progress = int((completed / total) * 100)
            else:
                progress = 100
            
            # 打印进度（每10%显示一次）
            if progress != last_progress and progress % 10 == 0:
                print(f"  进度: {progress}% ({completed}/{total})")
                last_progress = progress
            
            # 完成检查：只看当前批次的 pending 和 processing
            if pending == 0 and processing == 0:
                # 额外等待3秒，确保Worker完成数据库写入
                print(f"  所有任务已标记完成，等待数据写入...")
                time.sleep(3)
                print(f"\n✅ 分析完成!")
                break
            
            time.sleep(5)
        
        # 6. 生成报告
        return self.generate_report(sessions)
    
    def run_background(self, log_file: str) -> str:
        """后台模式：启动子代理，立即返回"""
        # 0. 清理旧消息文件（避免显示残留进度）
        self._clear_message_files()
        
        # 1. 重置超时任务（Worker崩溃恢复）
        self.reset_stale_tasks()
        
        # 1. 解析日志
        sessions = parse_log_file(log_file)
        total = len(sessions)
        
        # 2. 检查/启动Worker
        worker_started = False
        if not self.check_worker_running():
            if not self.start_worker():
                return "❌ Worker启动失败，无法继续分析。请检查依赖安装。"
            worker_started = True
            time.sleep(2)
        
        # 3. 生成批次ID并提交任务
        import uuid
        batch_id = str(uuid.uuid4())[:8]
        result = self.submit_sessions(sessions, batch_id=batch_id)
        
        # 4. 启动子代理（后台监控，传递batch_id）
        self.spawn_monitor_agent(
            total_tasks=result['submitted'],
            log_file=log_file,
            batch_id=batch_id
        )
        
        # 5. 立即返回（后台模式：只返回启动状态，由monitor自动推送进度和报告）
        return f"""📊 CS-Analyzer 批量分析已启动（后台模式）

├─ 日志文件: {Path(log_file).name}
├─ 总会话: {total}通
├─ 新提交: {result['submitted']}个任务
├─ 已存在: {result['skipped']}个（跳过）
├─ Worker: {'已启动' if worker_started else '运行中'}
├─ 预计耗时: 约{result['submitted'] * 1.2 / 60:.0f}分钟
├─ 监控代理: 已启动
└─ 消息服务: 已启动

📱 进度通知: 每10%自动推送到飞书
📋 完成报告: 分析结束后自动推送完整统计

💡 提示: 后台分析进行中，您可以关闭窗口。"""
    
    def spawn_monitor_agent(self, total_tasks: int, log_file: str, batch_id: str = ''):
        """启动后台监控子代理和消息轮询服务"""
        import subprocess
        import os
        
        # 构建子代理命令
        monitor_script = Path(__file__).parent / 'monitor_agent.py'
        
        cmd = [
            sys.executable,
            str(monitor_script),
            str(total_tasks),
            log_file,
            batch_id
        ]
        
        # 【修复】设置UTF-8编码环境变量，避免中文路径乱码
        env = os.environ.copy()
        env['PYTHONIOENCODING'] = 'utf-8'
        env['LC_ALL'] = 'en_US.UTF-8'
        
        # 启动子代理（独立进程，无限超时）
        monitor_proc = subprocess.Popen(
            cmd,
            stdout=open(os.path.join(LOGS_DIR, 'monitor.log'), 'a'),
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=env  # 【修复】传递编码环境变量
        )
        
        # 启动消息轮询服务（传递 monitor_agent 的 PID）
        self.spawn_message_poller(monitor_proc.pid)
    
    def spawn_message_poller(self, monitor_pid: int) -> subprocess.Popen:
        """启动消息轮询服务（增强版：单例检查 + 返回进程对象）
        
        【P0修复】启动前检查是否已有实例在运行，防止多实例竞态
        
        Args:
            monitor_pid: monitor_agent 的进程ID，用于生命周期检测
            
        Returns:
            subprocess.Popen: 消息轮询服务进程对象（如果已有实例则返回None）
        """
        import subprocess
        
        # 【P0修复】检查是否已有 poller 在运行
        if self.check_message_poller_running():
            print("⚠️ 消息轮询服务已在运行，跳过重复启动")
            return None
        
        poller_script = Path(__file__).parent / 'message_poller.py'
        
        cmd = [
            sys.executable,
            str(poller_script),
            str(monitor_pid)
        ]
        
        # 启动消息轮询服务
        proc = subprocess.Popen(
            cmd,
            stdout=open(os.path.join(LOGS_DIR, 'message_poller.log'), 'a'),
            stderr=subprocess.STDOUT,
            start_new_session=True
        )
        
        print(f"📨 消息轮询服务已启动（监控 PID: {monitor_pid}, 服务 PID: {proc.pid}）")
        return proc
    
    def check_message_poller_running(self) -> bool:
        """检查消息轮询服务是否在运行（PID文件 + 进程存在性检测）"""
        PID_FILE = os.path.join(LOGS_DIR, 'cs_analyzer_message_poller.pid')
        
        if os.path.exists(PID_FILE):
            try:
                with open(PID_FILE, 'r') as f:
                    pid = int(f.read().strip())
                
                # 检查进程是否真的存在
                try:
                    os.kill(pid, 0)
                    return True
                except (ProcessLookupError, OSError):
                    # 进程不存在，清理残留
                    print(f"🧹 清理消息服务残留锁文件 (PID {pid} 已不存在)")
                    os.unlink(PID_FILE)
                    return False
            except (ValueError, IOError) as e:
                print(f"🧹 清理消息服务损坏的锁文件: {e}")
                try:
                    os.unlink(PID_FILE)
                except:
                    pass
                return False
        
        return False
    
    def restart_message_poller_if_needed(self, monitor_pid: int) -> bool:
        """检查并重启消息轮询服务（如果需要）"""
        if not self.check_message_poller_running():
            print("⚠️ 消息轮询服务已停止，正在重启...")
            self.spawn_message_poller(monitor_pid)
            return True
        return False
    
    def generate_report(self, sessions: List[Dict]) -> str:
        """生成分析报告"""
        # TODO: 从数据库读取结果，生成完整报告
        return "分析报告生成完成"


if __name__ == '__main__':
    analyzer = BatchAnalyzer()
    # 测试用
    result = analyzer.run_foreground('/Users/jinlu/Desktop/小虾米专属文档/客服聊天记录/客服聊天记录(10).log')
    print(result)
