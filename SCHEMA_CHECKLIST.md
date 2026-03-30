# Schema 变更 Checklist

**强制要求**：任何数据库表结构的变更，必须完成以下所有检查项后方可提交代码。

---

## 变更前准备

- [ ] 1. 已备份现有数据库（如生产环境有数据）
- [ ] 2. 已评估变更对现有功能的影响
- [ ] 3. 已确定变更的必要性和紧急程度

---

## 变更实施

### 2.1 表结构更新
- [ ] 1. 更新 `database_schema.md` 文档，记录新字段/表
- [ ] 2. 更新初始化 SQL 脚本（如 `init_database.sql`）
- [ ] 3. 编写 ALTER TABLE 脚本用于现有表升级
- [ ] 4. 更新 `config.py` 中的 Schema 版本号（如有）

### 2.2 代码层更新
- [ ] 1. 更新 `db_utils.py` 中的查询语句（SELECT/INSERT/UPDATE）
- [ ] 2. 更新所有引用该表的 Python 文件
- [ ] 3. 检查 Streamlit 页面中的查询语句
- [ ] 4. 更新测试用例中的 Mock 数据

### 2.3 文档层更新
- [ ] 1. 更新 API 文档（如字段说明）
- [ ] 2. 更新 README 中的相关说明
- [ ] 3. 通知团队成员变更内容

---

## 变更验证

### 3.1 自动化检查
- [ ] 1. 运行 `python check_consistency.py`，确认无错误
- [ ] 2. 运行单元测试，确认全部通过
- [ ] 3. 运行端到端测试，确认流程正常

### 3.2 手动验证
- [ ] 1. 新环境初始化测试（删除数据库后重新初始化）
- [ ] 2. 现有数据迁移测试（如适用）
- [ ] 3. 关键业务流程验证

---

## 提交流程

1. **代码提交前必须执行：**
   ```bash
   python check_consistency.py --ci
   ```
   如有错误，禁止提交。

2. **PR 描述中必须包含：**
   - Schema 变更摘要（新增/修改/删除）
   - 影响范围说明
   - 回滚方案

3. **Review 时必须检查：**
   - 本 Checklist 是否全部勾选
   - 是否有遗漏的引用点
   - 是否影响现有数据

---

## 示例：添加新字段

假设要给 `sessions` 表添加 `priority` 字段：

**Step 1: 表结构**
```sql
-- migrate_20260321_add_priority.sql
ALTER TABLE sessions ADD COLUMN priority TEXT DEFAULT 'normal';
```

**Step 2: 更新 database_schema.md**
```markdown
| 字段名 | 类型 | 说明 |
|--------|------|------|
| priority | TEXT | 会话优先级 (high/normal/low) |
```

**Step 3: 更新 db_utils.py**
```python
# 更新 load_sessions() 查询
cursor.execute("""
    SELECT session_id, ..., priority FROM sessions
""")

# 更新 save_to_database() 插入
cursor.execute("""
    INSERT INTO sessions (..., priority) VALUES (..., ?)
""", (..., session_data.get('priority', 'normal')))
```

**Step 4: 运行检查**
```bash
python check_consistency.py --ci
```

**Step 5: 验证**
- [ ] 新环境初始化正常
- [ ] 现有数据迁移正常
- [ ] 页面显示正常

---

## 紧急修复流程

如生产环境需要紧急修复，可先执行最小化变更，但 **24小时内必须补齐：**

- [ ] 补充文档更新
- [ ] 补充测试用例
- [ ] 补充回滚脚本
- [ ] 通知团队成员

---

**违规后果**：未按此 Checklist 执行导致的生产事故，责任人需承担主要责任。
