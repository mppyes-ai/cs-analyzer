import json
import os
import sqlite3
import shutil
import glob
from datetime import datetime

class TestTracker:
    """测试追踪器 - 记录每次测试的详细数据"""
    
    def __init__(self, test_round=1):
        self.test_round = test_round
        self.test_time = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.base_dir = f"logs/test_round_{test_round}_{self.test_time}"
        os.makedirs(self.base_dir, exist_ok=True)
        
        # 创建子目录
        self.failed_sessions_dir = os.path.join(self.base_dir, "failed_sessions")
        self.logs_dir = os.path.join(self.base_dir, "logs")
        os.makedirs(self.failed_sessions_dir, exist_ok=True)
        os.makedirs(self.logs_dir, exist_ok=True)
        
        # 汇总数据
        self.summary = {
            "test_round": test_round,
            "test_time": self.test_time,
            "total_tasks": 0,
            "completed": 0,
            "failed": 0,
            "failed_sessions": []
        }
    
    def record_failed_session(self, session_id, session_data, error_info):
        """记录失败会话的详细信息"""
        session_file = os.path.join(self.failed_sessions_dir, f"{session_id}.json")
        
        record = {
            "session_id": session_id,
            "test_round": self.test_round,
            "test_time": self.test_time,
            "session_data": session_data,
            "error_info": error_info
        }
        
        with open(session_file, 'w', encoding='utf-8') as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
        
        self.summary["failed_sessions"].append({
            "session_id": session_id,
            "error": error_info.get("error", "未知错误"),
            "retry_count": error_info.get("retry_count", 0)
        })
    
    def copy_logs(self):
        """复制相关日志文件"""
        # 复制cs-analyzer worker日志
        worker_log = "logs/worker.log"
        if os.path.exists(worker_log):
            shutil.copy2(worker_log, os.path.join(self.logs_dir, "worker.log"))
        
        # 复制oMLX日志（最后1000行）
        omlx_log = os.path.expanduser("~/.omlx/logs/server.log")
        if os.path.exists(omlx_log):
            with open(omlx_log, 'r') as f:
                lines = f.readlines()
                last_lines = lines[-1000:] if len(lines) > 1000 else lines
            
            with open(os.path.join(self.logs_dir, "omlx.log"), 'w') as f:
                f.writelines(last_lines)
    
    def save_summary(self):
        """保存汇总数据"""
        summary_file = os.path.join(self.base_dir, "summary.json")
        with open(summary_file, 'w', encoding='utf-8') as f:
            json.dump(self.summary, f, ensure_ascii=False, indent=2)
    
    def collect_from_database(self):
        """从数据库收集失败会话信息"""
        conn = sqlite3.connect('data/task_queue.db')
        cursor = conn.cursor()
        
        # 获取统计信息
        cursor.execute("SELECT status, COUNT(*) FROM analysis_tasks GROUP BY status")
        for status, count in cursor.fetchall():
            if status == 'completed':
                self.summary["completed"] = count
            elif status == 'failed':
                self.summary["failed"] = count
        
        cursor.execute("SELECT COUNT(*) FROM analysis_tasks")
        self.summary["total_tasks"] = cursor.fetchone()[0]
        
        # 获取失败会话详情
        cursor.execute("""
            SELECT session_id, session_data, error, retry_count 
            FROM analysis_tasks 
            WHERE status='failed'
        """)
        
        for row in cursor.fetchall():
            session_id, session_data_json, error, retry_count = row
            
            try:
                session_data = json.loads(session_data_json) if session_data_json else {}
            except:
                session_data = {}
            
            error_info = {
                "error": error,
                "retry_count": retry_count
            }
            
            self.record_failed_session(session_id, session_data, error_info)
        
        conn.close()
        
        # 复制日志
        self.copy_logs()
        
        # 保存汇总
        self.save_summary()
        
        print(f"✅ 第{self.test_round}轮测试数据已保存至: {self.base_dir}")
        print(f"   成功: {self.summary['completed']}, 失败: {self.summary['failed']}")


class TestAnalyzer:
    """测试分析器 - 汇总多轮测试数据"""
    
    def __init__(self):
        self.test_rounds = []
        self.failed_sessions_history = {}
    
    def load_round(self, round_num):
        """加载某轮测试数据"""
        pattern = f"logs/test_round_{round_num}_*/summary.json"
        matches = glob.glob(pattern)
        
        if not matches:
            print(f"⚠️ 未找到第{round_num}轮测试数据")
            return None
        
        latest = max(matches, key=os.path.getmtime)
        
        with open(latest, 'r') as f:
            summary = json.load(f)
        
        self.test_rounds.append(summary)
        
        for failed in summary.get("failed_sessions", []):
            sid = failed["session_id"]
            if sid not in self.failed_sessions_history:
                self.failed_sessions_history[sid] = []
            self.failed_sessions_history[sid].append({
                "round": round_num,
                "error": failed["error"],
                "retry_count": failed["retry_count"]
            })
        
        return summary
    
    def analyze_patterns(self):
        """分析失败模式"""
        print("\n" + "="*60)
        print("📊 多轮测试失败模式分析")
        print("="*60)
        
        print("\n1. 固定失败会话统计:")
        print("-" * 60)
        
        for sid, history in sorted(self.failed_sessions_history.items(), 
                                   key=lambda x: len(x[1]), reverse=True):
            fail_count = len(history)
            print(f"\n{sid}: 失败{fail_count}次")
            
            session_file = f"logs/test_round_1_*/failed_sessions/{sid}.json"
            matches = glob.glob(session_file)
            if matches:
                with open(matches[0], 'r') as f:
                    session_data = json.load(f)
                    messages = session_data.get("session_data", {}).get("messages", [])
                    print(f"  消息数: {len(messages)}")
                    
                    for i, msg in enumerate(messages[:3]):
                        role = msg.get('role', 'unknown')
                        content = msg.get('content', '')[:50]
                        print(f"  [{i}] {role}: {content}...")
        
        print("\n2. 错误类型统计:")
        print("-" * 60)
        
        error_types = {}
        for sid, history in self.failed_sessions_history.items():
            for h in history:
                error = h["error"]
                if error not in error_types:
                    error_types[error] = 0
                error_types[error] += 1
        
        for error, count in sorted(error_types.items(), key=lambda x: x[1], reverse=True):
            print(f"  {error}: {count}次")
        
        self.generate_report()
    
    def generate_report(self):
        """生成分析报告"""
        report_file = "logs/test_analysis_report.json"
        
        report = {
            "analysis_time": datetime.now().isoformat(),
            "total_rounds": len(self.test_rounds),
            "failed_sessions": {}
        }
        
        for sid, history in self.failed_sessions_history.items():
            report["failed_sessions"][sid] = {
                "fail_count": len(history),
                "history": history
            }
        
        with open(report_file, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        
        print(f"\n✅ 分析报告已保存至: {report_file}")


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("用法:")
        print("  python3 test_tracker.py --collect <round_num>  # 收集某轮测试数据")
        print("  python3 test_tracker.py --analyze              # 分析多轮测试数据")
        sys.exit(1)
    
    if sys.argv[1] == "--collect" and len(sys.argv) >= 3:
        round_num = int(sys.argv[2])
        tracker = TestTracker(round_num)
        tracker.collect_from_database()
    
    elif sys.argv[1] == "--analyze":
        analyzer = TestAnalyzer()
        for i in range(1, 6):
            analyzer.load_round(i)
        analyzer.analyze_patterns()
