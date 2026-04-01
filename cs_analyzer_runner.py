#!/usr/bin/env python3
"""
CS-Analyzer 标准执行脚本
确保通过Worker模式进行分析，避免直接exec导致的超时问题

作者: 小虾米
版本: 1.0
"""

import os
import sys
import json
import time
import subprocess
from typing import List, Dict, Optional

# 添加技能目录到路径
sys.path.insert(0, '/Users/jinlu/.openclaw/workspace/skills/cs-analyzer')

from log_parser import parse_log_file


class CSAnalyzerRunner:
    """CS-Analyzer标准执行器"""
    
    def __init__(self, worker_mode: str = "grouped"):
        """
        Args:
            worker_mode: Worker运行模式 (grouped|parallel|serial)
        """
        self.worker_mode = worker_mode
        self.cs_analyzer_dir = '/Users/jinlu/.openclaw/workspace/skills/cs-analyzer'
        self._worker_process = None
        
    def _is_worker_running(self) -> bool:
        """检查Worker是否已在运行"""
        try:
            result = subprocess.run(
                ['pgrep', '-f', 'python3 worker.py'],
                capture_output=True,
                text=True
            )
            return result.returncode == 0 and result.stdout.strip()
        except:
            return False
    
    def start_worker(self, once: bool = True) -> bool:
        """
        启动Worker进程
        
        Args:
            once: 是否处理一轮后退出
            
        Returns:
            是否成功启动
        """
        if self._is_worker_running():
            print("✅ Worker已在运行")
            return True
        
        print(f"🚀 启动Worker (模式: {self.worker_mode})...")
        
        cmd = ['python3', 'worker.py', f'--{self.worker_mode}']
        if once:
            cmd.append('--once')
        
        try:
            # 后台启动Worker
            self._worker_process = subprocess.Popen(
                cmd,
                cwd=self.cs_analyzer_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True  # 独立进程组
            )
            
            # 等待Worker初始化
            time.sleep(3)
            
            if self._is_worker_running():
                print("✅ Worker启动成功")
                return True
            else:
                print("❌ Worker启动失败")
                return False
                
        except Exception as e:
            print(f"❌ 启动Worker出错: {e}")
            return False
    
    def stop_worker(self):
        """停止Worker进程"""
        if self._worker_process:
            self._worker_process.terminate()
            try:
                self._worker_process.wait(timeout=5)
            except:
                self._worker_process.kill()
            print("🛑 Worker已停止")
    
    def submit_sessions(self, log_file: str) -> List[str]:
        """
        提交会话到Worker队列
        
        Args:
            log_file: 日志文件路径
            
        Returns:
            任务ID列表
        """
        print(f"📁 解析日志文件: {log_file}")
        sessions = parse_log_file(log_file)
        print(f"✅ 解析完成: {len(sessions)}通会话")
        
        # 导入task_queue模块
        sys.path.insert(0, self.cs_analyzer_dir)
        from task_queue import submit_task
        
        task_ids = []
        print(f"\n📤 提交任务到Worker队列...")
        
        for i, session in enumerate(sessions, 1):
            try:
                task_id = submit_task(
                    session_id=session['session_id'],
                    session_data=session
                )
                task_ids.append(task_id)
                print(f"   会话{i}: {session['session_id']} → 任务{task_id}")
            except Exception as e:
                print(f"   会话{i}: 提交失败 - {e}")
        
        print(f"\n✅ 成功提交 {len(task_ids)}/{len(sessions)} 个任务")
        return task_ids
    
    def wait_for_results(self, task_ids: List[int], timeout: int = 600) -> Dict:
        """
        等待任务完成并获取结果
        
        Args:
            task_ids: 任务ID列表
            timeout: 超时时间（秒）
            
        Returns:
            结果字典
        """
        sys.path.insert(0, self.cs_analyzer_dir)
        from task_queue import get_task_detail
        
        print(f"\n⏳ 等待任务完成（超时: {timeout}s）...")
        start_time = time.time()
        completed = set()
        results = {}
        
        while len(completed) < len(task_ids):
            if time.time() - start_time > timeout:
                print(f"⚠️ 等待超时，已完成 {len(completed)}/{len(task_ids)}")
                break
            
            for task_id in task_ids:
                if task_id in completed:
                    continue
                
                try:
                    detail = get_task_detail(task_id)
                    if detail and detail['status'] in ['completed', 'failed']:
                        completed.add(task_id)
                        results[task_id] = detail['status']
                        print(f"   任务{task_id}: {detail['status']}")
                except Exception as e:
                    print(f"   任务{task_id}: 查询失败 - {e}")
            
            if len(completed) < len(task_ids):
                time.sleep(5)
                print(f"   进度: {len(completed)}/{len(task_ids)} 完成...")
        
        print(f"\n✅ 全部完成: {len(completed)}/{len(task_ids)}")
        return results
    
    def analyze(self, log_file: str, wait: bool = True, timeout: int = 600) -> Dict:
        """
        标准分析入口
        
        Args:
            log_file: 日志文件路径
            wait: 是否等待结果
            timeout: 等待超时时间
            
        Returns:
            分析结果
        """
        print("=" * 60)
        print("🚀 CS-Analyzer 标准执行模式")
        print("=" * 60)
        
        # 1. 启动Worker
        if not self.start_worker(once=wait):
            return {"error": "Worker启动失败"}
        
        try:
            # 2. 提交任务
            task_ids = self.submit_sessions(log_file)
            
            if not task_ids:
                return {"error": "没有任务被提交"}
            
            # 3. 等待结果（可选）
            if wait:
                results = self.wait_for_results(task_ids, timeout)
                return {
                    "status": "success",
                    "total_tasks": len(task_ids),
                    "completed": len([r for r in results.values() if r == 'completed']),
                    "failed": len([r for r in results.values() if r == 'failed']),
                    "task_ids": task_ids,
                    "results": results
                }
            else:
                return {
                    "status": "submitted",
                    "task_ids": task_ids,
                    "message": "任务已提交，请稍后查询结果"
                }
                
        finally:
            if wait:
                self.stop_worker()


def main():
    """命令行入口"""
    import argparse
    
    parser = argparse.ArgumentParser(description='CS-Analyzer标准执行脚本')
    parser.add_argument('log_file', help='客服日志文件路径')
    parser.add_argument('--mode', default='grouped', 
                       choices=['grouped', 'parallel', 'serial'],
                       help='Worker运行模式')
    parser.add_argument('--no-wait', action='store_true',
                       help='不等待结果，提交后立即返回')
    parser.add_argument('--timeout', type=int, default=600,
                       help='等待超时时间（秒）')
    
    args = parser.parse_args()
    
    runner = CSAnalyzerRunner(worker_mode=args.mode)
    result = runner.analyze(
        log_file=args.log_file,
        wait=not args.no_wait,
        timeout=args.timeout
    )
    
    print("\n" + "=" * 60)
    print("📊 执行结果")
    print("=" * 60)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
