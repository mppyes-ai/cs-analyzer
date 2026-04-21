"""数据库写入队列模块 - 异步处理所有数据库写入操作

包含功能：
- _db_writer_loop: 后台线程处理写入队列
- start_db_writer: 启动写入线程
- stop_db_writer: 停止写入线程
- queue_save_result: 将结果加入写入队列
- wait_for_db_writes: 等待所有写入完成

Usage:
    from db_writer import start_db_writer, stop_db_writer, queue_save_result
    start_db_writer()
    queue_save_result(task, result)
    stop_db_writer()
"""

import queue
import threading
import time
from typing import Dict, Optional

from task_queue import fail_task
from db_operations import _save_result_sync

# ========== 全局写入队列和线程 ==========
db_write_queue = queue.Queue()
db_writer_thread = None
db_writer_running = False
MAX_WRITE_RETRIES = 3  # 【v2.6.5-fix】最大重试次数，防止无限循环


def _db_writer_loop():
    """【v2.6.5】独立线程处理所有数据库写入
    
    从队列中获取 (task, result, retry_count) 元组并同步写入数据库
    这是一个守护线程，会持续运行直到收到 None 作为退出信号
    """
    global db_writer_running
    db_writer_running = True
    print("🔄 数据库写入线程已启动")
    
    while db_writer_running:
        try:
            item = db_write_queue.get(timeout=1.0)  # 1秒超时检查运行状态
            if item is None:  # 退出信号
                print("🛑 数据库写入线程收到退出信号")
                db_writer_running = False
                break
            
            task, result, retry_count = item  # 【v2.6.5-fix】解包重试计数器
            task_id = task.get('task_id', 'unknown')
            try:
                _save_result_sync(task, result)
                print(f"✅ 数据库写入成功 (任务 {task_id})")
            except Exception as e:
                if retry_count < MAX_WRITE_RETRIES:
                    print(f"⚠️ 写入重试 {retry_count + 1}/{MAX_WRITE_RETRIES} (任务 {task_id}): {e}")
                    time.sleep(0.5 * (retry_count + 1))  # 递增退避
                    db_write_queue.put((task, result, retry_count + 1))
                else:
                    print(f"❌ 写入彻底失败 (任务 {task_id}): {e}")
                    try:
                        fail_task(task_id, f"DB write failed after {MAX_WRITE_RETRIES} retries: {e}")
                    except:
                        pass  # fail_task 本身也可能失败
            finally:
                db_write_queue.task_done()
                
        except queue.Empty:
            continue  # 超时继续检查运行状态
        except Exception as e:
            print(f"⚠️ 数据库写入线程异常: {e}")
    
    print("✅ 数据库写入线程已退出")


def start_db_writer():
    """【v2.6.5】启动数据库写入线程"""
    global db_writer_thread, db_writer_running
    if db_writer_thread is None or not db_writer_thread.is_alive():
        db_writer_running = True
        db_writer_thread = threading.Thread(target=_db_writer_loop, daemon=True)
        db_writer_thread.start()
        print("✅ 数据库写入线程启动成功")


def stop_db_writer():
    """【v2.6.5】停止数据库写入线程"""
    global db_writer_running
    db_writer_running = False
    if db_writer_thread is not None:
        db_write_queue.put(None)  # 发送退出信号
        db_writer_thread.join(timeout=5.0)


def queue_save_result(task: Dict, result: Dict):
    """【v2.6.5】将结果加入异步写入队列
    
    非阻塞操作，立即返回，由后台线程处理数据库写入
    """
    db_write_queue.put((task, result, 0))  # 【v2.6.5-fix】初始 retry_count = 0
    return True


def wait_for_db_writes(timeout: Optional[float] = None) -> bool:
    """【v2.6.5】等待所有待写入的队列项完成
    
    Args:
        timeout: 最大等待时间（秒），None表示无限等待
        
    Returns:
        是否在超时前完成
    """
    # 【v2.6.5-fix】使用轮询模式支持timeout
    if timeout is None:
        db_write_queue.join()
        return True
    
    deadline = time.time() + timeout
    while not db_write_queue.empty() and time.time() < deadline:
        time.sleep(0.1)
    return db_write_queue.empty()


async def wait_for_db_writes_async(timeout: Optional[float] = None) -> bool:
    """【P1修复】异步等待所有待写入的队列项完成
    
    包装同步的 wait_for_db_writes 为异步函数，避免 await bool 错误
    
    Args:
        timeout: 最大等待时间（秒），None表示无限等待
        
    Returns:
        是否在超时前完成
    """
    import asyncio
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, wait_for_db_writes, timeout)
