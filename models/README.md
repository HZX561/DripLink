# 模型文件说明

当前权重、选优配置及来源记录通过 [v0.1.0 Release](https://github.com/HZX561/DripLink/releases/tag/v0.1.0) 的 `driplink-models-v0.1.0.zip` 发布，保持原始权重字节与哈希。下载后恢复下列配套文件：

```powershell
.\.venv\Scripts\python.exe scripts/download_assets.py --bundles models
```

| 本地文件 | 用途 | SHA256 |
|---|---|---|
| `detector.pt` | YOLO11n Pose 区域及角点检测 | `6d245b126e0beebdf63ba55d6189a21eae2c46ad5ddc237e24e920caaab56efb` |
| `reader.pt` | 953,194 参数的全指针 CNN + Transformer | `476527196873338ec6b26eba544815c2b86a1a27bfeba78de8648943e5676f76` |
| `selection.json` | 相对路径、权重哈希、清单哈希及选优记录 | 与上述权重和冻结清单配套 |

当前检测置信度为 0.15，主解码器为 context；读数位序为 10^5 至 10^-4 m³。检查点与冻结数据清单哈希绑定，不能用其他模型随意替代并引用当前指标。

`train_meter.py` 从配套读数网络检查点继续微调，`train_detector.py` 从配套检测器继续微调。这两条命令当前没有提供无初始权重的从零训练流程。第三方作者的 `im.pkl` / `tr.pkl` 不兼容这些入口，也不是本项目当前选定权重。

本项目检查点随AGPL-3.0-only源码提供；来源与训练历史见模型包的 provenance 目录及实验说明。第三方训练数据保留原有权利与适用条件，不因本项目源码许可而被重新授权。
