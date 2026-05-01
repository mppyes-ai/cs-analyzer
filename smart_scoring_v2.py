"""智能评分引擎 v2.6.1 - 基于规则知识库的4维度评分

核心功能：
1. 会话预分析（scene/intent/sentiment）
2. 规则检索（SQLite + LanceDB混合）
3. CoT评分输出（命中规则 + 判定过程）
4. 结果可解释化
5. 【v2.4】批量评分支持
6. 【v2.6.1】跨场景合并评分（不再按场景分组）

作者: 小虾米
更新: 2026-04-04（v2.6.1: 跨场景合并优化）
"""

import json
import os
import asyncio
import re
from typing import Dict, List, Optional, Tuple
from datetime import datetime

# 导入知识库模块
import sys
sys.path.insert(0, os.path.dirname(__file__))

from knowledge_base_v2 import (
    get_approved_rules, search_rules_by_vector, generate_combined_text
)
from config import LLM_CONFIG

# 导入本地意图分类器（已弃用，保留导入避免兼容性问题）
try:
    from intent_classifier_v3 import FunnelIntentClassifier, IntentClassificationResult
    INTENT_CLASSIFIER_AVAILABLE = True
except ImportError:
    INTENT_CLASSIFIER_AVAILABLE = False
    print("⚠️ 漏斗式意图分类器未导入，已弃用（场景/意图/情绪由LLM评分时识别）")

# 导入混合检索
try:
    from hybrid_retriever import HybridRuleRetriever
    HYBRID_RETRIEVER_AVAILABLE = True
except ImportError:
    HYBRID_RETRIEVER_AVAILABLE = False
    print("⚠️ 混合检索模块未导入，将使用基础向量检索")


# ========== 使用统一Embedding单例 ==========
from embedding_utils import get_embedding_model

# ========== 自定义异常类 ==========

class ScoringError(Exception):
    """AI评分失败异常"""
    def __init__(self, message: str, error_type: str = "unknown", details: Dict = None):
        self.message = message
        self.error_type = error_type
        self.details = details or {}
        super().__init__(self.message)
    
    def __str__(self):
        return f"[{self.error_type}] {self.message}"


# ========== 评分Prompt模板 ==========

# 【Phase 1优化】固定System Prompt，最大化oMLX前缀缓存命中率
# 注意：本字符串必须保持完全不变，任何修改都会废掉oMLX的前缀缓存
# 所有per-request变化的内容（规则、会话、session_id）严禁出现在此处
SYSTEM_PROMPT_V2 = """你是一位专业的客服质检专家，负责对客服会话进行4维度质量评分。严格按JSON格式输出评分结果。输出必须是合法JSON，不包含任何解释性文字。

## 评分维度
1. **专业性 (Professionalism)** - 产品知识准确性
2. **标准化 (Standardization)** - 服务规范（礼貌用语、响应速度）
3. **政策执行 (Policy Execution)** - 促销/售后政策传达
4. **转化能力 (Conversion)** - 销售引导能力

## 评分要求

1. **先分析会话**：提取主题、用户意图、情绪、关键博弈轮次
2. **识别场景分类**：判断会话属于哪种场景（售前咨询/售中服务/售后维修/客诉处理/活动咨询/其他），仅选一种最匹配的
3. **识别用户意图**：判断用户核心意图（咨询/投诉/退款/维修/安装/比价/其他）
4. **识别用户情绪**：判断用户情绪状态（positive/neutral/negative/urgent/complaint）
5. **逐维度评分**：1-5分，基于checkpoints逐项检查
6. **引用规则**：明确说明参考了哪条规则（rule_id）
7. **输出推理过程**：用自然语言描述为什么给这个分数，直接陈述事实和依据，不要加"判定过程："等前缀
8. **总分计算**：4-20分，风险分级（🔴高风险≤8 🟡中风险9-12 🟢正常≥13）

## 场景识别指南

请在 `session_analysis` 中增加 `scene_category` 字段，判断标准：
- **售前咨询**：用户询问产品价格、功能、推荐、对比、适用场景
- **售中服务**：订单处理、物流查询、支付问题、修改订单
- **售后维修**：故障报修、保修服务、维修进度、配件更换
- **客诉处理**：投诉、退货、退款、不满、质量质疑
- **活动咨询**：促销活动、优惠政策、赠品、保价、活动规则
- **其他**：以上都不符合的情况

## 意图识别指南

请在 `session_analysis` 中增加 `user_intent` 字段，判断标准：
- **咨询**：询问产品信息、价格、功能
- **投诉**：表达不满、质疑服务质量
- **退款**：明确要求退货退款
- **维修**：报告故障、请求维修
- **安装**：询问安装事宜、预约安装
- **比价**：对比不同产品、寻求推荐
- **其他**：以上都不符合

## 情绪识别指南

请在 `session_analysis` 中增加 `user_sentiment` 字段，判断标准：
- **positive**：满意、感谢、期待
- **neutral**：正常询问、无情绪波动
- **negative**：不满、失望、抱怨
- **urgent**：紧急、催促、焦虑
- **complaint**：明确投诉、威胁维权

注意：仅选择一种最匹配的场景、意图和情绪。

## 输出格式（必须严格遵循）

```json
{
  "session_analysis": {
    "theme": "会话主题（20-30字简介）",
    "user_intent": "用户意图（咨询/投诉/退款/维修/安装/比价/其他）",
    "user_sentiment": "用户情绪（positive/neutral/negative/urgent/complaint）",
    "scene_category": "场景分类（仅一种：售前咨询/售中服务/售后维修/客诉处理/活动咨询/其他）",
    "key_moments": ["关键轮次1", "关键轮次2"]
  },
  "dimension_scores": {
    "professionalism": {
      "score": 3,
      "reasoning": "用户询问产品参数，客服回答准确但缺少对比说明，符合3分标准",
      "evidence": ["证据片段1", "证据片段2"],
      "referenced_rules": ["rule_id"]
    },
    "standardization": {
      "score": 2,
      "reasoning": "响应及时但礼貌用语不够规范，存在服务瑕疵",
      "evidence": ["证据片段"],
      "referenced_rules": []
    },
    "policy_execution": {
      "score": 3,
      "reasoning": "政策传达准确但时机把握不当",
      "evidence": [],
      "referenced_rules": ["rule_id"]
    },
    "conversion": {
      "score": 2,
      "reasoning": "未主动挖掘用户需求，错失转化机会",
      "evidence": [],
      "referenced_rules": []
    }
  },
  "summary": {
    "total_score": 10,
    "risk_level": "中风险",
    "strengths": ["亮点1", "亮点2"],
    "issues": ["问题1", "问题2"],
    "suggestions": ["建议1", "建议2"]
  }
}
```

## 评分标准参考

### 5分标准（优秀）
- 完全符合checkpoints所有要求
- 超出预期的表现

### 3分标准（及格）
- 基本符合但存在瑕疵
- 有改进空间

### 1分标准（不合格）
- 触碰底线行为
- 严重违规

### 引用规则说明
1. **使用知识库规则时**：`referenced_rules`填写具体的规则ID（如：`["rule_xxx"]`）
2. **知识库未覆盖时**：`referenced_rules`必须填写空数组（`[]`），严禁自行编造规则名称或描述
3. **检查方式**：如果本次评分参考的规则列表为空，则所有维度的`referenced_rules`都必须是`[]`

### 通用标准定义
- **电商行业通用标准**：响应时效、服务态度、问题闭环等电商客服基本规范
- **客服服务通用标准**：首响及时、礼貌用语、主动解决、结束规范等服务行业基本要求
- **消费者认可标准**：信息准确、不推诿、有效解决用户问题等消费者普遍期望

直接输出JSON，不要Markdown代码块。"""


# 【方案A】固定User Prompt前缀，凑满4096 tokens，确保前2个block稳定命中
# 注意：本字符串必须保持完全不变，任何修改都会废掉oMLX的前缀缓存
FIXED_USER_PREFIX = """
## 评分参考资料（供本次评分使用）

### 维度一：专业性 详细判定细则
**5分标准**：回答准确，信息完整，主动提供超出预期的专业建议
- 产品参数解释清晰无误（型号、功率、尺寸、材质等）
- 能对比不同型号的差异（优缺点分析）
- 主动提供安装/使用建议（注意事项、最佳实践）
- 能解答复杂技术问题（故障排查、原理说明）
- 提供行业最佳实践参考（竞品对比、市场趋势）
- 主动补充用户未问到的关键信息

**4分标准**：回答准确，信息较完整
- 核心参数正确
- 能回答大部分问题
- 偶尔需要补充细节
- 能提供基本的技术支持

**3分标准**：回答基本准确，信息略有缺失
- 核心参数正确但缺少细节
- 能回答基本问题但不够深入
- 需要用户追问才能完善
- 技术解释过于简单

**2分标准**：回答有瑕疵，部分信息错误
- 次要参数错误（如尺寸偏差、功率误差）
- 遗漏重要信息（如安装条件、使用限制）
- 给用户造成困惑（前后矛盾、含糊其辞）
- 技术解释错误但不影响主要决策

**1分标准**：事实错误，误导用户
- 参数错误或与产品不符（如把16升说成13升）
- 给出不可行的方案（如推荐不匹配的配件）
- 导致用户决策失误（如错误的价格信息）
- 技术原理完全错误
- 推荐已停产或不存在的产品

### 维度二：标准化 详细判定细则
**5分标准**：礼貌规范，响应及时(<30秒)，用语得体
- 使用标准礼貌用语（您好、请、谢谢、再见、不客气）
- 响应迅速不拖沓（首响<30秒，后续响应<60秒）
- 格式规范统一（段落清晰、标点正确、无错别字）
- 主动确认用户需求（"请问您是咨询...对吗？"）
- 结束语规范完整（"如有其他问题随时联系，祝您生活愉快！"）
- 主动提供后续服务（"我加您微信，后续有问题随时找我"）

**4分标准**：礼貌规范，响应较快
- 用语得体
- 响应时间可接受（首响<60秒）
- 格式基本规范
- 能主动结束对话

**3分标准**：基本礼貌，响应一般
- 用语基本得体
- 响应时间可接受（首响<2分钟）
- 偶尔遗漏礼貌用语
- 格式基本正确

**2分标准**：礼貌欠缺，响应较慢
- 缺少必要礼貌用语（如无问候、无结束语）
- 响应时间偏长（首响>2分钟）
- 格式不够规范（错别字、标点错误）
- 态度冷淡

**1分标准**：态度恶劣，答非所问
- 使用不当用语（不耐烦、嘲讽、推诿）
- 明显敷衍用户（复制粘贴无关内容）
- 拒绝回答合理问题（"这个我不知道，您问别人吧"）
- 态度傲慢或冷漠

### 维度三：政策执行 详细判定细则
**5分标准**：政策传达准确及时，主动告知相关权益
- 准确说明促销规则（满减、折扣、赠品条件）
- 主动提醒售后政策（保修期限、退换货条件、维修流程）
- 清晰解释保修条款（整机保修、核心部件保修、延保服务）
- 告知用户可享受的优惠（国补、以旧换新、会员折扣）
- 主动提供政策对比（不同渠道政策差异、活动期间vs平时）
- 提醒用户注意政策限制（时间限制、数量限制、地区限制）

**4分标准**：政策传达准确，较主动
- 核心政策无误
- 能回答政策相关问题
- 偶尔主动提醒
- 能提供政策依据

**3分标准**：政策传达基本正确
- 核心政策无误
- 但缺少细节说明
- 被动回答政策问题
- 偶尔需要查询确认

**2分标准**：政策传达有误或遗漏
- 次要政策错误（如保修期说错）
- 遗漏重要政策信息（如未告知退换货条件）
- 给用户造成损失风险（如错过优惠期）
- 政策解释含糊

**1分标准**：编造政策，推诿责任
- 虚构不存在的政策（"我们店不支持7天无理由退货"）
- 推卸应有责任（"这是厂家的问题，您找厂家吧"）
- 损害用户权益（隐瞒保修权利）
- 故意误导用户（"这个活动已经结束了"实际未结束）

### 维度四：转化能力 详细判定细则
**5分标准**：主动挖掘需求，成功促成转化
- 询问用户使用场景（几口人、房屋面积、使用频率）
- 推荐合适型号（基于需求匹配，而非推销最贵）
- 引导下单或留资（"我帮您预约到店体验？"）
- 提供购买决策支持（价格对比、优惠计算、付款方式）
- 跟进转化结果（"您下单了吗？我帮您确认库存"）
- 处理价格异议（"这款虽然贵300元，但省气15%"）
- 创造紧迫感（"活动今晚结束，现在下单锁定优惠"）

**4分标准**：较主动挖掘需求
- 能识别购买信号（"什么时候送货？""怎么付款？"）
- 适时推荐产品（用户表现出兴趣时）
- 提供购买建议（"这款性价比最高"）
- 能处理简单异议

**3分标准**：被动应答需求
- 回答用户问题
- 但不主动推进
- 等待用户主动询问
- 不挖掘潜在需求

**2分标准**：转化意识薄弱
- 仅做基础应答
- 错失明显机会（用户问"怎么买？"只给链接）
- 未识别购买信号
- 不处理价格异议

**1分标准**：完全被动，客户流失
- 仅做基础应答
- 错失明显机会
- 用户主动离开（"我再看看"后无跟进）
- 甚至阻碍转化（"这个很贵，您考虑清楚"）

### Few-shot 示例

**示例1（优秀案例 - 热水器咨询）**：
用户："这款热水器怎么样？"
客服："您好！这款是林内16升恒温燃气热水器RUS-16QC08，适合3-4口家庭。相比13升型号，出水更稳定，冬天洗澡不会忽冷忽热。目前活动期间有国补优惠，原价3999元，国补后到手价3199元，节省800元。我可以帮您算下具体优惠。请问您家厨房到卫生间距离大概多远？如果超过10米建议选带循环泵的型号，出水更快。"
评分：
- 专业性5分：准确提供型号、价格、技术参数，主动建议循环泵
- 标准化4分：礼貌用语规范，主动询问需求
- 政策执行5分：主动告知国补政策，计算优惠金额
- 转化能力5分：主动挖掘安装场景，引导确认需求

**示例2（及格案例 - 价格咨询）**：
用户："这款热水器多少钱？"
客服："您好，这款16升恒温款，价格2999元。"
用户："有优惠吗？"
客服："目前有活动，具体优惠您下单时能看到。"
评分：
- 专业性3分：基本参数正确但缺少型号、技术细节
- 标准化3分：基本礼貌但结束语缺失
- 政策执行2分：未主动告知促销政策，让用户自己查看
- 转化能力2分：未主动挖掘需求，未引导下单

**示例3（不合格案例 - 保修咨询）**：
用户："这款保修多久？"
客服："保修一年。"
用户："整机都保吗？"
客服："对的。"
（实际：整机保修3年，核心部件8年）
评分：
- 专业性1分：保修年限错误，核心部件保修政策未说明
- 标准化3分：回答过于简短，缺乏详细说明
- 政策执行1分：错误传达保修政策，可能引发后续纠纷
- 转化能力2分：未利用保修优势促进转化

**示例4（优秀案例 - 故障处理）**：
用户："热水器不出热水了"
客服："您好，请问是突然不出热水还是逐渐变凉？机器有报错代码吗？"
用户："突然不出，显示E1"
客服："E1是点火失败代码。请先检查：1.燃气阀门是否打开 2.水压是否正常 3.电源是否接通。如果都正常可能是点火器故障，建议预约上门检修。保修期内免费维修，我帮您预约？"
评分：
- 专业性5分：准确识别故障代码，提供排查步骤
- 标准化4分：主动询问细节，提供解决方案
- 政策执行5分：主动提醒保修政策，提供免费维修
- 转化能力4分：主动提供预约服务

**示例5（及格案例 - 安装咨询）**：
用户："这款能装吗？"
客服："可以安装的。"
用户："需要准备什么？"
客服："需要预留燃气管和冷热水管。"
评分：
- 专业性3分：基本回答但缺少具体尺寸、安装条件
- 标准化3分：回答简洁但缺少主动建议
- 政策执行3分：未提及安装费用、保修政策
- 转化能力2分：未主动确认安装时间、未引导下单

### 输出格式细则
- 必须输出合法JSON
- 数组和对象必须完整闭合
- 字符串使用双引号
- 数字不使用引号
- 空数组使用[]，不要省略
- 空字符串使用""，不要省略
- 布尔值使用true/false，不要使用字符串
- 评分理由(reasoning)必须具体，引用会话中的实际内容
- 证据(evidence)必须引用原文片段，至少1条
- 引用规则(referenced_rules)必须填写实际rule_id或空数组[]
- 总分(total_score)必须是四个维度分数之和（4-20分）
- 风险等级(risk_level)必须与总分对应
- 亮点(strengths)和问题(issues)至少各1条
- 改进建议(suggestions)至少1条

### 评分流程检查清单
1. ✅ 已提取会话主题（20-30字）
2. ✅ 已识别用户意图（咨询/投诉/售后/购买）
3. ✅ 已判断用户情绪（满意/一般/不满/愤怒）
4. ✅ 已标记关键博弈轮次（价格谈判、故障处理、投诉升级）
5. ✅ 已逐维度评分（1-5分，基于具体证据）
6. ✅ 已提供具体评分理由（引用原文）
7. ✅ 已引用证据片段（至少1条/维度）
8. ✅ 已填写规则引用（rule_id或[]）
9. ✅ 已计算总分（4-20分）
10. ✅ 已确定风险等级（🔴高风险≤8 🟡中风险9-12 🟢正常≥13）
11. ✅ 已列出亮点（客服优秀表现）
12. ✅ 已列出问题（客服不足之处）
13. ✅ 已提供改进建议（具体可执行）

### 风险等级判定标准
- 🔴 高风险：总分 ≤ 8分（平均分≤2分）
  - 特征：存在严重错误、态度恶劣、政策错误、客户流失
  - 需立即整改，纳入重点监控
- 🟡 中风险：总分 9-12分（平均分2.25-3分）
  - 特征：基本合格但存在明显问题
  - 需针对性培训，提升服务质量
- 🟢 正常：总分 ≥ 13分（平均分≥3.25分）
  - 特征：服务合格，偶有小瑕疵
  - 保持现状，持续优化

### 评分注意事项
- 评分必须基于会话实际内容，不要臆测或脑补
- 评分理由必须引用具体对话片段作为证据
- 如果知识库规则未覆盖，referenced_rules必须填[]，严禁编造
- 不要为凑数而编造规则引用
- 总分必须是四个维度分数之和（4-20分）
- 风险等级必须与总分对应（≤8高风险，9-12中风险，≥13正常）
- 亮点和问题必须具体，不要泛泛而谈
- 改进建议必须可执行，不要空洞

### 常见错误避免
- 不要输出Markdown代码块标记（```json）
- 不要输出解释性文字（"以下是评分结果："）
- 不要遗漏任何必填字段
- 不要编造不存在的规则ID
- 评分必须在1-5的整数范围内
- 不要给所有维度相同分数（除非确实如此）
- 不要忽略明显的服务问题
- 不要过度美化客服表现

### 行业特定评分指南

**家电行业客服特殊要求**：
1. **产品知识深度**：必须掌握核心参数（功率、容量、尺寸、能效等级）
2. **安装条件确认**：必须询问安装环境（燃气类型、水压、空间尺寸）
3. **安全规范提醒**：必须提及安全注意事项（通风、接地、定期维护）
4. **竞品对比能力**：能客观对比竞品优缺点，不贬低对手
5. **售后流程熟悉**：能清晰说明报修、安装、退换货流程

**常见问题类型及评分重点**：

*咨询类*：
- 专业性：参数准确性、型号匹配度
- 转化能力：需求挖掘、方案推荐

*投诉类*：
- 标准化：情绪安抚、响应速度
- 政策执行：退换货政策、补偿方案

*售后类*：
- 专业性：故障诊断准确性
- 政策执行：保修政策说明、维修流程

*价格谈判类*：
- 转化能力：价格异议处理、优惠组合
- 政策执行：促销规则说明

**评分常见陷阱**：
1. 不要只看客服说了什么，要看用户问题是否解决
2. 不要忽略用户的负面情绪信号（"算了"、"我再看看"）
3. 不要过度关注礼貌用语而忽视实质帮助
4. 不要遗漏隐性转化机会（用户问"怎么买"是强烈信号）
5. 不要忽视政策错误，即使客服态度再好

**高质量评分特征**：
- 每个维度都有具体证据支撑
- 评分理由引用原文，不是概括
- 能识别客服的隐性优秀表现（如主动提醒）
- 能发现看似合格实则有问题的情况
- 改进建议具体可执行，不是泛泛而谈

### 评分质量自检
完成评分后，请检查：
1. 如果我是客服主管，这个评分能帮助我改进服务吗？
2. 评分理由是否足够具体，能让客服知道哪里做得好/不好？
3. 证据片段是否准确引用，没有断章取义？
4. 总分是否与四个维度分数一致？
5. 风险等级是否与总分匹配？
6. 是否遗漏了客服的优秀表现或明显问题？
7. 改进建议是否具体可执行？

### 模型输出自检
生成JSON前，请确认：
1. 所有字符串都用双引号包裹
2. 所有数字都没有引号
3. 数组和对象都正确闭合
4. 没有尾随逗号
5. 没有未转义的特殊字符
6. JSON可以正常解析
7. 所有必填字段都已填写
8. 分数在1-5的范围内
9. 总分是四个维度之和
10. 风险等级与总分匹配

### 最终确认
- 本次评分基于实际会话内容
- 评分客观公正，不偏袒客服
- 证据准确，理由充分
- 输出格式正确，可直接解析

### 完整评分示例参考

**示例：热水器安装咨询会话**

会话内容：
用户："这款热水器能装吗？"
客服："您好，可以安装的。您家是什么类型的燃气？"
用户："天然气"
客服："好的，这款支持天然气。您家厨房到卫生间距离多远？"
用户："大概8米"
客服："8米距离没问题，这款出水量稳定。需要预留冷热水管和燃气管，建议管径4分管。安装费200元，保修3年。您确定型号的话我可以帮您预约安装。"

正确评分输出：
```json
{
  "session_analysis": {
    "theme": "用户咨询热水器安装条件",
    "user_intent": "确认产品是否适合自家安装",
    "user_sentiment": "中性",
    "key_moments": ["客服询问燃气类型", "客服询问安装距离", "客服主动提供安装方案"]
  },
  "dimension_scores": {
    "professionalism": {
      "score": 4,
      "reasoning": "客服准确询问燃气类型和安装距离，提供管径建议（4分管），但缺少对水压要求的确认",
      "evidence": ["您家是什么类型的燃气？", "建议管径4分管"],
      "referenced_rules": []
    },
    "standardization": {
      "score": 4,
      "reasoning": "使用礼貌用语，响应及时，主动确认安装条件，但结束语不够完整",
      "evidence": ["您好，可以安装的", "您确定型号的话我可以帮您预约安装"],
      "referenced_rules": []
    },
    "policy_execution": {
      "score": 4,
      "reasoning": "主动告知安装费（200元）和保修期（3年），但缺少退换货政策说明",
      "evidence": ["安装费200元，保修3年"],
      "referenced_rules": []
    },
    "conversion": {
      "score": 4,
      "reasoning": "主动询问安装条件，引导预约安装，但缺少价格优惠说明和型号推荐",
      "evidence": ["您确定型号的话我可以帮您预约安装"],
      "referenced_rules": []
    }
  },
  "summary": {
    "total_score": 16,
    "risk_level": "正常",
    "strengths": ["主动确认安装条件", "提供技术参数建议", "告知费用和保修"],
    "issues": ["缺少水压确认", "结束语不够完整", "缺少优惠政策说明"],
    "suggestions": ["增加水压询问", "完善结束语", "主动提及当前优惠活动"]
  }
}
```

---
以下是本次评分的具体内容：
"""


SCORING_PROMPT_TEMPLATE = """## 本次评分参考规则

{retrieved_rules}

## 会话内容

```json
{session_data}
```

请按上述格式输出评分JSON。"""


BATCH_SCORING_PROMPT_TEMPLATE = """你是一位专业的客服质检专家，请对以下{count}个客服会话分别进行4维度质量评分。严格按JSON格式输出评分结果。输出必须是合法JSON数组，不包含任何解释性文字。

## 评分维度（每个会话单独评分）
1. **专业性** - 产品知识准确性
2. **标准化** - 服务规范
3. **政策执行** - 政策传达
4. **转化能力** - 销售引导

## 评分规则（适用于所有会话）

{retrieved_rules}

## 会话内容

{sessions_content}

## 输出格式（极其重要）

你必须严格返回JSON数组，数组长度必须严格等于{count}。每个数组元素对应一个会话的评分结果。

### 正确格式示例（3个会话）：
```json
[
  {{
    "session_analysis": {{
      "theme": "会话1主题",
      "user_intent": "意图1",
      "user_sentiment": "情绪1",
      "key_moments": ["关键1"]
    }},
    "dimension_scores": {{
      "professionalism": {{"score": 3, "reasoning": "...", "evidence": [], "referenced_rules": []}},
      "standardization": {{"score": 3, "reasoning": "...", "evidence": [], "referenced_rules": []}},
      "policy_execution": {{"score": 3, "reasoning": "...", "evidence": [], "referenced_rules": []}},
      "conversion": {{"score": 3, "reasoning": "...", "evidence": [], "referenced_rules": []}}
    }},
    "summary": {{
      "total_score": 12,
      "risk_level": "中风险",
      "strengths": ["亮点1"],
      "issues": ["问题1"],
      "suggestions": ["建议1"]
    }}
  }},
  {{
    "session_analysis": {{
      "theme": "会话2主题",
      "user_intent": "意图2",
      "user_sentiment": "情绪2",
      "key_moments": ["关键2"]
    }},
    "dimension_scores": {{
      "professionalism": {{"score": 4, "reasoning": "...", "evidence": [], "referenced_rules": []}},
      "standardization": {{"score": 4, "reasoning": "...", "evidence": [], "referenced_rules": []}},
      "policy_execution": {{"score": 3, "reasoning": "...", "evidence": [], "referenced_rules": []}},
      "conversion": {{"score": 4, "reasoning": "...", "evidence": [], "referenced_rules": []}}
    }},
    "summary": {{
      "total_score": 15,
      "risk_level": "正常",
      "strengths": ["亮点2"],
      "issues": ["问题2"],
      "suggestions": ["建议2"]
    }}
  }},
  {{
    "session_analysis": {{
      "theme": "会话3主题",
      "user_intent": "意图3",
      "user_sentiment": "情绪3",
      "key_moments": ["关键3"]
    }},
    "dimension_scores": {{
      "professionalism": {{"score": 2, "reasoning": "...", "evidence": [], "referenced_rules": []}},
      "standardization": {{"score": 2, "reasoning": "...", "evidence": [], "referenced_rules": []}},
      "policy_execution": {{"score": 3, "reasoning": "...", "evidence": [], "referenced_rules": []}},
      "conversion": {{"score": 2, "reasoning": "...", "evidence": [], "referenced_rules": []}}
    }},
    "summary": {{
      "total_score": 9,
      "risk_level": "中风险",
      "strengths": ["亮点3"],
      "issues": ["问题3"],
      "suggestions": ["建议3"]
    }}
  }}
]
```

### 格式要求（必须严格遵守）：
1. **必须返回JSON数组** - 以 `[` 开头，`]` 结尾
2. **数组长度必须等于{count}** - 当前有{count}个会话，必须返回{count}个评分结果
3. **每个元素是独立对象** - 每个会话一个对象，包含完整的session_analysis、dimension_scores、summary
4. **严禁合并结果** - 不要把多个会话的评分合并成1个对象
5. **数组元素顺序** - 与输入会话顺序一致
6. **直接输出JSON数组** - 不要Markdown代码块，不要解释性文字

### 常见错误（会导致评分失败）：
❌ 错误：返回单个对象 `{{...}}` 而不是数组 `[{{...}}, {{...}}]`
❌ 错误：把多个会话合并成1个对象
❌ 错误：数组长度不等于{count}
❌ 错误：缺少session_analysis或dimension_scores字段"""

GENERIC_RULES_FALLBACK = """（当前知识库没有可用的已审批专项规则，请严格按以下通用标准评判）

1. 专业性
- 回答是否准确、直接、没有事实性错误
- 面对安装/规格/售后问题时，是否给出清晰可执行的信息

2. 标准化
- 是否有基本礼貌与服务意识
- 是否围绕用户问题作答，避免答非所问或机械重复

3. 政策执行
- 涉及活动、发货、安装、售后时，是否避免编造政策
- 信息不确定时应明确说明，而不是主观臆断

4. 转化能力
- 是否主动澄清需求、补充关键信息
- 在不误导用户的前提下推进成交或下一步动作

如果会话很短、包含链接、或信息不足，也必须输出结构完整的JSON；证据数组和建议数组可以为空，但不能缺字段。"""

SCENE_ALIASES = {
    "售前": "售前阶段",
    "售前咨询": "售前阶段",
    "活动咨询": "售前阶段",
    "售中": "售中阶段",
    "售后": "售后阶段",
    "安装咨询": "售后阶段",
    "售后维修": "售后阶段",
    "客诉": "客诉处理",
    "投诉处理": "客诉处理",
    "其他": "其他",
}


# ========== 核心评分类 ==========

class SmartScoringEngine:
    """智能评分引擎 v2.4 - 支持异步批量评分"""
    
    def __init__(self, api_key: str = None, embedding_model=None, use_local_intent: bool = True, base_url: str = None, model: str = None):
        """
        Args:
            api_key: Moonshot API Key
            embedding_model: 向量模型（可选，默认使用全局单例）
            use_local_intent: 是否使用本地意图分类（默认True）
            base_url: API base URL（可选，默认Moonshot）
            model: 模型名称（可选，默认从环境变量读取）
        """
        self.api_key = api_key or os.getenv("MOONSHOT_API_KEY")
        self.base_url = base_url or "https://api.moonshot.cn/v1"
        self.model = model or os.getenv('KIMI_MODEL', 'kimi-k2.5')
        # 使用传入的模型或全局单例
        self.embedding_model = embedding_model or get_embedding_model()
        self.use_local_intent = use_local_intent
        
        # 初始化漏斗式意图分类器
        self.intent_classifier = None
        if use_local_intent:
            try:
                from intent_classifier_v3 import RobustIntentClassifier
                self.intent_classifier = RobustIntentClassifier()
                print("✅ 场景/意图/情绪识别已移至LLM评分时（qwen2.5已弃用）")
            except Exception as e:
                print(f"⚠️ 漏斗式意图分类器初始化失败: {e}，将使用关键词规则")
        
    def _analyze_session_pre(self, messages: List[Dict]) -> Dict:
        """会话预分析 - 漏斗式分类
        
        第一层：毫秒级规则匹配（高频简单意图）
        第二层：本地Qwen3:4b（复杂/长尾意图）
        第三层：关键词规则回退
        
        Args:
            messages: 消息列表
            
        Returns:
            预分析结果
        """
        # 尝试使用漏斗式分类器
        if self.intent_classifier:
            try:
                result = self.intent_classifier.classify(messages)
                if result:
                    return {
                        "scene": result.scene,
                        "sub_scene": result.sub_scene,
                        "intent": result.intent,
                        "sentiment": result.sentiment,
                        "confidence": result.confidence,
                        "reasoning": result.reasoning,
                        "source": result.source,
                        "latency_ms": result.latency_ms
                    }
            except Exception as e:
                print(f"⚠️ 漏斗式分类失败: {e}，回退到关键词规则")
        
        # 回退：关键词规则匹配
        return self._analyze_session_keyword_fallback(messages)
    
    def _compact_session_for_prompt(self, session_data: dict) -> str:
        """【优化C】将会话数据精简为纯文本格式，去掉元数据
        
        原格式：JSON 包含 session_id, timestamp, sender 等元数据
        新格式：[角色] 内容
        
        预计节省：输入 token 减少 30-40%
        """
        messages = session_data.get('messages', [])
        lines = []
        for m in messages:
            role = m.get('role', 'unknown')
            content = self._sanitize_prompt_content(m.get('content', ''))
            if content:
                if role in ('user', 'customer'):
                    lines.append(f"[用户] {content}")
                elif role == 'staff':
                    lines.append(f"[客服] {content}")
                else:
                    lines.append(f"[{role}] {content}")
        return '\n'.join(lines)

    def _sanitize_prompt_content(self, content: str) -> str:
        """清理提示词中的高噪声内容，降低链接/媒体文本对本地模型的干扰"""
        if not content:
            return ""

        text = content.strip()
        text = re.sub(r'https?://\S+', '[URL]', text)
        text = re.sub(r'www\.\S+', '[URL]', text)
        text = re.sub(r'\s+', ' ', text)
        return text

    def _normalize_scene_name(self, scene: Optional[str]) -> str:
        """统一场景命名，避免不同入口的场景值漂移"""
        if not scene:
            return "其他"
        normalized = SCENE_ALIASES.get(scene, scene)
        return normalized.strip() if isinstance(normalized, str) else "其他"
    
    def _analyze_session_keyword_fallback(self, messages: List[Dict]) -> Dict:
        """关键词规则匹配（回退方案）
        
        Args:
            messages: 消息列表
            
        Returns:
            预分析结果
        """
        user_messages = [m['content'] for m in messages if m.get('role') == 'user']
        all_text = ' '.join(user_messages)
        
        # 场景识别
        scene_keywords = {
            "售前咨询": ["多少钱", "价格", "优惠", "活动", "有没有", "推荐"],
            "安装咨询": ["安装", "尺寸", "预留", "辅材", "怎么装"],
            "客诉处理": ["骗子", "投诉", "退货", "退款", "不满意", "质量差"],
            "售后维修": ["坏了", "故障", "维修", "保修", "售后"],
            "活动咨询": ["国补", "补贴", "赠品", "保价", "活动规则"]
        }
        
        scene_scores = {}
        for scene, keywords in scene_keywords.items():
            score = sum(1 for kw in keywords if kw in all_text)
            scene_scores[scene] = score
        
        detected_scene = max(scene_scores, key=scene_scores.get) if max(scene_scores.values()) > 0 else "其他"
        detected_scene = self._normalize_scene_name(detected_scene)
        
        # 情绪识别
        negative_words = ["骗子", "垃圾", "投诉", "退钱", "欺诈", "糊弄", "愤怒", "生气"]
        urgent_words = ["马上", "立刻", "赶紧", "急", "催"]
        
        negative_count = sum(1 for w in negative_words if w in all_text)
        urgent_count = sum(1 for w in urgent_words if w in all_text)
        
        if negative_count >= 2:
            sentiment = "negative"
        elif urgent_count >= 2:
            sentiment = "urgent"
        elif negative_count == 1:
            sentiment = "neutral"
        else:
            sentiment = "positive"
        
        # 意图识别
        intent_keywords = {
            "咨询": ["多少钱", "怎么样", "有什么", "推荐"],
            "客诉": ["骗子", "投诉", "欺骗"],
            "退款": ["退货", "退款", "不要了"],
            "维修": ["坏了", "故障", "维修"],
            "安装": ["安装", "尺寸", "预留"]
        }
        
        intent_scores = {}
        for intent, keywords in intent_keywords.items():
            score = sum(1 for kw in keywords if kw in all_text)
            intent_scores[intent] = score
        
        detected_intent = max(intent_scores, key=intent_scores.get) if max(intent_scores.values()) > 0 else "其他"
        
        return {
            "scene": detected_scene,
            "sub_scene": "其他",
            "intent": detected_intent,
            "sentiment": sentiment,
            "confidence": 0.5,
            "reasoning": "基于关键词规则匹配",
            "source": "keyword"  # 标记来源
        }
    
    def _retrieve_rules(self, session_analysis: Dict, messages_text: str) -> List[Dict]:
        """检索相关规则（使用混合检索）
        
        Args:
            session_analysis: 会话预分析结果
            messages_text: 会话文本（用于向量检索）
            
        Returns:
            相关规则列表
        """
        # 优先使用混合检索
        normalized_scene = self._normalize_scene_name(session_analysis.get('scene'))

        if HYBRID_RETRIEVER_AVAILABLE:
            try:
                retriever = HybridRuleRetriever(embedding_model=self.embedding_model)
                rules = retriever.search(
                    query=messages_text,
                    scene_filter=normalized_scene,
                    top_k=5,
                    use_hybrid=True
                )
                if rules:
                    print(f"📚 混合检索返回 {len(rules)} 条规则")
                    return rules
            except Exception as e:
                print(f"⚠️ 混合检索失败: {e}，回退到基础检索")
        
        # 回退：基础检索（元数据过滤 + 向量检索）
        rules = []
        
        # 1. 基于元数据过滤获取规则
        scene_rules = get_approved_rules(
            scene_category=normalized_scene
        )
        rules.extend(scene_rules)
        
        # 2. 向量检索补充
        try:
            vector_rules = search_rules_by_vector(
                query_text=messages_text,
                top_k=3,
                scene_filter=normalized_scene,
                embedding_model=self.embedding_model
            )
            
            # 合并去重
            existing_ids = {r['rule_id'] for r in rules}
            for vr in vector_rules:
                if vr['rule_id'] not in existing_ids:
                    rules.append(vr)
        except Exception as e:
            print(f"向量检索失败: {e}")
        
        return rules[:5]  # 最多返回5条规则
    
    def _format_rules_for_prompt(self, rules: List[Dict]) -> str:
        """将规则格式化为Prompt文本
        
        Args:
            rules: 规则列表
            
        Returns:
            格式化后的规则文本
        """
        if not rules:
            return GENERIC_RULES_FALLBACK
        
        formatted = []
        for i, rule in enumerate(rules, 1):
            rule_text = f"""
### 规则{i}: {rule.get('rule_id', 'N/A')}
- **适用场景**: {rule.get('scene_category', 'N/A')} / {rule.get('scene_sub_category', 'N/A')}
- **触发条件**: {', '.join(rule.get('trigger_keywords', []))}
- **评分维度**: {rule.get('rule_dimension', 'N/A')}
- **核心判定**: {rule.get('rule_criteria', 'N/A')}

**5分标准**: {rule.get('rule_score_guide', {}).get('5', {}).get('description', 'N/A')}
- Checkpoints: {', '.join(rule.get('rule_score_guide', {}).get('5', {}).get('checkpoints', []))}

**3分标准**: {rule.get('rule_score_guide', {}).get('3', {}).get('description', 'N/A')}
- Checkpoints: {', '.join(rule.get('rule_score_guide', {}).get('3', {}).get('checkpoints', []))}

**1分标准**: {rule.get('rule_score_guide', {}).get('1', {}).get('description', 'N/A')}
- Checkpoints: {', '.join(rule.get('rule_score_guide', {}).get('1', {}).get('checkpoints', []))}
"""
            formatted.append(rule_text)
        
        return "\n---\n".join(formatted)
    
    # ========== 单通评分（保留兼容） ==========
    
    def score_session(self, session_data: Dict) -> Dict:
        """对会话进行智能评分
        
        Args:
            session_data: 会话数据，包含messages等
            
        Returns:
            评分结果JSON
            
        Raises:
            ScoringError: 当AI评分失败时抛出，包含具体失败原因
        """
        messages = session_data.get('messages', [])
        
        # 1. 会话预分析
        pre_analysis = self._analyze_session_pre(messages)
        print(f"📊 预分析: {pre_analysis}")
        
        # 2. 规则检索
        messages_text = '\n'.join([f"{m.get('role')}: {m.get('content')}" for m in messages[:10]])
        retrieved_rules = self._retrieve_rules(pre_analysis, messages_text)
        print(f"📚 检索到 {len(retrieved_rules)} 条规则")
        
        # 3. 构建Prompt（方案A：加入FIXED_USER_PREFIX凑满4096 tokens）
        rules_text = self._format_rules_for_prompt(retrieved_rules)
        prompt = FIXED_USER_PREFIX + "\n" + SCORING_PROMPT_TEMPLATE.format(
            retrieved_rules=rules_text,
            session_data=json.dumps(session_data, ensure_ascii=False, indent=2)
        )
        
        # 【DEBUG】计算并打印前4096字符的hash
        import hashlib
        full_prompt_for_hash = SYSTEM_PROMPT_V2 + "\n" + prompt
        prefix_4096 = full_prompt_for_hash[:4096]
        prefix_hash = hashlib.md5(prefix_4096.encode()).hexdigest()[:12]
        print(f"   🔍 DEBUG_PREFIX|hash={prefix_hash}|system_len={len(SYSTEM_PROMPT_V2)}|fixed_prefix_len={len(FIXED_USER_PREFIX)}|total_fixed={len(SYSTEM_PROMPT_V2)+len(FIXED_USER_PREFIX)}|prompt_total={len(prompt)}|full_prompt={len(full_prompt_for_hash)}")
        
        # 【调试】打印prompt长度信息
        print(f"   📏 DEBUG|system_prompt_chars={len(SYSTEM_PROMPT_V2)}|fixed_prefix_chars={len(FIXED_USER_PREFIX)}|total_fixed_chars={len(SYSTEM_PROMPT_V2)+len(FIXED_USER_PREFIX)}|prompt_total_chars={len(prompt)}")
        
        # 4. 调用Kimi API
        try:
            import openai
            import time
            
            client = openai.OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                max_retries=2
            )
            
            model = self.model
            
            max_retries = 3
            base_delay = 2.0
            last_exception = None
            
            for attempt in range(max_retries):
                try:
                    # 【v2.5】添加120秒超时保护，防止Worker僵死
                    response = client.chat.completions.create(
                        model=model,
                        messages=[
                            {"role": "system", "content": SYSTEM_PROMPT_V2},
                            {"role": "user", "content": prompt}
                        ],
                        temperature=LLM_CONFIG.get("temperature", 0.1),
                        max_tokens=int(os.getenv('KIMI_MAX_TOKENS', 16000)),
                        timeout=int(os.getenv('KIMI_API_TIMEOUT', 300))  # 【v2.5】从.env读取超时时间（默认300秒）
                    )
                    break
                    
                except Exception as e:
                    last_exception = e
                    error_msg = str(e)
                    
                    if "429" in error_msg or "Too Many Requests" in error_msg:
                        if attempt < max_retries - 1:
                            delay = base_delay * (2 ** attempt)
                            print(f"   ⏳ API限流(429)，等待 {delay:.1f}s 后重试 (第{attempt+1}/{max_retries}次)...")
                            time.sleep(delay)
                            continue
                        else:
                            print(f"   ⚠️ 限流重试耗尽，最后一次错误: {error_msg[:100]}")
                            raise
                    else:
                        raise
            else:
                raise last_exception if last_exception else Exception("API调用失败")
            
            content = response.choices[0].message.content
            
            result = self._parse_json_robust(content)
            
            # 【修复】截断各维度分数到有效范围
            if result:
                result = self._clamp_scores(result)
            
            if result is None:
                raise ScoringError(
                    message="JSON解析失败: 无法解析AI返回的内容",
                    error_type="json_parse_error",
                    details={"content_preview": content[:1000]}
                )
            
            result['_metadata'] = {
                'scored_at': datetime.now().isoformat(),
                'retrieved_rules': [r['rule_id'] for r in retrieved_rules],
                'pre_analysis': pre_analysis,
                'model': model,
            }
            
            return result
                
        except ScoringError:
            raise
        except Exception as e:
            raise ScoringError(
                message=f"评分失败: {str(e)}",
                error_type="api_error",
                details={"original_error": str(e)}
            )
    
    # ========== 批量评分方法（v2.4新增） ==========
    
    async def score_sessions_batch_async(self, sessions: List[Dict], pre_analyses: List[Dict] = None) -> List[Dict]:
        """【v2.6.1】异步批量评分 - 跨场景合并优化
        
        核心变更：
        - 不再按场景分组，所有会话统一批量处理
        - 场景信息通过pre_analysis传入，由模型自行处理
        - 检索规则时使用混合策略（覆盖所有场景）
        
        Args:
            sessions: 会话数据列表（20-40通）
            pre_analyses: 预分析结果列表（包含场景信息）
            
        Returns:
            评分结果列表（与输入顺序一致）
        """
        print(f"[DEBUG] score_sessions_batch_async START - {len(sessions)} sessions", flush=True)
        
        if not sessions:
            print(f"[DEBUG] No sessions, returning empty list", flush=True)
            return []
        
        # 如果未提供预分析结果，快速分析
        if pre_analyses is None:
            print(f"[DEBUG] Running pre-analysis for {len(sessions)} sessions...", flush=True)
            loop = asyncio.get_event_loop()
            pre_analyses = await asyncio.gather(*[
                loop.run_in_executor(None, self._analyze_session_pre, s.get('messages', []))
                for s in sessions
            ])
            print(f"[DEBUG] Pre-analysis completed", flush=True)
        
        # 【v2.6.1】跨场景统一评分（不再分组）
        print(f"[DEBUG] Processing {len(sessions)} sessions cross-scene", flush=True)
        results = await self._score_batch_cross_scene(sessions, pre_analyses)
        
        print(f"[DEBUG] score_sessions_batch_async END - returning {len([r for r in results if r is not None])} results", flush=True)
        return results
    
    async def _score_batch_cross_scene(self, sessions: List[Dict], 
                                          pre_analyses: List[Dict]) -> List[Dict]:
        """【v2.6.1】跨场景批量评分
        
        不再限制同一场景，支持混合场景的批量评分
        场景信息直接标注在每个会话前，由模型自行处理
        """
        if not sessions:
            return []
        
        # 收集所有场景
        scenes = list(set(self._normalize_scene_name(p.get('scene', '其他')) for p in pre_analyses))
        print(f"   📚 跨场景评分: {len(sessions)}通会话, 场景: {scenes}")
        
        # 检索所有相关场景的规则（混合检索）
        messages_text = '\n'.join([
            f"会话{i+1}({self._normalize_scene_name(p.get('scene', '其他'))}): " + '\n'.join([
                f"{m.get('role')}: {self._sanitize_prompt_content(m.get('content', ''))}" 
                for m in session.get('messages', [])[:3]
            ])
            for i, (session, p) in enumerate(zip(sessions, pre_analyses))
        ])
        
        # 检索规则：不限制场景，获取最相关的规则
        retrieved_rules = self._retrieve_rules_cross_scene(scenes, messages_text)
        rules_text = self._format_rules_for_prompt(retrieved_rules)
        
        # 构建跨场景批量Prompt（标注场景信息）
        sessions_json = '\n\n'.join([
               f"=== 会话{i+1} [场景: {self._normalize_scene_name(pre_analyses[i].get('scene', '其他'))}] ===\n{self._compact_session_for_prompt(s)}"
            for i, s in enumerate(sessions)
        ])
        
        # 【方案A】加入FIXED_USER_PREFIX凑满4096 tokens
        prompt = FIXED_USER_PREFIX + "\n" + BATCH_SCORING_PROMPT_TEMPLATE.format(
            count=len(sessions),
            retrieved_rules=rules_text,
            sessions_content=sessions_json
        )
        
        # 【DEBUG】计算并打印前4096字符的hash
        import hashlib
        full_prompt_for_hash = SYSTEM_PROMPT_V2 + "\n" + prompt
        prefix_4096 = full_prompt_for_hash[:4096]
        prefix_hash = hashlib.md5(prefix_4096.encode()).hexdigest()[:12]
        print(f"   🔍 DEBUG_PREFIX_BATCH|hash={prefix_hash}|system_len={len(SYSTEM_PROMPT_V2)}|fixed_prefix_len={len(FIXED_USER_PREFIX)}|total_fixed={len(SYSTEM_PROMPT_V2)+len(FIXED_USER_PREFIX)}|prompt_total={len(prompt)}|full_prompt={len(full_prompt_for_hash)}")
        
        # === Prompt 结构追踪 START ===
        try:
            prompt_parts = {
                "rules_chars": len(rules_text),
                "sessions_chars": len(sessions_json),
                "template_chars": len(prompt) - len(rules_text) - len(sessions_json),
                "total_prompt_chars": len(prompt),
                "session_count": len(sessions),
                "session_avg_chars": len(sessions_json) // max(len(sessions), 1)
            }
            print(f"   📐 PROMPT_STRUCT|{json.dumps(prompt_parts, ensure_ascii=False)}", flush=True)
        except Exception as e:
            print(f"   ⚠️ PROMPT_STRUCT logging failed: {e}", flush=True)
        # === Prompt 结构追踪 END ===
        
        # 调用API
        result = await self._call_llm_async(prompt, len(sessions), pre_analyses)
        return result
    
    def _retrieve_rules_cross_scene(self, scenes: List[str], messages_text: str) -> List[Dict]:
        """【方案B】按场景分别检索规则
        
        策略变更：
        1. 移除混合检索（混合场景下失效）
        2. 为每个场景分别检索规则（售前→售前规则，售中→售中规则...）
        3. 合并去重后返回
        
        这样即使20通混合批次，也能获取所有场景的规则。
        """
        all_rules = []
        seen_ids = set()
        
        # 去重场景列表
        unique_scenes = list(set(self._normalize_scene_name(scene) for scene in scenes))
        print(f"   📚 为 {len(unique_scenes)} 个场景分别检索规则: {unique_scenes}")
        
        # 为每个场景分别检索规则
        for scene in unique_scenes:
            try:
                # 1. 从知识库获取该场景的已批准规则
                scene_rules = get_approved_rules(scene_category=scene)
                for r in scene_rules:
                    if r['rule_id'] not in seen_ids:
                        all_rules.append(r)
                        seen_ids.add(r['rule_id'])
                
                if scene_rules:
                    print(f"     ✓ 场景'{scene}': {len(scene_rules)}条规则")
                
                # 2. 向量检索补充（带场景过滤）
                try:
                    # 构建该场景的查询文本
                    scene_query = f"{scene} 客服服务标准"
                    vector_rules = search_rules_by_vector(
                        query_text=scene_query,
                        top_k=3,
                        embedding_model=self.embedding_model
                    )
                    # 过滤：只保留匹配当前场景的规则
                    for vr in vector_rules:
                        if vr['rule_id'] not in seen_ids and vr.get('scene_category') == scene:
                            all_rules.append(vr)
                            seen_ids.add(vr['rule_id'])
                except Exception as e:
                    print(f"     ⚠️ 场景'{scene}'向量检索失败: {e}")
                    
            except Exception as e:
                print(f"   ⚠️ 检索场景'{scene}'规则失败: {e}")
        
        print(f"   📚 跨场景检索共 {len(all_rules)} 条规则（来自{len(unique_scenes)}个场景）")
        return all_rules[:10]  # 最多10条，避免Prompt过长
    
    async def _score_batch_same_scene(self, sessions: List[Dict], 
                                       pre_analyses: List[Dict],
                                       scene: str) -> List[Dict]:
        """对同一场景的会话进行批量评分（保留兼容）"""
        if not sessions:
            return []
        
        # 统一检索规则（同一场景用相同规则）
        messages_text = '\n'.join([
            f"会话{i+1}: " + '\n'.join([
                f"{m.get('role')}: {m.get('content')}" 
                for m in session.get('messages', [])[:5]
            ])
            for i, session in enumerate(sessions)
        ])
        
        retrieved_rules = self._retrieve_rules({'scene': scene}, messages_text)
        rules_text = self._format_rules_for_prompt(retrieved_rules)
        
        # 构建批量Prompt
        sessions_json = '\n\n'.join([
            f"=== 会话{i+1} ===\n{json.dumps(s, ensure_ascii=False, indent=2)}"
            for i, s in enumerate(sessions)
        ])
        
        prompt = BATCH_SCORING_PROMPT_TEMPLATE.format(
            count=len(sessions),
            retrieved_rules=rules_text,
            sessions_content=sessions_json
        )
        
        # 调用API（异步）
        result = await self._call_llm_async(prompt, len(sessions), pre_analyses)
        return result
    
    async def _call_llm_async(self, prompt: str, expected_count: int, pre_analyses: List[Dict] = None) -> List[Dict]:
        """异步调用Kimi API（带httpx精细超时控制）"""
        import logging
        logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(message)s')
        logger = logging.getLogger(__name__)
        
        logger.info(f"[DEBUG] _call_llm_async START - expected_count={expected_count}")
        print(f"   [DEBUG] === _call_llm_async START ===", flush=True)
        print(f"   [DEBUG] expected_count={expected_count}", flush=True)
        
        try:
            import openai
            import asyncio
            import httpx
            
            timeout_seconds = int(os.getenv('KIMI_API_TIMEOUT', '300'))  # 【v2.5】从环境变量读取，默认300秒
            import sys
            print(f"   [DEBUG] API Timeout: {timeout_seconds}s", file=sys.stderr, flush=True)
            print(f"   [DEBUG] Worker PID: {os.getpid()}", file=sys.stderr, flush=True)
            print(f"   [DEBUG] API Key exists: {bool(self.api_key)}", flush=True)
            
            client = openai.AsyncOpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                max_retries=2,
                timeout=httpx.Timeout(
                    connect=30.0,           # 连接超时30秒
                    read=timeout_seconds,   # 读取超时从.env读取
                    write=30.0,             # 写入超时30秒
                    pool=30.0               # 连接池超时30秒
                ),
                http_client=httpx.AsyncClient(
                    limits=httpx.Limits(
                        max_connections=100,  # 【优化】最大连接数100（原默认10）
                        max_keepalive_connections=50  # 【优化】保持活跃连接50
                    )
                )
            )
            
            model = self.model
            print(f"   [DEBUG] Using model: {model}", flush=True)
            
            # 【v2.5】添加超时保护（从.env读取，默认300秒）
            print(f"   [DEBUG] Calling API with timeout={timeout_seconds}s...", flush=True)
            start_time = datetime.now()
            
            async with asyncio.timeout(timeout_seconds):
                response = await client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT_V2},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=LLM_CONFIG.get("temperature", 0.1),
                    max_tokens=int(os.getenv('KIMI_MAX_TOKENS', 16000)),
                )
            
            elapsed = (datetime.now() - start_time).total_seconds()
            print(f"   [DEBUG] API call completed in {elapsed:.1f}s", flush=True)
            
            content = response.choices[0].message.content
            
            # === 成本追踪埋点 START ===
            try:
                usage = response.usage
                cost_log = {
                    "batch_idx": expected_count,
                    "prompt_tokens": usage.prompt_tokens,
                    "completion_tokens": usage.completion_tokens,
                    "total_tokens": usage.total_tokens,
                    "elapsed_seconds": elapsed,
                    "prompt_chars": len(prompt),
                    "model": model
                }
                print(f"   💰 TOKEN_COST|{json.dumps(cost_log, ensure_ascii=False)}", flush=True)
            except Exception as e:
                print(f"   ⚠️ TOKEN_COST logging failed: {e}", flush=True)
            # === 成本追踪埋点 END ===
            
            results = self._parse_batch_response(content, expected_count)
            print(f"   [DEBUG] Parsed {len(results)} results", flush=True)
            
            # 【修复】截断各维度分数到有效范围
            results = [self._clamp_scores(r) if isinstance(r, dict) else r for r in results]
            
            # 补充元数据（包含预分析数据）
            for i, r in enumerate(results):
                # 【Bug修复】确保r是字典，不是列表或其他类型
                if not isinstance(r, dict):
                    print(f"   ⚠️ 结果{i}类型错误: {type(r)}, 转换为错误字典", flush=True)
                    r = {"error": f"解析结果类型错误: {type(r)}", "_raw": str(r)[:200]}
                    results[i] = r
                # 确保基本字段存在
                if 'error' not in r:
                    r.setdefault('professionalism_score', 0)
                    r.setdefault('standardization_score', 0)
                    r.setdefault('policy_execution_score', 0)
                    r.setdefault('conversion_score', 0)
                if '_metadata' not in r:
                    r['_metadata'] = {}
                r['_metadata']['model'] = model
                r['_metadata']['scored_at'] = datetime.now().isoformat()
                # 添加预分析数据（从pre_analyses获取）
                if pre_analyses and i < len(pre_analyses):
                    r['_metadata']['pre_analysis'] = pre_analyses[i]
            
            print(f"   [DEBUG] === _call_llm_async END (success) ===", flush=True)
            return results
            
        except asyncio.TimeoutError:
            timeout_val = os.getenv('KIMI_API_TIMEOUT', '300')
            print(f"⚠️ 批量评分超时: Kimi API调用超过{timeout_val}秒", flush=True)
            print(f"   [DEBUG] === _call_llm_async END (timeout) ===", flush=True)
            return [{"error": f"API调用超时({timeout_val}s)"} for _ in range(expected_count)]
        except Exception as e:
            print(f"⚠️ 批量评分失败: {e}", flush=True)
            print(f"   [DEBUG] === _call_llm_async END (error) ===", flush=True)
            return [{"error": str(e)} for _ in range(expected_count)]
    
    def _parse_batch_response(self, content: str, expected_count: int) -> List[Dict]:
        """解析批量评分响应"""
        # 清理Markdown代码块
        cleaned = content
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        elif cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()
        
        def make_retry_marker(error_code: str) -> Dict:
            marker = {"error": error_code}
            if expected_count > 1:
                marker["_needs_single_retry"] = True
            if cleaned:
                marker["_raw"] = cleaned[:300]
            return marker

        try:
            results = json.loads(cleaned)
            
            if isinstance(results, dict):
                if expected_count == 1:
                    return [results]
                print(f"   [DEBUG] Model returned dict instead of list, marking batch for single retry")
                return [make_retry_marker("DICT_INSTEAD_OF_LIST") for _ in range(expected_count)]
            elif isinstance(results, list):
                if len(results) == expected_count:
                    return results
                elif len(results) < expected_count:
                    print(f"   [DEBUG] Batch result length mismatch: expected {expected_count}, got {len(results)}")
                    return results + [
                        make_retry_marker("ARRAY_LENGTH_MISMATCH")
                        for _ in range(expected_count - len(results))
                    ]
                else:
                    print(f"   [DEBUG] Batch result length overflow: expected {expected_count}, got {len(results)}")
                    return results[:expected_count]
        except json.JSONDecodeError as e:
            print(f"   [DEBUG] JSON decode error: {e}")
        
        # 尝试逐个解析JSON对象
        try:
            pattern = r'\{[^{}]*"session_analysis"[^{}]*(?:\{[^{}]*\}[^{}]*)*\}'
            matches = re.findall(pattern, cleaned, re.DOTALL)
            if len(matches) >= expected_count:
                return [json.loads(m) for m in matches[:expected_count]]
        except:
            pass
        
        # 回退：尝试用单条解析器
        single_result = self._parse_json_robust(cleaned)
        if single_result:
            if expected_count == 1:
                return [single_result]
            print(f"   [DEBUG] Recovered a single result from malformed batch output, marking batch for single retry")
            return [make_retry_marker("BATCH_RECOVERED_SINGLE_RESULT") for _ in range(expected_count)]
        
        return [make_retry_marker("JSON_DECODE_FAILED") for _ in range(expected_count)]
    
    # ========== 通用工具方法 ==========
    
    def _clamp_scores(self, result: Dict) -> Dict:
        """【修复】截断各维度分数到有效范围（1-5分）
        
        AI模型有时会给出超出1-5分范围的分数，需要截断
        """
        if not isinstance(result, dict):
            return result
        if 'error' in result or result.get('_needs_single_retry'):
            return result
        
        dims = ['professionalism', 'standardization', 'policy_execution', 'conversion']
        dim_scores = result.get('dimension_scores', {})
        
        for dim in dims:
            if dim in dim_scores and isinstance(dim_scores[dim], dict):
                score = dim_scores[dim].get('score', 3)
                # 截断到1-5分范围
                original_score = score
                clamped_score = max(1, min(5, score))
                if original_score != clamped_score:
                    print(f"   ⚠️ {dim}分数截断: {original_score} -> {clamped_score}")
                    dim_scores[dim]['score'] = clamped_score
                    # 添加截断标记
                    dim_scores[dim]['_clamped'] = True
                    dim_scores[dim]['_original_score'] = original_score
        
        # 重新计算总分
        total = sum(dim_scores.get(d, {}).get('score', 3) for d in dims)
        if 'summary' not in result:
            result['summary'] = {}
        result['summary']['total_score'] = total
        
        # 更新风险等级
        if total <= 8:
            result['summary']['risk_level'] = "高风险"
        elif total <= 12:
            result['summary']['risk_level'] = "中风险"
        else:
            result['summary']['risk_level'] = "正常"
        
        return result

    def _parse_json_robust(self, content: str) -> Optional[Dict]:
        """健壮JSON解析 - 处理截断和不完整JSON
        
        Args:
            content: 原始响应内容
            
        Returns:
            解析后的字典，或None
        """
        if not content:
            return None
        
        # 步骤1: 清理Markdown代码块
        cleaned = content
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        elif cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()
        
        # 步骤2: 尝试正常解析
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass
        
        # 步骤3: 尝试提取JSON对象（从第一个{到最后一个}）
        try:
            start = cleaned.find('{')
            end = cleaned.rfind('}')
            if start != -1 and end != -1 and end > start:
                extracted = cleaned[start:end+1]
                return json.loads(extracted)
        except json.JSONDecodeError:
            pass
        
        # 步骤4: 尝试修复截断的JSON
        try:
            fixed = self._fix_truncated_json(cleaned)
            if fixed:
                return json.loads(fixed)
        except json.JSONDecodeError:
            pass
        
        return None
    
    def _fix_truncated_json(self, content: str) -> Optional[str]:
        """尝试修复截断的JSON
        
        Args:
            content: 可能截断的JSON字符串
            
        Returns:
            修复后的JSON字符串，或None
        """
        if not content:
            return None
        
        fixed = content.strip()
        
        # 如果最后是不完整的字符串（在引号内截断）
        last_quote = fixed.rfind('"')
        if last_quote > 0:
            after_quote = fixed[last_quote+1:].strip()
            if after_quote and after_quote[0] not in [',', ':', '}', ']']:
                fixed = fixed + '"'
        
        # 统计开闭符号
        open_braces = fixed.count('{') - fixed.count('}')
        open_brackets = fixed.count('[') - fixed.count(']')
        
        # 补全缺失的闭括号
        fixed = fixed + ('}' * open_braces)
        fixed = fixed + (']' * open_brackets)
        
        # 如果最后是逗号，移除它
        if fixed.rstrip().endswith(','):
            fixed = fixed.rstrip()[:-1]
        
        return fixed


# ========== 便捷函数 ==========

def score_session_with_rules(session_data: Dict, api_key: str = None) -> Dict:
    """便捷函数：对会话进行规则增强评分
    
    Args:
        session_data: 会话数据
        api_key: Moonshot API Key
        
    Returns:
        评分结果
        
    Raises:
        ScoringError: 当AI评分失败时抛出
    """
    engine = SmartScoringEngine(api_key=api_key)
    return engine.score_session(session_data)


# ========== 测试 ==========

if __name__ == "__main__":
    # 测试数据
    test_session = {
        "session_id": "test_001",
        "messages": [
            {"role": "user", "content": "你们不是骗子吗？主播说的和客服说的不一样"},
            {"role": "staff", "content": "小主您可以以直播间主播的规则为准呢"},
            {"role": "user", "content": "那就是你们客服说的不算？"},
            {"role": "staff", "content": "小主您可以以直播间主播的规则为准呢"},
            {"role": "user", "content": "你只会重复这句话吗？"},
            {"role": "staff", "content": "小主您可以以直播间主播的规则为准呢"}
        ],
        "staff_name": "林内林小肖"
    }
    
    print("🧪 测试智能评分...")
    result = score_session_with_rules(test_session)
    
    if result:
        print("\n✅ 评分结果:")
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("\n❌ 评分失败")
