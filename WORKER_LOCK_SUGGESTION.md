# Worker 单例锁机制建议

## 方案：启动时创建锁文件

worker 启动时检查 `/tmp/worker.lock`：
- 如果不存在 → 创建锁文件，写入 PID，正常运行
- 如果存在 → 读取 PID，检查进程是否还在运行
  - 如果进程还在 → 拒绝启动，提示"worker 已在运行 (PID: xxx)"
  - 如果进程已死 → 删除旧锁文件，创建新锁，正常运行

## 代码实现

```python
import os
import sys

LOCK_FILE = '/tmp/cs_analyzer_worker.lock'

def acquire_lock():
    """获取单例锁，防止多个 worker 同时运行"""
    if os.path.exists(LOCK_FILE):
        # 读取旧 PID
        with open(LOCK_FILE, 'r') as f:
            old_pid = f.read().strip()
        
        # 检查进程是否还在运行
        if old_pid and os.path.exists(f'/proc/{old_pid}'):
            print(f"❌ Worker 已在运行 (PID: {old_pid})")
            print(f"   如需重启，请先执行: pkill -f 'python3 worker.py'")
            sys.exit(1)
        else:
            # 进程已死，删除旧锁
            os.remove(LOCK_FILE)
    
    # 创建新锁
    with open(LOCK_FILE, 'w') as f:
        f.write(str(os.getpid()))
    
    print(f"✅ 获取锁成功 (PID: {os.getpid()})")

def release_lock():
    """释放锁"""
    if os.path.exists(LOCK_FILE):
        os.remove(LOCK_FILE)
        print("✅ 锁已释放")

# 在 run_worker / run_parallel_worker / run_grouped_parallel_worker 开头调用
acquire_lock()

# 在 finally 块中释放锁
try:
    # ... 主循环 ...
finally:
    release_lock()
```

## 这样解决什么问题？

1. ✅ **自动防止重复启动** - 不用人工检查 ps aux
2. ✅ **自动清理僵尸锁** - 进程崩溃后锁文件不会一直存在
3. ✅ **清晰的错误提示** - 告诉用户 worker 已在运行
4. ✅ **安全退出时释放** - 正常停止时自动删锁

## 是否需要我加这个锁机制？
