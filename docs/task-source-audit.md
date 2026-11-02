# 前四项任务来源审计

核对日期：2026-11-02。仅静态读取源码；未启动 Kit、GPU 或模型请求，未修改原生任务。

## OpenTacBench：前三项 classify

本机根目录 `/mnt/a/ljz/cap/OpenTacBench`，分支 `tactile-memory-tasks`，commit `179bb8b5fe987f30ac17c407c5265b40c8f223a7`。

| 实验属性 | 原生入口 | 原生要求 |
|---|---|---|
| 重量 | `envs/weight_classify.py` | 轻物体→蓝盘，重物体→黄盘 |
| 粗糙度 | `envs/roughness_classify.py` | 光滑物体→蓝盘，粗糙物体→黄盘 |
| 硬度 | `envs/hardness_classify.py` | 软物体→蓝盘，硬物体→黄盘 |

三者均有 `TaskCfg` 和 `Task`，使用单个当前物体；另一属性的备用物体停在场景外侧。没有参考物体、两个候选和参考匹配流程。
重量原生默认 `weight_label="heavy"`；粗糙度和硬度默认 `random`。同学服务对重量/硬度显式设置随机标签；两个消融分支也将这三项的标签配置统一为 `random`，由原生 seed 决定本次类别。本次实际类别不进入公开请求。
原生 `check_success()` 检查相对目标位姿：各水平轴误差小于 0.04 m、竖直误差小于 0.01 m、朝向点积大于 0.965。不能把只输出分类标签记为原生成功。
本机没有单独的 `task_config/weight_classify.yml` 等配置；公共 `task_config/gelsight.yml` 存在，指定 `gsmini`。三个消融配置均加载它的传感器/观测设置，并保留原生 `reset/pre_move`；不执行专家 `play_once()`。

### 同学 CaP-X 配置的复用

- 重量：`env_configs/univtac/weight_classify/univtac_weight_classify.yaml`。
- 硬度：`env_configs/univtac/hardness_classify/univtac_hardness_classify.yaml`。
- 两者详细 prompt 位于 `capx/envs/tasks/tactile/univtac_<task>.py` 的 `PROMPT`。消融 YAML 复用单物体分类、颜色映射及放置要求；原文件保留，不导入其技能、预计算特征、标定阈值或 `ORACLE_CODE`。
- 此版本没有 `roughness_classify` 对应的同学 YAML。已有 `grasp_classify` 是棱柱→绿/橙垫，不能直接替代蓝/黄盘任务；新增消融配置的要求来自 OpenTacBench `envs/roughness_classify.py`。
- 三项消融 YAML 使用 `weight_classify/roughness_classify/hardness_classify` 命名，来源与 SHA256 已记录。完整程序和动作接口仍遵循两个消融分支共同的单次生成协议。

## UniVTAC：组合匹配

仓库：[flyingc2004/UniVTAC](https://github.com/flyingc2004/UniVTAC/tree/feat/composable-tactile-slots)。按用户更正采用 `feat/composable-tactile-slots`，核对 commit `1a131a416e57e95164b40c54aeb6b15c76ab4476`；此前 `feat/task-transfer` 的双属性结论不适用于本实验。

- [原生任务](https://github.com/flyingc2004/UniVTAC/blob/1a131a416e57e95164b40c54aeb6b15c76ab4476/envs/tactile_memory_match.py)：`envs.tactile_memory_match.TaskCfg/Task`。
- 三个二值属性为重量 `light/heavy`、粗糙度 `smooth/rough`、硬度 `rigid/soft`，共 8 类。一个 reference、两个候选，其中一个三属性均匹配，另一个至少一项不同；左右匹配位置随机。
- 重量由密度设置；粗糙度由 `Can_d4cm.usd` / `Can_d4cm_axial_ridges.usda` 表面网格设置，摩擦系数统一为 2.5；柔软类别使用 `StableNeoHookeanCfg(youngs_modulus=0.1, poisson_rate=0.45)`。单位遵循 UIPC 配置，不能将该参数擅自解释为牛顿力值。
- 代码的公开探测协议是 `tactile_probe.v4`，包含动作、depth/marker 与预计算摘要；仓库 benchmark 文档仍有 v3 和全同网格的旧描述，以该 commit 的源码为准。
- 完整任务判分要求匹配候选实际放到垫上、非匹配候选未放入。放置要求垫面重叠、中心高度不超过任务给定的支撑上限、夹爪开度大于 0.020 m，连续满足 10 步。仅输出候选名称不足以成功。
- `calibration_probe_only=True` 会改为检查三个探测质量有效，因此正式实验必须保留默认 `False`；完整任务判分不单独要求历史上触摸过 reference。
- `TaskCfg.use_adaptive_grasp=True`，公开 probe 自带触觉自适应闭合。两组消融不能直接调用整个 probe 或投递其摘要；仍使用共同的底层位置动作接口。原生触觉诊断应留在私有审计。

### 配置选择

资产清单选用 [通用配置](https://github.com/flyingc2004/UniVTAC/blob/1a131a416e57e95164b40c54aeb6b15c76ab4476/task_config/tactile_memory_match_smoke.yml) 的传感器/观测设置，配合 `TaskCfg` 的随机类别、完整任务判分和关闭 Easy-GT 的默认值；单次 seed 仍由实验清单传入。此配置选择尚待 Kit 接线验证。

`tactile_memory_match_composable_smoke.yml` 固定一对软/硬类别，仅用于软体可行性测试；`tactile_memory_match_composable_capx_demo.yml` 固定类别并开启 `capx_easy_gt_enabled`，都不作为正式 40-seed 任务配置。标定配置也不能替代完整操作评分。

远端文件副本和 SHA256 存于 `/mnt/data/ljz/opentacbench/task-source-audit/20261102-composable-tactile-slots/`；旧分支审计保留在原目录。
完整 checkout 已部署到 `/mnt/data/ljz/opentacbench/UniVTAC-composable-tactile-slots`，共用现有 Conda/Isaac Sim/Isaac Lab/cuRobo/UIPC 二进制，通过独立路径加载 UniVTAC 自带的四个 TacEx 包；未改 OpenTacBench 的安装映射。
Kit 实际导入和相关 USD 依赖检查通过（含凸纹圆柱），未创建任务或执行物理。启动：`source /mnt/data/ljz/opentacbench/univtac-deployment/activate.sh`。
部署报告与 UIPC 共用差异说明：`/mnt/data/ljz/opentacbench/univtac-deployment/DEPLOYMENT_REPORT.md`；判分/控制接线及划分审核仍未完成，就绪标记保持 false。

## 对当前实验配置的影响

前三项已按用户确认改为 OpenTacBench 的实际 classify，配置、入口、源码 hash 与 40-seed 请求一起更新；仍使用原来的 41000–41039 正式 seed 和 42000/42001 开发 seed。`combined_match` 保留 UniVTAC 的三属性参考匹配。原生源码、专家策略、物理和判分均未修改；分类任务也不额外要求原生判分中没有的释放动作。
CPU 检查覆盖随机标签传入、原生 reset/最终判分调用、请求不泄露实际类别，以及两个模式的 prompt 边界；它不能替代真实物理接线验证。四项 `native_contract_verified=false` 保持不变，正式实验仍需物理接线与划分审核。
本次 `tests/test_paired_ablation.py`：32 passed（CPU、模拟任务，无 GPU/模型请求）。报告：`/mnt/data/ljz/opentacbench/ablation-cpu-tests/classify-wiring-20261102-v2.xml`。
