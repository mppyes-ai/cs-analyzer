# CS-Analyzer 取消会话清单 (2026-04-20测试)

## 统计
- 总取消数: 64通
- 取消原因: 全部为 "merged" (会话合并)
- 合并窗口: 30分钟 (MERGE_WINDOW_MINUTES)

## 取消会话列表

| # | 被取消Session ID | 取消原因 | 创建时间 |
|---|------------------|---------|---------|
| 1 | session_0000_a92c2105 | merged | 21:33:20 |
| 2 | session_0015_8024c76c | merged | 21:33:20 |
| 3 | session_0021_8024c76c | merged | 21:33:20 |
| 4 | session_0029_5bdbb5bb | merged | 21:33:20 |
| 5 | session_0036_57b45287 | merged | 21:33:20 |
| 6 | session_0039_542522f2 | merged | 21:33:20 |
| 7 | session_0040_240c8d7f | merged | 21:33:20 |
| 8 | session_0041_16e9abc8 | merged | 21:33:20 |
| 9 | session_0046_90b41dc4 | merged | 21:33:20 |
| 10 | session_0049_4f71c8b8 | merged | 21:33:20 |
| 11 | session_0054_35413ada | merged | 21:33:20 |
| 12 | session_0060_a92c2105 | merged | 21:33:20 |
| 13 | session_0091_0f921bd6 | merged | 21:33:20 |
| 14 | session_0096_960cb70a | merged | 21:33:20 |
| 15 | session_0108_a1e84b6b | merged | 21:33:20 |
| 16 | session_0113_ba189dc3 | merged | 21:33:20 |
| 17 | session_0120_09d6c7b1 | merged | 21:33:20 |
| 18 | session_0122_75cb50f0 | merged | 21:33:20 |
| 19 | session_0136_72495336 | merged | 21:33:20 |
| 20 | session_0137_a92c2105 | merged | 21:33:20 |
| 21 | session_0153_8024c76c | merged | 21:33:20 |
| 22 | session_0164_47557673 | merged | 21:33:20 |
| 23 | session_0165_eb9b7555 | merged | 21:33:20 |
| 24 | session_0166_8024c76c | merged | 21:33:20 |
| 25 | session_0172_64fea49c | merged | 21:33:20 |
| 26 | session_0181_9578bd37 | merged | 21:33:20 |
| 27 | session_0182_e8bff492 | merged | 21:33:20 |
| 28 | session_0183_600e4b12 | merged | 21:33:20 |
| 29 | session_0186_00aa8a90 | merged | 21:33:20 |
| 30 | session_0196_a25d2a99 | merged | 21:33:20 |
| 31 | session_0198_d1b4ec95 | merged | 21:33:20 |
| 32 | session_0208_ec002cfb | merged | 21:33:20 |
| 33 | session_0209_3876259f | merged | 21:33:20 |
| 34 | session_0225_f9e547e7 | merged | 21:33:20 |
| 35 | session_0228_d5d7c6d1 | merged | 21:33:20 |
| 36 | session_0233_4e4751f7 | merged | 21:33:20 |
| 37 | session_0239_d7900b1b | merged | 21:33:20 |
| 38 | session_0244_1d3b70d0 | merged | 21:33:20 |
| 39 | session_0245_d5d7c6d1 | merged | 21:33:20 |
| 40 | session_0246_570c671a | merged | 21:33:20 |
| 41 | session_0249_a92c2105 | merged | 21:33:20 |
| 42 | session_0250_86556d3c | merged | 21:33:20 |
| 43 | session_0252_33db0a0b | merged | 21:33:20 |
| 44 | session_0255_e995a0a8 | merged | 21:33:20 |
| 45 | session_0259_313263f6 | merged | 21:33:20 |
| 46 | session_0282_515749a9 | merged | 21:33:20 |
| 47 | session_0284_d5d7c6d1 | merged | 21:33:20 |
| 48 | session_0286_cdb1bf9a | merged | 21:33:20 |
| 49 | session_0296_24ad7994 | merged | 21:33:20 |
| 50 | session_0300_d5d7c6d1 | merged | 21:33:20 |
| 51 | session_0301_847a46fe | merged | 21:33:20 |
| 52 | session_0303_3333d84a | merged | 21:33:20 |
| 53 | session_0305_da1a2999 | merged | 21:33:20 |
| 54 | session_0307_886c63b4 | merged | 21:33:20 |
| 55 | session_0309_7eca689f | merged | 21:33:20 |
| 56 | session_0311_38569af0 | merged | 21:33:20 |
| 57 | session_0315_3f7bf269 | merged | 21:33:20 |
| 58 | session_0317_8c376519 | merged | 21:33:20 |
| 59 | session_0321_d588164b | merged | 21:33:20 |
| 60 | session_0336_a7902d6b | merged | 21:33:20 |
| 61 | session_0350_dfe429c9 | merged | 21:33:20 |
| 62 | session_0371_a1339e0f | merged | 21:33:20 |
| 63 | session_0374_3ad63cb3 | merged | 21:33:20 |
| 64 | session_0391_4a909d0f | merged | 21:33:20 |

## 关键观察

1. **全部64通都是 `cancel_reason: "merged"`** — 没有重复去重(Duplicate session)的情况
2. **这64通被合并到了哪些主会话？** 需要查看 `session_data` 中保留的 `merged_session_ids` 字段
3. **合并窗口: 30分钟** — 同一用户在30分钟内连续咨询会被合并

## 人工核实建议

要验证合并逻辑是否正确，需要:
1. 找到保留的主会话 (status='completed')
2. 检查其 `session_data->merged_session_ids` 列表
3. 核对这些被合并的会话是否确实是同一用户在短时间内连续咨询

## 文件位置
- 数据库: `data/task_queue.db`
- 本清单生成时间: 2026-04-20 22:24
