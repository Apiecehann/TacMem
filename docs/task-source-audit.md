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

仓库：[flyingc2004/UniVTAC](https://github.com/flyingc2004/UniVTAC/tree/feat/task-transfer)。分支 `feat/task-transfer`，核对 commit `015f972ee9c3e9173f862aed9038bd1c34fee9ac`。

- [原生任务](https://github.com/flyingc2004/UniVTAC/blob/015f972ee9c3e9173f862aed9038bd1c34fee9ac/envs/tactile_memory_match.py)：`envs.tactile_memory_match.TaskCfg/Task`。
- [配置](https://github.com/flyingc2004/UniVTAC/blob/015f972ee9c3e9173f862aed9038bd1c34fee9ac/task_config/tactile_memory_match_smoke.yml)：`sensor_type: gsmini`，不是 selection-only 配置。
- 四类为 `light_smooth / light_rough / heavy_smooth / heavy_rough`，由密度和摩擦系数组合产生；该版未加入硬度。
- 一个 reference、两个候选，其中一个与 reference 同类，另一个为不同类；左右匹配位置随机。
- 原生判分要求匹配候选实际放到匹配垫上、非匹配候选未放入。放置检查包含垫面重叠、支撑高度误差小于 0.012 m、夹爪开度大于 0.020 m，并连续满足 10 步。仅输出候选名称不足以成功。
- `check_success()` 不单独要求历史上触摸过 reference；不能把“原生成功”当作“证明执行了参考探测”的证据。
- `TaskCfg.use_adaptive_grasp=True`，任务还会记录触觉 timeline。接入消融时须复核公共动作路径不执行触觉自动抓持、原生诊断留在私有审计，不让标量日志进入无触觉组。此次未改变这些行为。

远端文件副本和 SHA256 存于 `/mnt/data/ljz/opentacbench/task-source-audit/20261102/`。这是源码审计副本，不是完整可运行的 UniVTAC 安装；未验证其全部依赖、资产和 Kit 兼容性。

## 对当前实验配置的影响

当前 `weight_match/roughness_match/hardness_match` 仍要求 reference 匹配，`combined_match` 仍写三属性且指向 OpenTacBench，均与查证源码不符。这次只审计来源，不擅自更换任务或修改评分；就绪标记继续为 false。后续应统一修正两组配置，并与原表征组保持相同任务定义。
