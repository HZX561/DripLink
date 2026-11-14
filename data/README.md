# 数据文件说明

当前实验的3780张全指针照片、作者读数/角点标注、冻结划分和来源清单已通过 [v0.1.0 Release](https://github.com/HZX561/DripLink/releases/tag/v0.1.0) 的 `driplink-data-v0.1.0.zip` 发布，训练裁剪缓存另在 cache 包中。源码仓库保留本说明和空采集模板，大文件通过 Release 分发。

WMeter5K 的来源及下载说明见作者项目：[ZZZHANG-jx/WMeter-Reader](https://github.com/ZZZHANG-jx/WMeter-Reader)。请从原始来源取得数据并按其适用条件使用。本项目开源许可证不授予第三方数据的额外使用或再分发许可。

## 复测当前指标需要的本地文件

```text
data/
  manifest.json                本项目冻结清单：样本、真值、角点及分组划分
  manifest.sha256              清单哈希
  images/                     当前 3780 张全指针原图
  raw/WMeter5K_source.zip      原始下载包；原图缺失时用于恢复
  provenance/parent_manifest.json   上游实验暴露清单；独立评估查重需要
  cache/                      prepare_data.py 可重建的训练缓存
```

清单 SHA256：`ddd23a9f10c88420e2b90824bebf4fea6f18ed27e698962cc573fa005f168486`。

当前划分为训练2601、验证607、历史测试572，共463个近重复组。data发布包包含原字节的冻结清单及全部当前原图，下载后无需重新划分。原始下载包本身不含本项目清单；当前代码仍不提供从零重新生成相同分组的入口。

```powershell
.\.venv\Scripts\python.exe scripts/download_assets.py --bundles data cache
```

不要为通过模型的哈希检查而随意修改清单或将不同划分冒充当前实验版本。新数据集需要独立版本、相应初始化权重及评估绑定。

发布包包含当前原图，缺图时可以重新用下载脚本恢复；缓存也可以基于原图和当前检测器重建。原始 OneDrive 包中的作者 `im.pkl` / `tr.pkl`、暂停使用的混合型图片及旧100张项目备份不在当前实验包中，本地原始备份仍保留。

## 新采集数据

`collection/independent.json` 是空清单；`sample.example.json` 只演示字段，示例数字不对应任何照片。采集及人工核验规则见 [人工任务说明](../docs/HUMAN_TASKS.md)。实际照片放在被忽略的 `collection/private/`。
