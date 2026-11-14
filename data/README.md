# 数据文件说明

GitHub 仓库只包含本说明及 `collection/` 下的空采集模板，不包含照片、作者标注、冻结实验清单、裁剪缓存或旧项目备份。

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

当前划分为训练 2601、验证 607、历史测试 572，共 463 个近重复组。冻结清单需从项目维护者处取得，原数据下载包本身不含该清单。本次源码发布没有提供其公开下载地址，也没有提供从原始包重新生成相同分组和清单的脚本。

不要为通过模型的哈希检查而随意修改清单或将不同划分冒充当前实验版本。新数据集需要独立版本、相应初始化权重及评估绑定。

原图恢复和训练缓存重建命令见根目录 README，前提是已经有冻结清单及当前检测器。`raw/legacy_originals.zip` 为维护者本地旧项目资料备份，与当前训练无关，也不随源码发布。

## 新采集数据

`collection/independent.json` 是空清单；`sample.example.json` 只演示字段，示例数字不对应任何照片。采集及人工核验规则见 [人工任务说明](../docs/HUMAN_TASKS.md)。实际照片放在被忽略的 `collection/private/`。
