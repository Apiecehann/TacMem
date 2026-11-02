# 前四项任务来源审计

核对日期：2026-11-02。仅静态读取源码；未启动 Kit、GPU 或模型请求，未修改原生任务。

## OpenTacBench：前三项 classify

本机根目录 `/mnt/a/ljz/cap/OpenTacBench`，分支 `tactile-memory-tasks`，commit `179bb8b5fe987f30ac17c407c5265b40c8f223a7`。

| 实验属性 | 原生入口 | 原生要求 |
|---|---|---|
| 重量 | `envs/weight_classify.py` | 轻物体→蓝盘，重物体→黄盘 |
| 粗糙度 | `envs/roughness_classify.py` | 光滑物体→蓝盘，粗糙物体→黄盘 |
| 硬度 | `envs/hardness_classify.py` | 软物体→蓝盘，硬物体→黄盘 |

三者均有 `TaskCfg` 和 `Task`，默认属性标签为 `random`，使用单个当前物体；另一属性的备用物体停在场景外侧。没有参考物体、两个候选和参考匹配流程。
原生 `check_success()` 检查相对目标位姿：各水平轴误差小于 0.04 m、竖直误差小于 0.01 m、朝向点积大于 0.965。不能把只输出分类标签记为原生成功。
本机没有单独的 `task_config/weight_classify.yml` 等配置；公共 `task_config/gelsight.yml` 存在，指定 `gsmini`。同学服务已有重量/硬度的 `gelsight.yml` 配置路由；粗糙度入口和实验路由仍需显式接线。

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
资产清单的部署目标为 `/mnt/data/ljz/opentacbench/UniVTAC-composable-tactile-slots`，当前尚未建立完整运行 checkout。源码副本不冒充运行安装；依赖、凸纹资产和 Kit 兼容性尚未验证，就绪标记保持 false。

## 对当前实验配置的影响

当前 `weight_match/roughness_match/hardness_match` 仍要求 reference 匹配，需要另行对齐前三个 classify。`combined_match` 的三属性要求与此次确认的分支一致；两个实验分支均已更正仓库、commit、源码 hash、任务配置和部署目标，不再指向 OpenTacBench 的不存在入口。后续完成环境部署、底层动作/信息隔离和划分审核后才能放行运行。
