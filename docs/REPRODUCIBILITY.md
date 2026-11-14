# 复现范围与文件准备

## 仅克隆源码可以完成的内容

本次发布包含算法实现、固定版本及 wheel 哈希的 Windows CPU/CUDA 环境、无数据核心测试、训练与评估入口。可以安装环境、检查网络结构、运行合成输入的几何/模型/指标测试，查看汇总实验结果。

```powershell
.\bootstrap.ps1 -Device cpu -Python 'C:\path\to\python311\python.exe'
.\.venv\Scripts\python.exe scripts/verify_project.py --environment-only
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

`test_core.py` 无需真实照片或权重。`test_current_pipeline.py` 需要本地配套研究资产，缺少时整组显示 skipped。GitHub Actions 同样只对源码和可用资产执行测试，CI 通过不表示重新测得了论文或本项目的准确率。

## 识别与复测现有指标

需要单独取得本项目的冻结清单、原图、当前检测器和读数权重、对应 `selection.json`，并按 [数据说明](../data/README.md) 与 [模型说明](../models/README.md) 放置。这些内容未随本次源码发布，也没有公开下载入口。请与维护者确认可提供的资料及适用使用条件。

拿到配套资产后，`prepare_data.py` 可从已准备的原图或指定格式的原始下载包恢复图片，并重建裁剪缓存；它不从零生成冻结清单，也不负责从网络下载数据。

```powershell
.\.venv\Scripts\python.exe prepare_data.py --rebuild-cache --device cpu
.\.venv\Scripts\python.exe scripts/verify_project.py --inference --device cpu
.\.venv\Scripts\python.exe evaluate_meter.py --split val --output reports/validation_retest --device cpu
```

公开指标由固定 GPU 环境产生，CPU 与 GPU 的浮点计算可能带来少量差异。对齐原复测环境时应使用 CUDA 锁文件及记录的硬件环境。选定权重、数据清单和软件版本均需一并核对。

## 继续训练

读数和检测器训练入口均依赖配套初始权重，使用冻结训练/验证划分，结果写入新的 `runs/` 目录。比较候选只用验证集，保留每次参数及结果。原测试集有历史查看记录，后续复测仍应披露这一事实。

如果采用新的数据划分或从头训练，这是新的实验版本，不能直接使用当前检查点绑定及准确率。当前发布没有提供还原原混合表阶段全部训练历史的入口。

种子固定为 42，但 CUDA 自适应池化反向传播存在非确定性，跨硬件或重复训练不保证逐比特相同。详细历史、误差和限制见 [实验说明](EXPERIMENT.md)。
