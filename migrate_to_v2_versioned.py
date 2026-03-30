"""数据迁移脚本 v2 - 版本化分析（不覆盖原分数）

将v2引擎评分结果保存到独立的analysis_runs表，保留历史分析记录。
支持新旧版本对比、效果评估、回滚追溯。

用法: python migrate_to_v2_versioned.py [--limit 10] [--session_id xxx]

作者: 小虾米
更新: 2026-03-17（修复：版本化写入，不覆盖原分数）
"""

import sys
import os
import json
import argparse
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))

from db_utils import get_connection, load_sessions
from smart_scoring_v2 import SmartScoringEngine

def init_analysis_runs_table():
    """初始化分析运行记录表（版本化存储）"""
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS analysis_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            
            -- 分析版本信息
            run_version INTEGER DEFAULT 1,
            model_version TEXT DEFAULT 'v2.0',
            kb_version TEXT DEFAULT 'v1.0',
            prompt_version TEXT DEFAULT 'v2.0',
            
            -- 评分结果
            professionalism_score INTEGER,
            standardization_score INTEGER,
            policy_execution_score INTEGER,
            conversion_score INTEGER,
            total_score INTEGER,
            risk_level TEXT,
            
            -- 检索信息
            retrieved_rule_ids TEXT,  -- JSON数组
            pre_analysis TEXT,  -- JSON对象
            
            -- 完整分析结果
            analysis_json TEXT,
            
            -- 元数据
            run_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            run_by TEXT DEFAULT 'system',
            latency_ms INTEGER,
            
            -- 是否当前活跃版本
            is_active BOOLEAN DEFAULT 0,
            
            -- 备注
            notes TEXT
        )
    """)
    
    # 创建索引
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_analysis_session ON analysis_runs(session_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_analysis_active ON analysis_runs(is_active)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_analysis_version ON analysis_runs(session_id, run_version)")
    
    conn.commit()
    conn.close()
    print("✅ analysis_runs表初始化完成")

def get_next_version(session_id: str) -> int:
    """获取下一个版本号
    
    Args:
        session_id: 会话ID
        
    Returns:
        下一个版本号
    """
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute(
        "SELECT MAX(run_version) FROM analysis_runs WHERE session_id = ?",
        (session_id,)
    )
    result = cursor.fetchone()
    conn.close()
    
    return (result[0] or 0) + 1

def save_analysis_run(session_id: str, result: dict, 
                      model_version: str = 'v2.0',
                      kb_version: str = 'v1.0',
                      prompt_version: str = 'v2.0',
                      notes: str = '') -> bool:
    """保存分析运行记录
    
    Args:
        session_id: 会话ID
        result: v2评分结果
        model_version: 模型版本
        kb_version: 知识库版本
        prompt_version: Prompt版本
        notes: 备注
        
    Returns:
        是否成功
    """
    init_analysis_runs_table()
    conn = get_connection()
    cursor = conn.cursor()
    
    # 获取版本号
    version = get_next_version(session_id)
    
    # 提取分数
    dim_scores = result.get('dimension_scores', {})
    prof = dim_scores.get('professionalism', {}).get('score', 3)
    stan = dim_scores.get('standardization', {}).get('score', 3)
    pol = dim_scores.get('policy_execution', {}).get('score', 3)
    conv = dim_scores.get('conversion', {}).get('score', 3)
    total = result.get('summary', {}).get('total_score', prof + stan + pol + conv)
    risk = result.get('summary', {}).get('risk_level', '中风险')
    
    # 提取检索信息
    metadata = result.get('_metadata', {})
    retrieved_rules = metadata.get('retrieved_rules', [])
    pre_analysis = metadata.get('pre_analysis', {})
    latency = metadata.get('latency_ms', 0)
    
    # 先将该会话的所有版本设为is_active=0
    cursor.execute(
        "UPDATE analysis_runs SET is_active = 0 WHERE session_id = ?",
        (session_id,)
    )
    
    # 插入新记录
    cursor.execute("""
        INSERT INTO analysis_runs (
            session_id, run_version, model_version, kb_version, prompt_version,
            professionalism_score, standardization_score, policy_execution_score, conversion_score,
            total_score, risk_level, retrieved_rule_ids, pre_analysis, analysis_json,
            run_at, run_by, latency_ms, is_active, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), 'system', ?, 1, ?)
    """, (
        session_id, version, model_version, kb_version, prompt_version,
        prof, stan, pol, conv, total, risk,
        json.dumps(retrieved_rules, ensure_ascii=False),
        json.dumps(pre_analysis, ensure_ascii=False),
        json.dumps(result, ensure_ascii=False),
        latency,
        notes
    ))
    
    conn.commit()
    conn.close()
    
    return True

def migrate_session_versioned(session_id: str, api_key: str = None, 
                              force: bool = False) -> bool:
    """单条会话迁移（版本化）
    
    Args:
        session_id: 会话ID
        api_key: Moonshot API Key
        force: 强制重新评分
        
    Returns:
        是否成功
    """
    conn = get_connection()
    cursor = conn.cursor()
    
    # 获取会话数据
    cursor.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,))
    row = cursor.fetchone()
    
    if not row:
        print(f"⚠️ 会话不存在: {session_id}")
        conn.close()
        return False
    
    columns = [desc[0] for desc in cursor.description]
    session = dict(zip(columns, row))
    conn.close()
    
    # 检查是否已有v2数据（通过analysis_runs表查询）
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT COUNT(*) FROM analysis_runs WHERE session_id = ? AND model_version = 'v2.0'",
        (session_id,)
    )
    existing_count = cursor.fetchone()[0]
    conn.close()
    
    if not force and existing_count > 0:
        print(f"⏭️ 跳过（已有v2分析记录）: {session_id}")
        return True
    
    print(f"📝 处理会话: {session_id}")
    
    # 构建会话数据
    try:
        messages = json.loads(session.get('messages', '[]'))
    except:
        messages = []
    
    session_data = {
        "session_id": session_id,
        "messages": messages,
        "staff_name": session.get('staff_name', '')
    }
    
    # 使用v2引擎评分
    engine = SmartScoringEngine(api_key=api_key, use_local_intent=True)
    result = engine.score_session(session_data)
    
    if not result:
        print(f"❌ 评分失败: {session_id}")
        return False
    
    # 保存到analysis_runs表（版本化）
    if save_analysis_run(session_id, result, notes='Phase 2.5 v2 migration'):
        print(f"✅ 迁移成功: {session_id} (版本: v2.0)")
        return True
    else:
        print(f"❌ 保存失败: {session_id}")
        return False

def get_analysis_versions(session_id: str) -> list:
    """获取会话的所有分析版本
    
    Args:
        session_id: 会话ID
        
    Returns:
        版本列表
    """
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT run_version, model_version, total_score, risk_level, run_at, is_active
        FROM analysis_runs
        WHERE session_id = ?
        ORDER BY run_version DESC
    """, (session_id,))
    
    rows = cursor.fetchall()
    conn.close()
    
    versions = []
    for row in rows:
        versions.append({
            'version': row[0],
            'model': row[1],
            'total_score': row[2],
            'risk_level': row[3],
            'run_at': row[4],
            'is_active': row[5]
        })
    
    return versions

def compare_versions(session_id: str):
    """对比会话的不同版本分析结果
    
    Args:
        session_id: 会话ID
    """
    versions = get_analysis_versions(session_id)
    
    if len(versions) < 2:
        print(f"⚠️ {session_id} 只有一个版本，无法对比")
        return
    
    print(f"\n📊 会话 {session_id} 版本对比")
    print("=" * 70)
    
    print(f"{'版本':<8} {'模型':<10} {'总分':<8} {'风险':<10} {'时间':<20} {'当前'}")
    print("-" * 70)
    
    for v in versions:
        active_mark = "✓" if v['is_active'] else ""
        print(f"{v['version']:<8} {v['model']:<10} {v['total_score']:<8} "
              f"{v['risk_level']:<10} {v['run_at']:<20} {active_mark}")

def migrate_all_versioned(limit: int = None, api_key: str = None, force: bool = False):
    """批量迁移（版本化）
    
    Args:
        limit: 限制数量
        api_key: Moonshot API Key
        force: 强制重新评分
    """
    init_analysis_runs_table()
    
    df = load_sessions()
    if limit:
        df = df.head(limit)
    
    print(f"📊 共 {len(df)} 条会话需要迁移（版本化存储）")
    print("=" * 60)
    
    success_count = 0
    failed_count = 0
    skipped_count = 0
    
    for idx, row in df.iterrows():
        session_id = row['session_id']
        
        # 检查是否已有v2记录
        if not force:
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) FROM analysis_runs WHERE session_id = ? AND model_version = 'v2.0'",
                (session_id,)
            )
            if cursor.fetchone()[0] > 0:
                print(f"⏭️ [{idx+1}/{len(df)}] 跳过（已有v2记录）: {session_id}")
                skipped_count += 1
                conn.close()
                continue
            conn.close()
        
        print(f"\n[{idx+1}/{len(df)}] ", end="")
        
        if migrate_session_versioned(session_id, api_key, force):
            success_count += 1
        else:
            failed_count += 1
    
    print("\n" + "=" * 60)
    print(f"📈 迁移完成: 成功 {success_count}, 失败 {failed_count}, 跳过 {skipped_count}")

def main():
    parser = argparse.ArgumentParser(description='数据迁移到v2评分引擎（版本化）')
    subparsers = parser.add_subparsers(dest='command', help='子命令')
    
    # migrate 子命令
    migrate_parser = subparsers.add_parser('migrate', help='执行迁移')
    migrate_parser.add_argument('--session_id', type=str, help='指定单个会话')
    migrate_parser.add_argument('--limit', type=int, help='限制数量')
    migrate_parser.add_argument('--force', action='store_true', help='强制重新评分')
    migrate_parser.add_argument('--api_key', type=str, help='Moonshot API Key')
    
    # versions 子命令
    versions_parser = subparsers.add_parser('versions', help='查看版本')
    versions_parser.add_argument('session_id', type=str, help='会话ID')
    
    # compare 子命令
    compare_parser = subparsers.add_parser('compare', help='对比版本')
    compare_parser.add_argument('session_id', type=str, help='会话ID')
    
    args = parser.parse_args()
    
    if args.command == 'migrate':
        api_key = args.api_key or os.getenv("MOONSHOT_API_KEY")
        
        if args.session_id:
            success = migrate_session_versioned(args.session_id, api_key, args.force)
            sys.exit(0 if success else 1)
        else:
            migrate_all_versioned(args.limit, api_key, args.force)
    
    elif args.command == 'versions':
        versions = get_analysis_versions(args.session_id)
        print(f"\n会话 {args.session_id} 的分析版本:")
        for v in versions:
            active = " (当前)" if v['is_active'] else ""
            print(f"  v{v['version']} [{v['model']}]: {v['total_score']}/20 - {v['risk_level']} @ {v['run_at']}{active}")
    
    elif args.command == 'compare':
        compare_versions(args.session_id)
    
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
