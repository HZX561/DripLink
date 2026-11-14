# 复现范围与文件准备

## 仅克隆源码可以完成的内容

本次发布包含算法实现、固定版本及 wheel 哈希的 Windows CPU/CUDA 环境、无数据核心测试、训练与评估入口。可以安装环境、检查网络结构、运行合成输入的几何/模型/指标测试，查看汇总实验结果。

```powershell
.\bootstrap.ps1 -Device cpu -Python 'C:\path\to\python311\python.exe'
.\.venv\Scripts\python.exe scripts/verify_project.py --environment-only
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

`test_core.py` 和 `test_assets.py` 无需真实照片或权重。`test_current_pipeline.py` 需要配套研究资产，缺少时整组显示 skipped。GitHub Actions默认只运行源码/恢复工具测试，CI通过不表示重新测得了论文或本项目的准确率。

## 识别与复测现有指标

完整研究资产已在 [v0.1.0 Release](https://github.com/HZX561/DripLink/releases/tag/v0.1.0) 发布。`assets/release.json` 记录各包的文件名、大小、SHA256和下载地址；发布页同时提供 `SHA256SUMS.txt`。自动下载、校验并恢复到正确目录：

```powershell
.\.venv\Scripts\python.exe scripts/download_assets.py
.\.venv\Scripts\python.exe scripts/verify_project.py --inference --device cpu
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe evaluate_meter.py --split val --output reports/validation_retest --device cpu
```

公开指标由固定 GPU 环境产生，CPU 与 GPU 的浮点计算可能带来少量差异。对齐原复测环境时应使用 CUDA 锁文件及记录的硬件环境。选定权重、数据清单和软件版本均需一并核对。

手动从Release下载四个ZIP后，也可以离线恢复：

```powershell
.\.venv\Scripts\python.exe scripts/download_assets.py --local-archives 'C:\path\to\downloaded-zips'
```

脚本校验压缩包大小和SHA256，拒绝越界路径、符号链接和不同内容的已有文件；只有明确传入 `--overwrite` 才会替换不同的工作副本。只需要单图识别时可下载 `--bundles models`，配自己的全指针照片使用。复测、完整集成测试和继续训练建议下载四个包。

cache包可省略，再运行 `prepare_data.py --rebuild-cache --device cpu` 重建，但将增加准备时间。data包已经包含当前全部原图，原始OneDrive下载包及作者检查点不随当前实验包发布。

## 继续训练

读数和检测器训练入口均依赖配套初始权重，使用冻结训练/验证划分，结果写入新的 `runs/` 目录。比较候选只用验证集，保留每次参数及结果。原测试集有历史查看记录，后续复测仍应披露这一事实。

如果采用新的数据划分或从头训练，这是新的实验版本，不能直接使用当前检查点绑定及准确率。当前发布没有提供还原原混合表阶段全部训练历史的入口。

种子固定为 42，但 CUDA 自适应池化反向传播存在非确定性，跨硬件或重复训练不保证逐比特相同。详细历史、误差和限制见 [实验说明](EXPERIMENT.md)。
