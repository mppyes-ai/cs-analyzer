# CS-Analyzer 会话合并核查报告

## 数据概览

| 类别 | 数量 |
|------|------|
| 源文件总会话 | 400通 |
| 前置过滤 | 41通 |
| 提交到队列 | 359通 |
| 合并取消 | 64通 |
| 实际完成 | 295通 |

---

## 被取消的64通会话详情

### 按消息数排序（Top 20）

| 排名 | Session ID | 消息数 | 状态 |
|------|-----------|--------|------|
| 1 | session_0015_8024c76c | 90 | cancelled (merged) |
| 2 | session_0311_38569af0 | 40 | cancelled (merged) |
| 3 | session_0186_00aa8a90 | 32 | cancelled (merged) |
| 4 | session_0181_9578bd37 | 28 | cancelled (merged) |
| 5 | session_0284_d5d7c6d1 | 27 | cancelled (merged) |
| 6 | session_0041_16e9abc8 | 24 | cancelled (merged) |
| 7 | session_0255_e995a0a8 | 23 | cancelled (merged) |
| 8 | session_0153_8024c76c | 20 | cancelled (merged) |
| 9 | session_0166_8024c76c | 20 | cancelled (merged) |
| 10 | session_0336_a7902d6b | 20 | cancelled (merged) |

### 完整64通列表

<details>
<summary>点击展开全部64通</summary>

1. session_0000_a92c2105 (merged)
2. session_0015_8024c76c (90 msgs, merged)
3. session_0021_8024c76c (15 msgs, merged)
4. session_0029_5bdbb5bb (merged)
5. session_0036_57b45287 (merged)
6. session_0039_542522f2 (merged)
7. session_0040_240c8d7f (merged)
8. session_0041_16e9abc8 (24 msgs, merged)
9. session_0046_90b41dc4 (merged)
10. session_0049_4f71c8b8 (merged)
11. session_0054_35413ada (merged)
12. session_0060_a92c2105 (merged)
13. session_0091_0f921bd6 (merged)
14. session_0096_960cb70a (merged)
15. session_0108_a1e84b6b (merged)
16. session_0113_ba189dc3 (13 msgs, merged)
17. session_0120_09d6c7b1 (merged)
18. session_0122_75cb50f0 (16 msgs, merged)
19. session_0136_72495336 (merged)
20. session_0137_a92c2105 (merged)
21. session_0153_8024c76c (20 msgs, merged)
22. session_0164_47557673 (merged)
23. session_0165_eb9b7555 (13 msgs, merged)
24. session_0166_8024c76c (20 msgs, merged)
25. session_0172_64fea49c (merged)
26. session_0181_9578bd37 (28 msgs, merged)
27. session_0182_e8bff492 (17 msgs, merged)
28. session_0183_600e4b12 (18 msgs, merged)
29. session_0186_00aa8a90 (32 msgs, merged)
30. session_0196_a25d2a99 (merged)
31. session_0198_d1b4ec95 (merged)
32. session_0208_ec002cfb (merged)
33. session_0209_3876259f (13 msgs, merged)
34. session_0225_f9e547e7 (merged)
35. session_0228_d5d7c6d1 (merged)
36. session_0233_4e4751f7 (merged)
37. session_0239_d7900b1b (merged)
38. session_0244_1d3b70d0 (merged)
39. session_0245_d5d7c6d1 (merged)
40. session_0246_570c671a (merged)
41. session_0249_a92c2105 (17 msgs, merged)
42. session_0250_86556d3c (merged)
43. session_0252_33db0a0b (merged)
44. session_0255_e995a0a8 (23 msgs, merged)
45. session_0259_313263f6 (merged)
46. session_0282_515749a9 (merged)
47. session_0284_d5d7c6d1 (27 msgs, merged)
48. session_0286_cdb1bf9a (merged)
49. session_0296_24ad7994 (merged)
50. session_0300_d5d7c6d1 (merged)
51. session_0301_847a46fe (merged)
52. session_0303_3333d84a (merged)
53. session_0305_da1a2999 (merged)
54. session_0307_886c63b4 (merged)
55. session_0309_7eca689f (merged)
56. session_0311_38569af0 (40 msgs, merged)
57. session_0315_3f7bf269 (merged)
58. session_0317_8c376519 (merged)
59. session_0321_d588164b (merged)
60. session_0336_a7902d6b (20 msgs, merged)
61. session_0350_dfe429c9 (merged)
62. session_0371_a1339e0f (merged)
63. session_0374_3ad63cb3 (14 msgs, merged)
64. session_0391_4a909d0f (merged)

</details>

---

## 已完成的会话中消息数最多的（疑似合并了被取消的会话）

| 排名 | Session ID | 消息数 | 推测 |
|------|-----------|--------|------|
| 1 | session_0031_616028fb | 99 | 可能合并了多个短会话 |
| 2 | session_0131_da0e610b | 87 | 可能合并了多个短会话 |
| 3 | session_0132_ab2845a5 | 48 | 可能合并了被取消的会话 |
| 4 | session_0177_e406d6ea | 48 | 可能合并了被取消的会话 |
| 5 | session_0264_a92c2105 | 47 | 可能合并了被取消的会话 |
| 6 | session_0018_d5d7c6d1 | 45 | 可能合并了被取消的会话 |
| 7 | session_0014_91ec92b3 | 42 | 可能合并了被取消的会话 |
| 8 | session_0335_72183f8b | 42 | 可能合并了被取消的会话 |
| 9 | session_0019_d5d7c6d1 | 40 | 可能合并了被取消的会话 |
| 10 | session_0058_1ae024de | 40 | 可能合并了被取消的会话 |

---

## 合并逻辑说明

**触发条件:**
1. 同一用户（通过session_id中的用户标识匹配）
2. 时间间隔在 `MERGE_WINDOW_MINUTES=30` 分钟内
3. 连续咨询被视为同一服务流程

**合并效果:**
- 被合并的会话 → `status=cancelled`, `cancel_reason=merged`
- 保留的会话 → 消息内容合并，消息数增加

---

## 人工核实建议

1. **查看原始日志文件** 中被取消的session_id对应的时间段
2. **检查这些会话是否确实属于同一用户的连续咨询**
3. **验证合并后的评分是否合理**（合并后的会话评分是否反映了整体服务质量）

### 特别需要关注的会话

| Session ID | 消息数 | 关注点 |
|-----------|--------|--------|
| session_0015_8024c76c | 90 | 消息数极多，被合并是否意味着丢失了大量独立对话？ |
| session_0311_38569af0 | 40 | 同上 |
| session_0186_00aa8a90 | 32 | 同上 |

---

## 文件位置

- 本报告: `logs/cancelled_sessions_64.md`
- 原始数据: `data/task_queue.db`
- 生成时间: 2026-04-20 22:25
