import re

with open('index.html', 'r') as f:
    content = f.read()

# 找到 setupFilters 函数
start = content.find('        // 设置筛选器')
if start == -1:
    print('未找到 setupFilters 函数')
    exit(1)

end = content.find('        // 选择/取消选择', start)
if end == -1:
    print('未找到 setupFilters 函数结束')
    exit(1)

old_func = content[start:end]
print('找到的旧函数:')
print(old_func[:200])

new_func = '''        // 设置筛选器
        function setupFilters() {
            // 类型筛选
            document.getElementById('type-filters').addEventListener('click', function(e) {
                const option = e.target.closest('.filter-option');
                if (!option) return;
                
                document.querySelectorAll('#type-filters .filter-option').forEach(el => {
                    el.classList.remove('active');
                });
                option.classList.add('active');
                currentType = option.dataset.type;
                currentPage = 1;
                
                // 更新审核状态计数（基于当前类型）
                updateStats();
                
                renderEntityList();
            });
            
            // 状态筛选
            document.getElementById('status-filters').addEventListener('click', function(e) {
                const option = e.target.closest('.filter-option');
                if (!option) return;
                
                document.querySelectorAll('#status-filters .filter-option').forEach(el => {
                    el.classList.remove('active');
                });
                option.classList.add('active');
                currentStatus = option.dataset.status;
                currentPage = 1;
                
                // 更新实体类型计数（基于当前状态）
                updateStats();
                
                renderEntityList();
            });
        }
'''

content = content.replace(old_func, new_func)

with open('index.html', 'w') as f:
    f.write(content)

print('✅ setupFilters 函数已更新')
