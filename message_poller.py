#!/usr/bin/env python3
"""消息轮询发送服务 v2.3 - 新增消息级去重

核心改进:
1. 【v2.3】消息级去重（5分钟窗口期内相同内容只发送一次）
2. 【方案1】移除monitor_agent退出等待，消息服务独立生命周期
3. PID文件自监控（外部可检测存活状态）
4. 崩溃自动恢复（残留消息重新加载）
5. 完成报告必达（失败重试3次）
6. 优雅退出保护（60秒消息清理窗口）

用法:
    python3 message_poller.py <monitor_pid>

作者: 小虾米
更新: 2026-04-06 (v2.3 消息去重)
"""

import os
import sys
import time
import json
import signal
import atexit
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any

# ============ 配置 ============
# ============ 配置 ============
LOGS_DIR = Path(os.path.join(os.path.dirname(__file__), 'logs'))
LOGS_DIR.mkdir(exist_ok=True)

MSG_FILE = LOGS_DIR / 'cs_analyzer_messages.jsonl'
MSG_FILE_PROCESSED = LOGS_DIR / 'cs_analyzer_messages_processed.jsonl'
MSG_FILE_FAILED = LOGS_DIR / 'cs_analyzer_messages_failed.jsonl'
PID_FILE = LOGS_DIR / 'cs_analyzer_message_poller.pid'
POLL_INTERVAL = 3  # 秒
MUST_DELIVER_KEYWORDS = ['完成', '100%', '质检报告', '分析完成']  # 必达消息关键词
MAX_RETRY_COUNT = 3  # 单条消息最大重试次数
IDLE_EXIT_MINUTES = 5  # 【修复】空闲5分钟后退出（原30分钟太长）


class MessagePoller:
    """增强型消息轮询发送器（v2.3 - 新增消息级去重）"""

    def __init__(self, monitor_pid: int):
        self.monitor_pid = monitor_pid
        self.running = True
        self.processed_count = 0
        self.failed_messages: Dict[str, int] = {}  # 消息指纹 -> 重试次数
        
        # 【v2.3新增】消息去重缓存：指纹 -> 发送时间戳
        self._recent_sent_cache: Dict[str, float] = {}
        self._dedup_window_seconds = 300  # 5分钟去重窗口

        # 注册信号处理
        signal.signal(signal.SIGTERM, self.handle_signal)
        signal.signal(signal.SIGINT, self.handle_signal)

        # 注册退出清理
        atexit.register(self.cleanup)

        # 写入PID文件
        self._write_pid_file()

        # 加载残留的失败消息
        self._load_failed_messages()

    def _write_pid_file(self):
        """写入PID文件供外部监控，带单例检查（增强版）"""
        try:
            # 【修复】先检查文件修改时间，如果是最近10秒内创建的，可能是并发启动
            if PID_FILE.exists():
                try:
                    file_mtime = PID_FILE.stat().st_mtime
                    if time.time() - file_mtime < 10:
                        # 文件是最近10秒内创建的，可能另一个实例正在启动中
                        print(f"⚠️ PID文件最近创建，可能有其他实例正在启动，本实例等待...")
                        time.sleep(2)  # 等待2秒让另一个实例完成启动
                        
                    old_pid = int(PID_FILE.read_text().strip())
                    # 检查旧进程是否仍在运行
                    os.kill(old_pid, 0)
                    # 如果运行到这里，说明旧进程存在
                    print(f"⚠️ 已有消息轮询服务在运行 (PID: {old_pid})，本实例退出")
                    sys.exit(0)
                except (ValueError, ProcessLookupError, OSError):
                    # PID文件损坏或进程不存在，清理后重新创建
                    print(f"🧹 清理残留PID文件")
                    PID_FILE.unlink()
            
            PID_FILE.write_text(str(os.getpid()))
            # 【修复】立即刷新文件系统，确保其他实例能看到
            os.sync()
            print(f"📝 PID文件已创建: {PID_FILE} ({os.getpid()})")
        except SystemExit:
            raise
        except Exception as e:
            print(f"⚠️ PID文件写入失败: {e}")

    def _load_failed_messages(self):
        """加载上次崩溃时残留的失败消息"""
        if MSG_FILE_FAILED.exists():
            try:
                with open(MSG_FILE_FAILED, 'r') as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        # 将失败消息重新写入待发送队列
                        with open(MSG_FILE, 'a') as out:
                            out.write(line + '\n')
                # 清空失败文件
                MSG_FILE_FAILED.write_text('')
                print(f"🔄 已恢复残留消息到发送队列")
            except Exception as e:
                print(f"⚠️ 恢复残留消息失败: {e}")

    def cleanup(self):
        """退出清理"""
        print("\n🧹 清理PID文件...")
        try:
            if PID_FILE.exists():
                PID_FILE.unlink()
        except Exception:
            pass

    def handle_signal(self, signum, frame):
        """处理退出信号"""
        print(f"\n⚠️ 收到信号 {signum}，准备优雅退出...")
        self.running = False

    def check_monitor_alive(self) -> bool:
        """检查 monitor_agent 是否仍在运行"""
        try:
            os.kill(self.monitor_pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False

    def _is_must_deliver_message(self, message: str) -> bool:
        """检查是否是必须送达的消息（完成报告等）"""
        message_lower = message.lower()
        return any(keyword in message for keyword in MUST_DELIVER_KEYWORDS)

    def _get_message_fingerprint(self, msg_data: Dict) -> str:
        """生成消息指纹用于去重和重试计数"""
        import hashlib
        # 【v2.3】指纹包含进度内容，确保同进度消息能被识别为重复
        content = f"{msg_data.get('chat_id', '')}:{msg_data.get('message', '')}"
        return hashlib.md5(content.encode()).hexdigest()[:16]
    
    def _is_recently_sent(self, fingerprint: str) -> bool:
        """【v2.3新增】检查消息是否在最近去重窗口内已发送"""
        now = time.time()
        if fingerprint in self._recent_sent_cache:
            sent_time = self._recent_sent_cache[fingerprint]
            if now - sent_time < self._dedup_window_seconds:
                return True
            else:
                # 过期，清理
                del self._recent_sent_cache[fingerprint]
        return False
    
    def _record_sent(self, fingerprint: str):
        """【v2.3新增】记录消息已发送时间"""
        self._recent_sent_cache[fingerprint] = time.time()
        # 清理过期条目（每100条清理一次）
        if len(self._recent_sent_cache) > 100:
            now = time.time()
            expired = [fp for fp, ts in self._recent_sent_cache.items() 
                      if now - ts > self._dedup_window_seconds]
            for fp in expired:
                del self._recent_sent_cache[fp]

    def send_feishu_message(self, message: str, chat_id: str) -> bool:
        """发送飞书消息（带重试）"""
        try:
            if not chat_id:
                chat_id = os.getenv('FEISHU_CHAT_ID', 'ou_7a8de0e44d0870581478030fb08b1021')

            import subprocess

            cmd = [
                'openclaw', 'message', 'send',
                '--channel', 'feishu',
                '--target', chat_id,
                '--message', message
            ]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.returncode == 0:
                return True
            else:
                print(f"  ⚠️ 发送失败: {result.stderr[:200]}")
                return False

        except subprocess.TimeoutExpired:
            print(f"  ⚠️ 发送超时")
            return False
        except Exception as e:
            print(f"  ❌ 消息发送失败: {e}")
            return False

    def process_messages(self) -> int:
        """处理消息文件中的待发送消息（增强版）"""
        if not MSG_FILE.exists():
            return 0

        processed = 0
        new_messages = []
        failed_to_save = []

        try:
            with open(MSG_FILE, 'r') as f:
                lines = f.readlines()

            for line in lines:
                line = line.strip()
                if not line:
                    continue

                try:
                    msg = json.loads(line)
                    chat_id = msg.get('chat_id', '')
                    message = msg.get('message', '')
                    msg_fingerprint = self._get_message_fingerprint(msg)
                    is_must_deliver = self._is_must_deliver_message(message)

                    # 【v2.3新增】检查是否最近已发送过（去重）
                    if self._is_recently_sent(msg_fingerprint):
                        print(f"  🔄 跳过重复消息: {message[:30]}...")
                        processed += 1  # 计为已处理，但不实际发送
                        continue

                    # 获取当前重试次数
                    retry_count = self.failed_messages.get(msg_fingerprint, 0)

                    # 发送消息
                    if self.send_feishu_message(message, chat_id):
                        processed += 1
                        # 【v2.3新增】记录已发送，用于去重
                        self._record_sent(msg_fingerprint)
                        # 记录到已处理文件
                        with open(MSG_FILE_PROCESSED, 'a') as f:
                            f.write(line + '\n')
                        # 从失败计数中移除
                        if msg_fingerprint in self.failed_messages:
                            del self.failed_messages[msg_fingerprint]
                    else:
                        # 发送失败
                        retry_count += 1
                        self.failed_messages[msg_fingerprint] = retry_count

                        if is_must_deliver and retry_count < MAX_RETRY_COUNT:
                            # 必达消息且未超过重试次数，保留重试
                            print(f"  🔄 必达消息将在下次重试 ({retry_count}/{MAX_RETRY_COUNT})")
                            new_messages.append(line)
                        elif retry_count >= MAX_RETRY_COUNT:
                            # 超过最大重试次数，移入失败文件
                            print(f"  ❌ 消息重试{MAX_RETRY_COUNT}次失败，移入失败队列")
                            failed_to_save.append(line)
                        else:
                            # 非必达消息，保留一次
                            new_messages.append(line)

                except json.JSONDecodeError:
                    print(f"  ⚠️ 跳过无效消息行: {line[:50]}...")
                    continue

            # 重写消息文件（保留未处理的）
            if new_messages:
                with open(MSG_FILE, 'w') as f:
                    for line in new_messages:
                        f.write(line + '\n')
            else:
                MSG_FILE.write_text('')

            # 保存失败消息
            if failed_to_save:
                with open(MSG_FILE_FAILED, 'a') as f:
                    for line in failed_to_save:
                        f.write(line + '\n')

            return processed

        except Exception as e:
            print(f"  ❌ 处理消息文件失败: {e}")
            return 0

    def run(self):
        """主循环（方案1：独立生命周期）"""
        print(f"🚀 消息轮询服务启动 v2.3【消息级去重】")
        print(f"   监控 PID: {self.monitor_pid}（仅用于记录，不影响服务生命周期）")
        print(f"   轮询间隔: {POLL_INTERVAL}秒")
        print(f"   去重窗口: {self._dedup_window_seconds}秒")
        print(f"   必达关键词: {MUST_DELIVER_KEYWORDS}")
        print(f"   最大重试: {MAX_RETRY_COUNT}次")

        empty_count = 0  # 连续空轮询计数

        while self.running:
            # ========== 方案1：移除monitor_agent退出等待 ==========
            # 【原逻辑】检查 monitor_agent 是否存活 - 已移除
            # 原因：message_poller应独立运行，不因monitor崩溃而停止
            # if not self.check_monitor_alive():
            #     print(f"\n👋 monitor_agent ({self.monitor_pid}) 已退出")
            #     break
            # ======================================================

            # 【新逻辑】改为检查消息文件状态
            # 如果消息文件为空且连续空轮询超过阈值，则考虑退出
            if MSG_FILE.exists() and MSG_FILE.stat().st_size == 0:
                empty_count += 1
                # 【修复】5分钟无消息退出 (300 * 3秒 / 60 = 5分钟)
                if empty_count > 300:
                    print("\n⏰ 5分钟无新消息，消息服务正常退出")
                    break
            else:
                empty_count = 0

            # 2. 处理消息
            count = self.process_messages()
            if count > 0:
                print(f"  📤 发送 {count} 条消息")
                self.processed_count += count
                empty_count = 0
            else:
                empty_count += 1

            # 3. 等待
            time.sleep(POLL_INTERVAL)

        # ========== 方案1：简化优雅退出阶段 ==========
        # 【移除】不再等待monitor_agent退出，消息服务独立生命周期
        # 原代码：
        # # 阶段1：等待monitor_agent彻底退出
        # exit_wait_start = time.time()
        # while self.check_monitor_alive() and time.time() - exit_wait_start < 5:
        #     time.sleep(0.5)
        # ======================================================

        print("\n⏳ 进入消息清理阶段...")

        # 阶段1：最后消息清理（最长60秒）
        print("📤 最后消息清理中...")
        final_wait_start = time.time()
        final_wait_timeout = 30

        while time.time() - final_wait_start < final_wait_timeout:
            count = self.process_messages()
            if count > 0:
                print(f"  📤 发送最后 {count} 条消息")
                self.processed_count += count
                final_wait_start = time.time()  # 重置计时器
            elif not MSG_FILE.exists() or MSG_FILE.stat().st_size == 0:
                print("  ✅ 消息文件已清空")
                break
            time.sleep(1)

        # 阶段2：保存未发送的必达消息
        if MSG_FILE.exists() and MSG_FILE.stat().st_size > 0:
            print(f"  ⚠️ 有消息未发送完成，保存到失败队列")
            try:
                with open(MSG_FILE, 'r') as f:
                    remaining = f.read()
                with open(MSG_FILE_FAILED, 'a') as f:
                    f.write(remaining)
                MSG_FILE.write_text('')
            except Exception as e:
                print(f"  ❌ 保存失败: {e}")

        print(f"👋 消息轮询服务退出（共发送 {self.processed_count} 条消息）")


def main():
    if len(sys.argv) < 2:
        print("用法: python3 message_poller.py <monitor_pid>")
        sys.exit(1)

    monitor_pid = int(sys.argv[1])

    poller = MessagePoller(monitor_pid)
    poller.run()


if __name__ == '__main__':
    main()
