#!/usr/bin/env python3
"""最小范围测试：验证Kimi API响应时间"""

import os
import sys
import time
import asyncio
from pathlib import Path

# 加载环境变量
env_path = Path(__file__).parent / '.env'
if env_path.exists():
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ.setdefault(key, value)

api_key = os.getenv('MOONSHOT_API_KEY')
if not api_key:
    print("❌ 未找到MOONSHOT_API_KEY")
    sys.exit(1)

# 测试1：同步API调用
def test_sync_api():
    """测试同步API调用耗时"""
    from openai import OpenAI
    
    client = OpenAI(api_key=api_key, base_url="https://api.moonshot.cn/v1")
    
    # 构造一个简化的评分prompt（类似实际使用的）
    prompt = """你是一位专业的客服质检专家。请对以下客服会话进行评分：

会话内容：
用户：你好，我想咨询一下林内燃气热水器
客服：您好，欢迎咨询林内热水器，请问有什么可以帮您？
用户：RUS-16E32FBF多少钱？
客服：这款目前活动价是2899元

请从以下四个维度评分（1-5分）：
1. 专业性：参数准确、解释清晰
2. 标准化：礼貌用语、响应及时
3. 政策执行：政策传达准确完整
4. 转化能力：主动挖掘需求、成功引导

请以JSON格式返回评分结果。"""

    print("\n=== 测试1：同步API调用 ===")
    start = time.time()
    try:
        response = client.chat.completions.create(
            model="kimi-k2.5",
            messages=[
                {"role": "system", "content": "你是客服质检专家，输出JSON格式评分结果。"},
                {"role": "user", "content": prompt}
            ],
            timeout=60
        )
        elapsed = time.time() - start
        print(f"✅ 同步API调用成功")
        print(f"   耗时: {elapsed:.1f}秒")
        print(f"   返回token数: {response.usage.completion_tokens if response.usage else 'N/A'}")
        return elapsed
    except Exception as e:
        print(f"❌ 同步API调用失败: {e}")
        return None

# 测试2：异步API调用
async def test_async_api():
    """测试异步API调用耗时"""
    from openai import AsyncOpenAI
    
    client = AsyncOpenAI(api_key=api_key, base_url="https://api.moonshot.cn/v1")
    
    prompt = """你是一位专业的客服质检专家。请对以下客服会话进行评分：

会话内容：
用户：你好，我想咨询一下林内燃气热水器
客服：您好，欢迎咨询林内热水器，请问有什么可以帮您？
用户：RUS-16E32FBF多少钱？
客服：这款目前活动价是2899元

请从以下四个维度评分（1-5分）：
1. 专业性
2. 标准化
3. 政策执行
4. 转化能力

请输出JSON格式评分结果。"""

    print("\n=== 测试2：异步API调用 ===")
    start = time.time()
    try:
        response = await client.chat.completions.create(
            model="kimi-k2.5",
            messages=[
                {"role": "system", "content": "你是客服质检专家，输出JSON格式评分结果。"},
                {"role": "user", "content": prompt}
            ],
            timeout=60
        )
        elapsed = time.time() - start
        print(f"✅ 异步API调用成功")
        print(f"   耗时: {elapsed:.1f}秒")
        print(f"   返回token数: {response.usage.completion_tokens if response.usage else 'N/A'}")
        return elapsed
    except Exception as e:
        print(f"❌ 异步API调用失败: {e}")
        return None

# 测试3：批量API调用（3通并发）
async def test_batch_api():
    """测试批量API调用耗时"""
    from openai import AsyncOpenAI
    import asyncio
    
    client = AsyncOpenAI(api_key=api_key, base_url="https://api.moonshot.cn/v1")
    semaphore = asyncio.Semaphore(5)
    
    async def score_one(idx):
        prompt = f"""请对以下客服会话进行评分：

会话{idx}：
用户：咨询热水器
客服：您好，请问有什么可以帮您？

请从四个维度评分（1-5分），输出JSON格式。"""
        
        async with semaphore:
            start = time.time()
            response = await client.chat.completions.create(
                model="kimi-k2.5",
                messages=[
                    {"role": "system", "content": "你是客服质检专家。"},
                    {"role": "user", "content": prompt}
                ],
                timeout=60
            )
            elapsed = time.time() - start
            return elapsed
    
    print("\n=== 测试3：批量API调用（3通并发）===")
    start = time.time()
    times = await asyncio.gather(*[score_one(i) for i in range(3)])
    total_elapsed = time.time() - start
    
    print(f"✅ 批量API调用完成")
    print(f"   总耗时: {total_elapsed:.1f}秒")
    print(f"   单次耗时: {[f'{t:.1f}s' for t in times]}")
    print(f"   平均耗时: {sum(times)/len(times):.1f}秒")

if __name__ == '__main__':
    print("🧪 CS-Analyzer API耗时最小范围测试")
    print("=" * 50)
    
    # 运行测试
    sync_time = test_sync_api()
    async_time = asyncio.run(test_async_api())
    asyncio.run(test_batch_api())
    
    print("\n" + "=" * 50)
    print("📊 测试结果汇总")
    print("=" * 50)
    if sync_time:
        print(f"同步API: {sync_time:.1f}秒")
    if async_time:
        print(f"异步API: {async_time:.1f}秒")
    print("\n预期耗时: 5-15秒")
    print("实际日志耗时: 63-295秒")
    print("\n结论：")
    if sync_time and sync_time > 30:
        print("❌ API响应确实缓慢（>30秒），可能是网络或Kimi服务端问题")
    elif sync_time:
        print("✅ API响应正常（<30秒），问题可能在代码逻辑")
