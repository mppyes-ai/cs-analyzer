#!/usr/bin/env python3
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

from task_queue import get_pending_task

task = get_pending_task()
if task:
    print(f"Task ID: {task['task_id']}")
    print(f"Session ID: {task['session_id']}")
    print(f"session_data type: {type(task['session_data'])}")
    
    session_data = task['session_data']
    print(f"messages type: {type(session_data.get('messages'))}")
    
    messages = session_data.get('messages', [])
    print(f"messages length: {len(messages)}")
    
    if messages:
        print(f"First message type: {type(messages[0])}")
        print(f"First message: {messages[0]}")
else:
    print("No pending task")