# 知识图谱字段扩展方案

## 核心设计：Schema-Free（无固定模式）

```python
# 传统数据库：固定字段，扩展需改表结构
ALTER TABLE products ADD COLUMN weight VARCHAR(20);  -- ❌ 需要DBA操作

# 知识图谱：JSON格式，动态扩展
{
    "id": "product_林内_GD32",
    "type": "Product",
    "name": "林内GD32",
    "attributes": {
        "brand": "林内",           # 基础字段
        "model": "GD32",
        "price": 3199,
        "weight": "15kg",          # ✅ 随时添加，无需改表
        "warranty": "3年",         # ✅ 随时添加
        "color": "白色",           # ✅ 随时添加
        "new_field": "任意值"      # ✅ 完全自由
    }
}
```

---

## 扩展方式

### 方式1：AI自动提取（推荐）

```python
class AutoExtender:
    """自动扩展实体属性"""
    
    def extend_from_session(self, entity_id, session_data):
        """从客服会话自动发现新属性"""
        entity = self.kg.get_entity(entity_id)
        existing_attrs = entity['attributes']
        
        # 从会话中提取潜在属性
        new_attrs = {}
        
        for msg in session_data['messages']:
            content = msg['content']
            
            # 模式匹配：数字+单位
            import re
            
            # 重量
            weight_match = re.search(r'(\d+(?:\.\d+)?)\s*(kg|公斤)', content)
            if weight_match and 'weight' not in existing_attrs:
                new_attrs['weight'] = f"{weight_match.group(1)}{weight_match.group(2)}"
            
            # 尺寸
            size_match = re.search(r'(\d+)[×x](\d+)[×x](\d+)\s*mm', content)
            if size_match and 'size' not in existing_attrs:
                new_attrs['size'] = f"{size_match.group(1)}×{size_match.group(2)}×{size_match.group(3)}mm"
            
            # 功率
            power_match = re.search(r'(\d+)W|(\d+)瓦', content)
            if power_match and 'power' not in existing_attrs:
                new_attrs['power'] = f"{power_match.group(1) or power_match.group(2)}W"
            
            # 保修期
            warranty_match = re.search(r'(\d+)年(?:质保|保修)', content)
            if warranty_match and 'warranty' not in existing_attrs:
                new_attrs['warranty'] = f"{warranty_match.group(1)}年"
        
        # 合并新属性
        if new_attrs:
            self.kg.update_entity(entity_id, {
                **existing_attrs,
                **new_attrs,
                '_extended_at': datetime.now().isoformat(),
                '_extended_from': session_data['session_id']
            })
            
            return new_attrs
        
        return {}
```

### 方式2：人工添加

```python
# 审核界面直接添加新字段
def add_custom_field(entity_id, field_name, field_value):
    """添加自定义字段"""
    entity = kg.get_entity(entity_id)
    
    # 直接扩展attributes
    updates = {
        field_name: field_value,
        '_modified_by': '质检员A',
        '_modified_at': datetime.now().isoformat()
    }
    
    kg.update_entity(entity_id, {**entity['attributes'], **updates})
```

### 方式3：批量导入

```python
# 从Excel/CSV批量扩展
import pandas as pd

def batch_extend_from_excel(excel_path):
    """从Excel批量扩展属性"""
    df = pd.read_excel(excel_path)
    
    for _, row in df.iterrows():
        entity_id = row['entity_id']
        
        # 动态提取所有字段
        new_attrs = {}
        for col in df.columns:
            if col != 'entity_id' and pd.notna(row[col]):
                new_attrs[col] = row[col]
        
        # 更新实体
        entity = kg.get_entity(entity_id)
        if entity:
            kg.update_entity(entity_id, {**entity['attributes'], **new_attrs})
```

---

## 实际示例：GD32属性自动扩展

### 初始状态
```json
{
    "id": "product_林内_GD32",
    "attributes": {
        "brand": "林内",
        "model": "GD32",
        "price": 3199
    }
}
```

### 会话1：用户问"GD32多重？"
```
客服回答：15公斤
```
**自动扩展：**
```json
{
    "attributes": {
        "brand": "林内",
        "model": "GD32",
        "price": 3199,
        "weight": "15kg"        // ✅ 自动添加
    }
}
```

### 会话2：用户问"GD32尺寸多大？"
```
客服回答：380×532×150mm
```
**自动扩展：**
```json
{
    "attributes": {
        "brand": "林内",
        "model": "GD32",
        "price": 3199,
        "weight": "15kg",
        "size": "380×532×150mm"  // ✅ 自动添加
    }
}
```

### 会话3：用户问"保修多久？"
```
客服回答：3年整机保修
```
**自动扩展：**
```json
{
    "attributes": {
        "brand": "林内",
        "model": "GD32",
        "price": 3199,
        "weight": "15kg",
        "size": "380×532×150mm",
        "warranty": "3年"         // ✅ 自动添加
    }
}
```

---

## 字段命名规范（建议）

| 类别 | 命名规则 | 示例 |
|------|---------|------|
| **基础属性** | 英文小写 | brand, model, price |
| **规格参数** | 英文小写 | size, weight, power |
| **业务属性** | 英文小写 | warranty, return_policy |
| **自定义字段** | 英文小写+下划线 | min_pressure, max_temp |
| **系统字段** | 下划线前缀 | _created_at, _source |

---

## 扩展性对比

| 能力 | 传统数据库 | 知识图谱 |
|------|----------|---------|
| **添加字段** | 需ALTER TABLE | ✅ 直接写入JSON |
| **字段类型** | 需预定义 | ✅ 任意类型（字符串/数字/列表） |
| **字段数量** | 固定 | ✅ 无限扩展 |
| **历史追溯** | 需额外表 | ✅ 版本控制内置 |
| **冲突处理** | 复杂 | ✅ 自动合并/标记 |

---

## 金总，核心结论

> **知识图谱的属性字段是完全自由的JSON格式，可以：**
> 1. **自动扩展** - AI从会话中提取新属性自动添加
> 2. **人工扩展** - 审核界面随时添加新字段
> 3. **批量扩展** - Excel导入大量新属性
> 
> **无需改表结构，无需停机维护，即加即用。**

---

**是否演示自动扩展功能？**