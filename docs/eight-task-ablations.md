# 两个八任务消融分支

共同基线：SMF-an/cap-tac `e1703a7e396caa9654fa68b692999b530d32ad99`。
独立 checkout：`/mnt/data/ljz/opentacbench/cap-tac-baselines`。
只在 `exp/tactile-rgb-opencv`、`exp/no-tactile` 本地分支开发，不向同学 main 推送。
两个分支的实验差异由 `env_configs/ablations/mode.yaml` 固定；框架、八任务配置、seed 请求和预算相同。

## 当前交付状态

代码与 CPU 接线验证已实现；**尚不能宣称八任务均可运行，正式实验尚未启动**。
`assets.json` 保留明确的未就绪状态，缺失输入时在 Kit 启动和模型请求之前失败。
这不是一个可通过删除检查来运行的占位实验。必须补齐证据后冻结配置、审核状态和文件 hash。

| 任务 | 原生入口 | 传感器 | 原生完整操作要求 | 当前缺项 |
|---|---|---|---|---|
| 重量匹配 | envs.tactile_memory_match，重量条件 | 双 GelSight Mini | 参考→两个候选→将匹配物体放到匹配位 | 对应环境及重量配对配置未提供 |
| 粗糙度匹配 | 同上，粗糙度条件 | 双 GelSight Mini | 同上，匹配粗糙度 | 环境及粗糙度配对配置未提供 |
| 硬度匹配 | 同上，硬度条件 | 双 GelSight Mini | 同上，匹配硬度 | 环境及硬度配对配置未提供 |
| 随机组合匹配 | 同上，组合条件 | 双 GelSight Mini | 参考与候选多属性匹配并放置 | 环境及组合配对配置未提供 |
| 弹力带 | envs.tension_strap | 双 GelSight Mini | 两段指定力与连续保持时间，合法 END | 源码存在；Kit 接线待验证 |
| 拧灯泡 | envs.bulb_tightening | 双 GelSight Mini | 完成原生旋入、就位与安全判定 | 源码存在；Kit 接线待验证 |
| 钢琴 | env.tasks.arm_shadow_piano | 单指 DIGIT | 指定八音的音高、力度、持音和释放 | 源码存在；Kit 接线待验证 |
| 盲盒抓球 | env.tasks.blind_pick_balls | 双 GelSight Mini | 原生抓持验证、提离箱体、放入碗并释放 | 源码存在；Kit 接线待验证 |

当前 OpenTacBench 固定为 `179bb8b5fe987f30ac17c407c5265b40c8f223a7`。
四个参考匹配任务不能用 `weight_classify` 等分类入口替代。也不能仅改 prompt 的属性名便声称原生任务已经改变。
需要同学提供实际参考匹配环境、各单属性/组合条件的配置或 seed 对应关系，以及原生完整成功检查。
当前四项的 `native_contract_verified=false`，源码检查也直接失败；尚未猜测任何控制参数来补这个缺口。

钢琴使用原生 240 Hz、action_repeat=8（30 Hz 四维目标），保持原生动作范围与严格力度评分。
夹爪任务采用原生位置命令和运动规划，按实际物理 step 记时，不把一个宏动作当作一个物理步。
现有原生配置为 120 Hz；参考匹配环境的频率仍须对照实际环境确认。
每次程序预算：钢琴 2400 次动作调用，其他 800 次；进程预算：灯泡 14400 秒，其他 7200 秒。
这是两组共同的预注册预算；正式冻结前可根据接线 smoke 核对是否足够，不能根据正式组别结果分别调整。

## 信息与控制边界

`capx/ablation/server.py` 在同学的 HTTP 服务和 Kit 主线程队列上注册受限端点。
原服务的 `/tactile`、`/observation`、`/check_success` 等丰富端点在实验模式中均被拦截。
任务源码、物理、安全终止和原生 scorer 不改。正常启动仍需同一份原生初始化/pre_move。
原生物理的固定夹具释放、安全接触检查可能内部使用传感器，两个实验一致；它们不作为 agent 的控制反馈。
程序发出的 gripper 命令强制使用位置模式，不调用自动触觉闭合、滑移矫正、自动重抓或示范策略。
灯泡不开放同学原来读取真实力进行调整的 feed helper；程序只能组合位置/姿态命令。

| 接口/信息 | RGB/OpenCV | 无触觉 |
|---|---|---|
| 普通场景 RGB、实际机器人位置/速度 | 开放 | 相同 |
| 固定位置动作、动作状态、物理 step/time | 开放 | 相同 |
| 单指/左右原始 RGB | read_tactile_rgb | 不注册，服务端也拒绝 |
| 数值 depth/marker、标定权重、估计力/属性接口 | 不开放 | 不开放 |
| 真实力、私有成功、score/metadata | 仅宿主最终审计 | 仅宿主最终审计 |
| numpy/cv2 图像计算 | 程序内部自行计算 | 可处理普通场景图像 |
| 技能/历史代码/整理/检索 | 全部停用 | 全部停用 |

生成程序只得到三个共同函数与 RGB 组增加的一个函数，不得到 env、APIS、网络或文件句柄。
AST 和受限内建/模块接口阻止访问私有信息；禁止多段代码，缺少合法 `capx_stop=True` 记失败。
这是针对实验程序的信息约束，不宣称是可抵御恶意 Python 的操作系统安全沙箱。
不运行任意导入，numpy/cv2 仅开放纯计算函数；普通 stdout 被禁用。
允许程序内部循环与恢复，但宿主不会续写、新增 block、调用修复或重试模型。

公开日志复用 `capx.serving.univtac_action_log`，按白名单记录动作和错误类型。
RGB 只在程序实际读取时存入 `tactile_images/`，同 episode/传感器/物理 step 去重，日志只有图像引用、hash 和采样信息。
无触觉分支不创建这个目录，也不产出触觉特征文件。
错误文字可能包含用户计算的数值，因此运行异常只输出类型；完整程序和响应保留供人工审计。
原生输出和最终结果在 `private/`，从不进入正在执行的程序或后续模型请求。

## 输入清单与公平性

- `env_configs/ablations/seeds40.json`：正式保留 41000–41039，共 40 个 seed，两个分支逐项配对。
- 接线开发 seed：42000、42001；默认 `CAPX_ABLATION_PHASE=smoke`。正式必须显式设为 `formal`。
- 40 条弹力带请求已独立生成并冻结，目标 10–18 N、保持 1.3–2.0 秒；不是原 30 条清单循环补十条。
- 钢琴请求沿用已有八音 `E4 F4 C#4 D#4 D#4 E4 C#4 F4`，轻重均衡，持音/释放 0.12 秒。
- `assets.json`：任务入口、原生源码/版本 hash、传感器、配置、划分审核状态。
- 当前只能确认这些 seed 不属于已知开发编号；**完整标定训练与他人开发集的重叠审计未完成**，所以未放行正式运行。

首次请求只包含任务说明、本次任务请求、API 文档、初始机器人状态和**当前 episode 的场景图片**。
当前场景以 PNG data URL 输入；不读取专家视频、不切帧，也不要求登记专家视频资产。
两个分支都保留 `get_scene_rgb()`，生成的完整程序可以在执行期间读取普通相机图像并自行处理；不会因此新增模型请求。
此前视频接线测试的录像和产物保留在数据盘，不再作为本实验的输入依赖。
模型冻结为 `gpt-6.1-sol`，Responses API、medium reasoning、8192 最大输出 token；请求超时 300 秒且不重试。
所有模型请求清除大小写代理，HTTP Session 也关闭 trust_env。未知 token 统计保留未知，不伪造为零。

这份文档、八项 YAML、seeds40.json、assets.json 及两个分支共同代码 commit 是给原表征组的实验约定。
原表征组需使用同一底层动作集合、任务请求、当前场景视觉输入、输入预算与原生判分。这里没有运行或自动通知该组。

## CPU 检查

```bash
cd /mnt/data/ljz/opentacbench/cap-tac-baselines
source env_configs/ablations/runtime.env
export CUDA_VISIBLE_DEVICES=''
/mnt/data/ljz/opentacbench/conda-envs/UniVTAC/bin/python -m pytest \
  tests/test_paired_ablation.py -q -p no:cacheprovider \
  --basetemp=/mnt/data/ljz/opentacbench/ablation-cpu-tests/unique-new-directory
/mnt/data/ljz/opentacbench/conda-envs/UniVTAC/bin/python -m capx.ablation.protocol \
  --config env_configs/ablations/shadow_piano.yaml
```

检查报告不需要 Kit/GPU/凭据。preflight 当前返回非零是对缺失输入的正确拦截。
CPU 测试采用模拟原生任务/模拟模型，并验证当前场景图片实际进入唯一请求；不能证明真实物理任务成功或跨版本 Kit 兼容。
完整服务的动作/触觉时序、当前场景观测和判分需在输入就绪后的 16 条接线 smoke 中核验。

## 后续单 seed 启动方式（本轮未执行）

先补齐并审核 assets.json。使用原框架的两个入口，不启动任何 Claude/SkillOpt/代码记忆循环。
两个终端使用相同 branch、配置、RUN_ID、seed 和明确 GPU。每 seed 都启动新服务，尤其是灯泡和盲盒。

```bash
cd /mnt/data/ljz/opentacbench/cap-tac-baselines
source /mnt/a/ljz/miniconda/etc/profile.d/conda.sh
conda activate /mnt/data/ljz/opentacbench/conda-envs/UniVTAC
source env_configs/ablations/runtime.env
export GPU=1 CAPX_ABLATION_SEED=42000 CAPX_ABLATION_PHASE=smoke
export CAPX_ABLATION_RUN_ID=shadow_wiring_42000
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy
python -m capx.serving.launch_univtac_server \
  --ablation-config env_configs/ablations/shadow_piano.yaml \
  --seed "$CAPX_ABLATION_SEED" --headless --port 8120
```

另一个终端完成同样的环境设置，并设置自己的 Responses URL/凭据环境变量（不要提交到 Git）：

```bash
python -m capx.envs.launch \
  --config-path env_configs/ablations/shadow_piano.yaml \
  --model gpt-6.1-sol --server-url "$CAPX_API_BASE_URL"
```

必须显式设置 GPU；运行前解析为单卡 UUID，CUDA 仅看见这一张卡，Kit 禁用 multi-GPU，EGL 显式使用指定物理设备。
首次运行仍须实机验证 GPU 隔离；本轮没有占卡确认。
服务就绪后，原有独立 watchdog 另外执行 process_timeout_s 的墙钟上限，即使 /health 正常也会到期结束本次服务。
它核对 /proc 的进程启动标识，只针对本次 PID；没有自动重启。Kit 启动阶段仍建议外部进程管理器设置启动超时。
不要使用按名称 kill，也不要用原有会 respawn 的 shell 循环批跑此实验。

HTTP 依赖引用保存在 `/mnt/data/ljz/opentacbench/ablation-http-deps`，仅链接 Kit 已带的 fastapi 0.110.0、starlette 0.36.3、uvicorn 0.29.0、pydantic 2.9.2 等所需包。
未升级仿真环境，也未把整个 Kit pip_prebundle 放到 Python 搜索路径前面。
`runtime.env` 只含本机路径和非敏感设置；若迁移服务器需要重新核对这些位置。

## 产物与统计

数据根目录：`/mnt/data/ljz/opentacbench/ablation-runs/<mode>/<task>/<RUN_ID>/`。
`server/` 保存原生私有审计与普通相机的动作时刻截图；截图索引保存真实物理时间，不将稀疏截图冒充连续视频。
`seed_<seed>/` 保存 effective_config、request/response、code、run.log、univtac_actions.jsonl、result.json 和原框架 trial 目录。
RGB 组另有 tactile_images；private/native_result.json 只由宿主读取。
记录模型调用/token、控制调用数、实际物理步数、合法 END、失败类型及耗时；公开错误与原生失败分开。
连接中断时以终态审计的 server_physics_steps 为完整计数，缺少终态审计就保留未知。

`python -m capx.ablation.report <明确选择的正式运行目录...> --output <数据盘统计目录>`
生成统一 per_seed.json、per_task.json/CSV。重复 seed 报错，smoke 不计入，缺失 trial 显式列出。
仅当某组某任务收齐 40 条才报告成功率；正式成功数始终由原生完整任务判定，不采用 5/6 晋升门槛。
