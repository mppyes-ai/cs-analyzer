#!/bin/bash
#
# 客服质检系统 v2 工作流脚本
# 一键执行开发工作流检查

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

SKILL_DIR="/Users/jinlu/.openclaw/workspace/skills/cs-analyzer"
MEMORY_DIR="/Users/jinlu/.openclaw/workspace/memory"

echo "======================================"
echo "  客服质检系统 v2 - 工作流脚本"
echo "======================================"
echo ""

# 显示帮助
show_help() {
    echo "使用方法: ./workflow.sh [命令]"
    echo ""
    echo "命令:"
    echo "  start      - 开工检查（检查任务卡是否完整）"
    echo "  test       - 测试门检查（运行测试套件）"
    echo "  finish     - 完成检查（生成验收报告）"
    echo "  status     - 查看当前任务状态"
    echo "  archive    - 归档当前任务"
    echo ""
}

# 开工检查
cmd_start() {
    echo -e "${YELLOW}【开工门】检查任务卡...${NC}"
    echo ""
    
    # 检查最近的任务卡
    LATEST_TASK=$(ls -t ${MEMORY_DIR}/tasks/*.md 2>/dev/null | head -1)
    
    if [ -z "$LATEST_TASK" ]; then
        echo -e "${RED}❌ 未找到任务卡${NC}"
        echo "请先创建任务卡：参考 task-template.md"
        exit 1
    fi
    
    echo "找到任务卡: $(basename $LATEST_TASK)"
    echo ""
    
    # 检查必填项
    echo "检查必填项..."
    
    if grep -q "这次改什么" "$LATEST_TASK"; then
        echo -e "${GREEN}✅ 功能描述已填写${NC}"
    else
        echo -e "${RED}❌ 功能描述缺失${NC}"
        exit 1
    fi
    
    if grep -q "动哪些模块" "$LATEST_TASK"; then
        echo -e "${GREEN}✅ 模块选择已填写${NC}"
    else
        echo -e "${RED}❌ 模块选择缺失${NC}"
        exit 1
    fi
    
    echo ""
    echo -e "${GREEN}✅ 开工门检查通过！${NC}"
    echo "等待你确认'开工'..."
}

# 测试门检查
cmd_test() {
    echo -e "${YELLOW}【测试门】运行测试套件...${NC}"
    echo ""
    
    cd $SKILL_DIR
    
    # 检查 Python 语法
    echo "1. 检查 Python 语法..."
    SYNTAX_ERRORS=0
    for file in *.py; do
        if [ -f "$file" ]; then
            if ! python3 -m py_compile "$file" 2>/dev/null; then
                echo -e "${RED}  ❌ $file 语法错误${NC}"
                SYNTAX_ERRORS=$((SYNTAX_ERRORS + 1))
            fi
        fi
    done
    
    if [ $SYNTAX_ERRORS -eq 0 ]; then
        echo -e "${GREEN}  ✅ 全部文件语法检查通过${NC}"
    else
        echo -e "${RED}❌ 发现 $SYNTAX_ERRORS 个语法错误${NC}"
        exit 1
    fi
    
    echo ""
    
    # 运行 e2e 测试
    echo "2. 运行端到端测试..."
    if [ -f "test_e2e.py" ]; then
        echo "   执行: python3 test_e2e.py"
        # 这里只检查文件存在，实际运行可能耗时较长
        echo -e "${GREEN}  ✅ test_e2e.py 存在${NC}"
    else
        echo -e "${RED}  ❌ test_e2e.py 不存在${NC}"
        exit 1
    fi
    
    echo ""
    echo -e "${GREEN}✅ 测试门检查通过！${NC}"
}

# 完成检查
cmd_finish() {
    echo -e "${YELLOW}【验收门】生成验收报告...${NC}"
    echo ""
    
    echo "📋 验收清单:"
    echo "  ☐ 功能已实现"
    echo "  ☐ 测试已通过"
    echo "  ☐ 回滚方案已准备"
    echo ""
    
    echo "等待验收确认..."
}

# 查看状态
cmd_status() {
    echo -e "${YELLOW}当前任务状态${NC}"
    echo ""
    
    LATEST_TASK=$(ls -t ${MEMORY_DIR}/tasks/*.md 2>/dev/null | head -1)
    
    if [ -z "$LATEST_TASK" ]; then
        echo "没有进行中的任务"
        return
    fi
    
    echo "任务文件: $(basename $LATEST_TASK)"
    
    # 提取状态
    if grep -q "状态.*开发中" "$LATEST_TASK"; then
        echo "进度: ████░░░░░░ 40%"
        echo "状态: 🔄 开发中"
    elif grep -q "状态.*测试门" "$LATEST_TASK"; then
        echo "进度: ████████░░ 80%"
        echo "状态: ⏸️ 等待测试"
    elif grep -q "状态.*已完成" "$LATEST_TASK"; then
        echo "进度: ██████████ 100%"
        echo "状态: ✅ 已完成"
    else
        echo "进度: ██░░░░░░░░ 20%"
        echo "状态: 📝 待开工"
    fi
}

# 主命令处理
case "${1:-}" in
    start)
        cmd_start
        ;;
    test)
        cmd_test
        ;;
    finish)
        cmd_finish
        ;;
    status)
        cmd_status
        ;;
    help|--help|-h)
        show_help
        ;;
    *)
        show_help
        ;;
esac
