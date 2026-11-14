# 许可证、依赖及研究来源

## 本项目

DripLink（滴水智联）源码采用 GNU Affero General Public License version 3 only（SPDX：`AGPL-3.0-only`），完整文本见 [LICENSE](../LICENSE)。项目署名见 [NOTICE](../NOTICE)。

## Ultralytics

项目通过安装 `ultralytics==8.4.121` 使用 YOLO11n Pose。该依赖提供 AGPL-3.0 版本；本项目选择 AGPL-3.0-only 发布源码。上游源代码和许可见：

- https://github.com/ultralytics/ultralytics
- https://github.com/ultralytics/ultralytics/blob/main/LICENSE

本仓库不打包第三方 Python 安装包；使用者通过锁文件安装。PyTorch、TorchVision、NumPy、OpenCV、Pillow、PyYAML 及其余传递依赖分别保留各自的许可和版权。

## WMeter5K 与参考研究

数据及方法参考来源为作者项目 [ZZZHANG-jx/WMeter-Reader](https://github.com/ZZZHANG-jx/WMeter-Reader)。相关论文题目为 *Towards Accurate Readings of Water Meters by Eliminating Transition Error: New Dataset and Effective Solution*，作者包括 Jiaxin Zhang 等；完整引用请以作者项目和出版记录为准。

本项目实现的是 YOLO 区域定位与 CNN/Transformer 读数流程，实验划分和训练流程以本仓库说明为准，不将其标为作者官方实现的直接复现。

当前实验使用的3780张照片、相关读数/角点标注和本项目冻结划分经维护者要求，通过GitHub Release单独提供，并保留作者及数据来源说明。它们不因本仓库AGPL源码许可而被重新授权，数据使用条件仍以原始来源的适用条款为准。

本项目的检测器及全指针读数网络检查点随AGPL-3.0-only源码提供。作者 `im.pkl` / `tr.pkl`、未使用的混合型图像和旧项目备份不在当前发布包中。
