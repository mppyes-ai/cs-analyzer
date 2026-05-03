from flask import Flask, request, jsonify, send_from_directory
import sqlite3
import json
import os

app = Flask(__name__)

# 数据库路径
DB_PATH = os.path.join(os.path.dirname(__file__), "knowledge_store.db")

def get_db():
    """获取数据库连接"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

@app.route('/')
def index():
    """首页"""
    return send_from_directory('.', 'index.html')

@app.route('/api/stats')
def get_stats():
    """获取统计信息"""
    conn = get_db()
    cursor = conn.cursor()
    
    # 按状态统计
    cursor.execute('SELECT status, COUNT(*) as count FROM entities GROUP BY status')
    status_counts = {row['status']: row['count'] for row in cursor.fetchall()}
    
    # 按类型统计
    cursor.execute('SELECT entity_type, COUNT(*) as count FROM entities GROUP BY entity_type')
    type_counts = {row['entity_type']: row['count'] for row in cursor.fetchall()}
    
    # 平均置信度
    cursor.execute('SELECT AVG(confidence) as avg_conf FROM entities WHERE status = "pending"')
    avg_conf = cursor.fetchone()['avg_conf'] or 0
    
    # 总实体数
    cursor.execute('SELECT COUNT(*) as total FROM entities')
    total = cursor.fetchone()['total']
    
    # Phase 3: 过期实体统计
    cursor.execute('''
        SELECT COUNT(*) as expired FROM entities 
        WHERE status = 'approved' 
        AND last_seen < datetime('now', '-90 days')
    ''')
    expired_count = cursor.fetchone()['expired'] or 0
    
    conn.close()
    
    return jsonify({
        "pending": status_counts.get('pending', 0),
        "approved": status_counts.get('approved', 0),
        "rejected": status_counts.get('rejected', 0),
        "modified": status_counts.get('modified', 0),
        "expired": expired_count,  # Phase 3
        "total": total,
        "by_type": type_counts,
        "avg_confidence": round(avg_conf, 3),
        "cdf": type_counts.get('ConsumerDecisionFactor', 0)
    })

@app.route('/api/entities')
def get_entities():
    """获取实体列表"""
    status = request.args.get('status', 'pending')
    entity_type = request.args.get('type', 'all')
    page = int(request.args.get('page', 1))
    limit = int(request.args.get('limit', 10))
    offset = (page - 1) * limit
    
    conn = get_db()
    cursor = conn.cursor()
    
    # 构建查询
    where_clause = "WHERE status = ?"
    params = [status]
    
    if entity_type != 'all':
        where_clause += " AND entity_type = ?"
        params.append(entity_type)
    
    # 查询实体
    cursor.execute(f'''
        SELECT * FROM entities 
        {where_clause}
        ORDER BY confidence DESC
        LIMIT ? OFFSET ?
    ''', params + [limit, offset])
    
    rows = cursor.fetchall()
    
    # 查询总数
    cursor.execute(f'''
        SELECT COUNT(*) as total FROM entities {where_clause}
    ''', params)
    
    total = cursor.fetchone()['total']
    conn.close()
    
    entities = []
    for row in rows:
        entity = {
            "id": row['id'],
            "entity_type": row['entity_type'],
            "name": row['name'],
            "attributes": json.loads(row['attributes']) if row['attributes'] else {},
            "confidence": row['confidence'],
            "source_quote": row['source_quote'],
            "source_session": row['source_session'],
            "status": row['status'],
            "reviewer_notes": row['reviewer_notes'],
            "modified_attributes": json.loads(row['modified_attributes']) if row['modified_attributes'] else None,
            # Phase 1: 时序字段
            "first_seen": row['first_seen'],
            "last_seen": row['last_seen'],
            "frequency": row['frequency'],
            "source_sessions": json.loads(row['source_sessions']) if row['source_sessions'] else [],
            "created_at": row['created_at'],
            "updated_at": row['updated_at']
        }
        
        # Phase 3: 检查是否过期
        if entity['last_seen']:
            from datetime import datetime
            try:
                last_seen = datetime.fromisoformat(entity['last_seen'].replace('Z', '+00:00'))
                days_since = (datetime.now() - last_seen).days
                entity['is_expired'] = days_since > 90
            except:
                entity['is_expired'] = False
        else:
            entity['is_expired'] = False
        
        # Phase 2: 检查相似实体
        from knowledge_store import EntityMerger, KnowledgeStore
        store = KnowledgeStore(DB_PATH)
        merger = EntityMerger(store)
        similar = merger.find_similar_entities(entity['id'], threshold=0.9)
        entity['similar_count'] = len(similar)
        
        entities.append(entity)
    
    return jsonify({
        "entities": entities,
        "total": total,
        "page": page,
        "pages": (total + limit - 1) // limit
    })

@app.route('/api/entities/<entity_id>/review', methods=['POST'])
def review_entity(entity_id):
    """审核实体"""
    data = request.json
    action = data.get('action')
    notes = data.get('notes', '')
    
    if action not in ['approve', 'reject', 'modify']:
        return jsonify({"error": "Invalid action"}), 400
    
    conn = get_db()
    cursor = conn.cursor()
    
    # 获取旧值
    cursor.execute('SELECT attributes FROM entities WHERE id = ?', (entity_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "Entity not found"}), 404
    
    old_values = row['attributes']
    new_status = 'approved' if action == 'approve' else 'rejected' if action == 'reject' else 'modified'
    
    # 更新实体
    cursor.execute('''
        UPDATE entities 
        SET status = ?, reviewer_notes = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    ''', (new_status, notes, entity_id))
    
    # 记录日志
    cursor.execute('''
        INSERT INTO review_logs (item_id, item_type, action, reviewer, notes, old_values)
        VALUES (?, 'entity', ?, 'admin', ?, ?)
    ''', (entity_id, action, notes, old_values))
    
    conn.commit()
    conn.close()
    
    return jsonify({"success": True, "status": new_status})

@app.route('/api/entities/<entity_id>', methods=['GET'])
def get_entity(entity_id):
    """获取单个实体"""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('SELECT * FROM entities WHERE id = ?', (entity_id,))
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        return jsonify({"error": "Entity not found"}), 404
    
    return jsonify({
        "id": row['id'],
        "entity_type": row['entity_type'],
        "name": row['name'],
        "attributes": json.loads(row['attributes']) if row['attributes'] else {},
        "confidence": row['confidence'],
        "source_quote": row['source_quote'],
        "source_session": row['source_session'],
        "status": row['status'],
        "reviewer_notes": row['reviewer_notes']
    })

@app.route('/api/relations')
def get_relations():
    """获取关系列表"""
    status = request.args.get('status', 'pending')
    page = int(request.args.get('page', 1))
    limit = int(request.args.get('limit', 10))
    offset = (page - 1) * limit
    
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT * FROM relations 
        WHERE status = ?
        ORDER BY confidence DESC
        LIMIT ? OFFSET ?
    ''', (status, limit, offset))
    
    rows = cursor.fetchall()
    conn.close()
    
    relations = []
    for row in rows:
        relations.append({
            "id": row['id'],
            "relation_type": row['relation_type'],
            "source_entity": row['source_entity'],
            "target_entity": row['target_entity'],
            "attributes": json.loads(row['attributes']) if row['attributes'] else {},
            "fact_statement": row['fact_statement'],
            "confidence": row['confidence'],
            "status": row['status']
        })
    
    return jsonify({
        "relations": relations,
        "page": page
    })

# ============ Phase 2-4 API扩展 ============

@app.route('/api/entities/<entity_id>/similar')
def get_similar_entities(entity_id):
    """获取相似实体 (Phase 2)"""
    from knowledge_store import EntityMerger, KnowledgeStore
    
    store = KnowledgeStore(DB_PATH)
    merger = EntityMerger(store)
    
    similar = merger.find_similar_entities(entity_id, threshold=0.9)
    
    return jsonify({
        "entity_id": entity_id,
        "similar_count": len(similar),
        "similar": similar
    })

@app.route('/api/entities/<entity_id>/merge', methods=['POST'])
def merge_entities_api(entity_id):
    """合并实体 (Phase 2)"""
    from knowledge_store import EntityMerger, KnowledgeStore
    
    data = request.get_json()
    merge_ids = data.get('merge_ids', [])
    conflict_resolution = data.get('conflict_resolution', 'keep_all')
    
    store = KnowledgeStore(DB_PATH)
    merger = EntityMerger(store)
    
    result = merger.merge_entities(entity_id, merge_ids, conflict_resolution)
    
    return jsonify(result)

@app.route('/api/entities/<entity_id>/timeline')
def get_entity_timeline_api(entity_id):
    """获取实体时序信息 (Phase 3)"""
    from knowledge_store import KnowledgeStore
    
    store = KnowledgeStore(DB_PATH)
    timeline = store.get_entity_timeline(entity_id)
    
    return jsonify(timeline)

@app.route('/api/entities/expired')
def get_expired_entities_api():
    """获取过期实体 (Phase 3)"""
    from knowledge_store import KnowledgeStore
    
    days = request.args.get('days', 90, type=int)
    
    store = KnowledgeStore(DB_PATH)
    expired = store.get_expired_entities(days)
    
    return jsonify({
        "expired_count": len(expired),
        "expired": expired
    })

if __name__ == '__main__':
    print("=" * 60)
    print("知识图谱审核系统 - Web服务")
    print("=" * 60)
    print(f"数据库: {DB_PATH}")
    print("访问地址: http://localhost:5000")
    print("=" * 60)
    
    app.run(host='0.0.0.0', port=5002, debug=False)
