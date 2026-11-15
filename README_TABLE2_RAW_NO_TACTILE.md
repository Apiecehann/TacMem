# Table 2：raw / no-tactile 实验复现说明

本说明记录本机已经采用的 CaP-X 两组实验方法，供其他服务器及真机接入参考。不包含技能自动整理、技能更新或 SkillOpt 实验。

仓库：[Apiecehann/TacMem](https://github.com/Apiecehann/TacMem)。本说明核对的代码基线为：

| 组别 | 分支 | 文档提交前的代码 commit |
|---|---|---|
| raw | `exp/tactile-rgb-opencv` | `eab8b9f8c2c53c744fd4469c973bdcb355a81a90` |
| no-tactile | `exp/no-tactile` | `542b17fda674be6a627b2a80f0fd1d5d0af883f2` |

两分支保存同一份本说明。分支名保留历史名称，但 raw 当前不是“仅 RGB”，也不要求只能用 OpenCV。已有 `docs/eight-task-ablations.md` 是早期设计，包含已删除的入口、空技能库、40-seed 等旧约定；复现本次实验以本说明及上述代码/YAML 为准。

本文覆盖六项主体实验及单独的弹力带配置。钢琴的后续随机六音、技能和 30 Hz 改动仍有本地未提交内容，不能将拉取这两个分支等同于得到最新钢琴实验；本次文档提交不发布这些改动。

## 1. 两组到底比较什么

| 项目 | raw | no-tactile |
|---|---|---|
| YAML 的 `agent0_mode` / `observation_mode` | `raw` | `none` |
| 左右触觉 RGB | 可读，uint8 RGB 数组 | 不提供 |
| 左右带 marker 的 RGB | 可读，独立于普通触觉 RGB | 不提供 |
| 左右完整 depth | 可读，二维 float32，单位 mm | 不提供 |
| marker 坐标/位移数组 | 不提供 | 不提供 |
| 预计算 shear、coherence、anisotropy、粗糙度、contact、滑移判断、力估计 | 不提供 | 不提供 |
| 普通场景视觉、允许的位置 GT | 保留 | 相同 |
| 技能库 | 冻结的运动技能＋原始信号采样建议 | 冻结的非触觉运动技能 |
| 在线检索、技能更新、历史代码复用 | 不启用 | 不启用 |

raw 的 depth 是传感器原始深度定义，不自动等于形变量，更不是牛顿。缺失通道为 `None`，两种 RGB 不互相代替，缺失也不补零。

两组允许相同的底层位置运动和位置闭爪，不通过触觉自适应闭爪或隐藏的力估计替程序做控制。实体传感器、碰撞、任务物理和原生判分仍存在；no-tactile 关闭的是 agent 的触觉信息通路。动作返回和场景观测也按实验模式过滤，不能只检查函数名单。

普通任务允许物体/托盘位置 GT 与末端位姿。盲盒继续隐藏球位置，使用 `UniVTACBallApi`，不能为方便抓球开放球的 GT。接口中的成功判定/提交功能由宿主使用，不作为程序运行中的判分反馈。

## 2. 实际给模型什么

一次生成请求包含：任务要求、该 YAML 选中的 API 文档、选中的固定技能摘要，以及当前 episode 的普通场景图像。代码执行期间可循环采样和调整，但不重新请求 LLM。

- 两组保留同学的任务目标和共同运动说明，在 YAML 中追加各自的实验条件。
- raw 明确要求用 NumPy、OpenCV 或等价数组运算分析原始信号，并尝试用于判断或控制；仅调用读取函数不代表已经分析。
- no-tactile 明确说明无触觉接口，避免模型等待不存在的接口或因此放弃动作。
- 分类/匹配无法确定时，要求在两个合法选项中均匀随机选一次，并实际尝试完成抓取、运输和放置。raw 要先进行预算内的采样与分析，再考虑这一回退。
- `use_oracle_code: false`：任务文件中的 `ORACLE_CODE` 不作为本实验的专家示范输入。
- 不上传专家视频或切片，不使用视频/图像差分触发二次生成。
- 正常结束后由宿主取得原生判定；任务失败不触发自动修复。

模型约定为 `gpt-6-astra`、`reasoning_effort: high`、请求 `max_output_tokens: 8192`、Responses 流式输出。YAML 的 `max_tokens` 在客户端映射到 Responses 顶层的 `max_output_tokens`。模型请求超时 300 秒，自动重试为 0；每 episode 最多一次代码请求，不开启 ensemble。

YAML 虽保留 `temperature: 1.0`，当前 Responses 分支实际 payload 不发送 temperature；审计实际请求应读取 `model/request.json`。服务端实际 token usage 单独保存，不用请求上限替代实际用量。

## 3. raw API 用法

七项 GelSight 任务的 raw 配置选择：

```python
sample = get_tactile_data()
step = sample["step"]
time_seconds = sample["time_seconds"]
left = sample["sensors"]["left"]
right = sample["sensors"]["right"]

left_rgb = left["rgb"]                # H,W,3 uint8，可能为 None
left_marker_rgb = left["rgb_marker"]  # H,W,3 uint8，可能为 None
left_depth = left["depth"]            # H,W float32，单位 mm，可能为 None
```

也可分别调用：

```python
rgb = get_tactile_image(side="left", image_type="rgb")
marker_rgb = get_tactile_image(side="left", image_type="rgb_marker")
depth = get_tactile_depth(side="left")
```

需要三通道和左右同步比较时，优先一次 `get_tactile_data()`，使用同一快照的 step/time。分别调用不能自行假定它们一定来自同一帧。

实际读取的原始数据保存到运行目录的 `raw_tactile/`：RGB 为 PNG，depth 为无损浮点 NPY，`index.jsonl` 记录传感器、step、时间、文件名、shape/dtype。同一步同一传感器去重。当前目录名不是早期设计的 `tactile_images/`。no-tactile 不创建这份触觉采样产物。

## 4. 固定技能库怎样投递

使用原生 `capx/tactile_skills/library.py`，技能文件位于 `capx/tactile_skills/skills/{sense,act,place,protocol}/`。每个 YAML 的 `env.cfg.tactile_skills` 显式列出本任务的技能。

默认实际送入 prompt 的是技能的 `name + summary`。完整正文和 evidence 用于产物审计，不应误认为全部正文都发送给模型。实际发送内容以 `model/request.json` 为准。

这两组不是空技能库，也不是投递完全相同的全部技能：

- 共同保留接近、末端/物体位姿查询、位置闭爪、运输、放置等操作经验。
- raw 额外提供左右身份对应、动作前后采样、保持采样条件等建议，删除标定阈值、触觉公式及力转换模型。
- no-tactile 只选择非触觉技能，不投递触觉判断建议。
- 仓库仍可能留有未选择的原始技能文件。不能扫描整个目录并把所有技能追加到 prompt，也不能仅凭某文件还存在就认定它被投递。
- 正式运行不调用整理 agent，不写新技能、不根据测试结果修改摘要。

重量任务的例子：两组共同投递 `public-pose-queries`、`one-call-approach`、`lift-before-lateral`、`world-frame-transport`、`seat-height-from-plate`、`settle-after-release`、`keep-closed-if-task-allows`、`finger-position-grip`。raw 另选 `gel-shear-weight` 等采样技能；该分支中同名技能已改为原始观测建议，不提供旧重量阈值。no-tactile 不选它。

每次运行保存 `skills.json` 和 `skills_sha256`。两组技能文件不同，因此不要求两组技能 hash 相等；同组冻结版本应一致。

## 5. 任务与 seed

| 任务 | task_name | 配置，路径相对仓库根目录 |
|---|---|---|
| 轻重分类 | weight_classify | `env_configs/univtac/weight_classify/univtac_weight_classify.yaml` |
| 粗糙度分类 | grasp_classify | `env_configs/univtac/grasp_classify/univtac_grasp_classify.yaml` |
| 硬度分类 | hardness_classify | `env_configs/univtac/hardness_classify/univtac_hardness_classify.yaml` |
| 组合匹配 | tactile_memory_match | `env_configs/univtac/tactile_memory_match/univtac_tactile_memory_match.yaml` |
| 拧灯泡 | bulb_tightening | `env_configs/univtac/bulb_tightening/univtac_bulb_tightening.yaml` |
| 盲盒抓球 | blind_pick_balls | `env_configs/univtac/blind_pick_balls/univtac_blind_pick_balls.yaml` |
| 弹力带，单独批次 | tension_strap | `env_configs/univtac/tension_strap_smoke.yaml` |

前三项是单物体分类并放置到对应托盘，不是参考物体匹配；组合任务才需要 reference/candidate 比较。粗糙度在本分支使用的名称是 `grasp_classify`，不要猜成不存在的 roughness YAML。

六任务正式清单是 `env_configs/ablations/seeds30.json`，seed 0–29。开发 smoke 使用 42000/42001，不混入正式结果。本机分片为 0–14，另一台为 15–29。每个 seed 依次运行上表前六个任务，再进入下一 seed；每条任务在新进程中初始化。两组使用配对 seed 和同一任务条件。

弹力带使用 `seeds30_tension.json` 中已经冻结的逐 seed 目标和保持时间，不重新生成请求，也不固定回旧 12/18 N。当前两组已有把张开的夹爪接近把手的 pre-move，程序从闭爪开始，正常返回后宿主提交 END。

## 6. 环境、路径与单条启动

两个分支均可从仓库独立获取；已有脏工作区不要直接切换覆盖：

```bash
git clone --branch exp/tactile-rgb-opencv --single-branch \
  git@github.com:Apiecehann/TacMem.git TacMem-raw
git clone --branch exp/no-tactile --single-branch \
  git@github.com:Apiecehann/TacMem.git TacMem-no-tactile
```

客户端运行代码生成和程序，HTTP 服务端在 Isaac Sim/Kit 中运行任务。现有 `python -m capx.envs.launch` 会根据 YAML 启动服务端，不需要再手动起第二份服务。这里不使用旧 t-cap 的 `./run_responses`。

本机路径如下，迁移时必须替换为目标机器实际路径：

| 用途 | 本机路径 |
|---|---|
| raw 工作树 | `/mnt/data/ljz/opentacbench/cap-tac-baselines` |
| no-tactile 工作树 | `/mnt/data/ljz/opentacbench/cap-tac-baselines-no-tactile` |
| 客户端 Python | `/mnt/data/ljz/opentacbench/cap-agent0-baselines/client-env/bin/python` |
| Kit Python | `/mnt/data/ljz/opentacbench/conda-envs/UniVTAC/bin/python` |
| OpenTacBench | `/mnt/a/ljz/cap/OpenTacBench` |
| 组合匹配环境 | `/mnt/data/ljz/opentacbench/UniVTAC-composable-tactile-slots` |
| 实验产物根目录 | `/mnt/data/ljz/opentacbench/cap-agent0-baselines` |

本机运行前 source 当前分支的 `env_configs/ablations/runtime.env`。它包含本机路径，不能在另一台机器不经检查就照搬。需要核对：

- `UNIVTAC_ROOT`、`VITAFORGE_ROOT` 指向当前 OpenTacBench；`TACEX_RUNTIME_ROOT` 指向该仓库自带的 `third_party/TacEx`。
- 客户端 Python、服务端 Python、HTTP 依赖 overlay、动态库路径及数据盘缓存路径。
- 前六任务 YAML 的 `api_servers[].python` 是本机绝对路径，迁移需改为目标机器的 Kit Python；弹力带支持 `CAPX_SIM_PYTHON`。
- **组合路径例外**：本文代码基线的 `capx/serving/launch_univtac_server.py::TASK_BACKING["tactile_memory_match"]` 仍写有本机绝对路径。另一台必须把该根路径改为实际组合环境路径，两组一致并记录部署差异；不能只设置 `UNIVTAC_COMPOSABLE_ROOT` 就认为该已提交版本会读取它。
- 组合任务采用 `compact_fixed_pair_scene=true`，初始化本 seed 所需场景，不全量加载无关变体。
- 一个 GPU 上串行运行，显式设置物理 `GPU` 编号。服务端将其映射为单卡 UUID，内部 `cuda:0` 指可见的这一张卡；Kit 已关闭 multi-GPU。
- API 默认端口 8120；同机并发要同时改服务端端口及客户端 `UNIVTAC_SERVER_URL`，本说明的示例不并发。

本机 raw 重量开发 smoke 的命令如下。凭据预先通过环境加载，不写进 YAML、README 或 Git；`CAPX_MODEL_URL` 必须是自己的完整 Responses 地址。

```bash
cd /mnt/data/ljz/opentacbench/cap-tac-baselines
source env_configs/ablations/runtime.env
unset http_proxy https_proxy all_proxy no_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY NO_PROXY
export GPU=2 CAPX_TASK_SEED=42000
: "${CAPX_MODEL_URL:?请先设置完整 Responses URL}"
: "${CAPX_API_KEY:=${OPENAI_API_KEY:-}}"
: "${CAPX_API_KEY:?请先加载 API 凭据}"
export CAPX_API_KEY
export RUN_OUTPUT_ROOT=/mnt/data/ljz/opentacbench/cap-agent0-baselines/raw_weight_manual
mkdir -p "$RUN_OUTPUT_ROOT"
set -o pipefail
(
  umask 0022
  timeout --signal=TERM --kill-after=60s 2400s \
    "$CAPX_BASELINE_PYTHON" -m capx.envs.launch \
    --config-path env_configs/univtac/weight_classify/univtac_weight_classify.yaml \
    --model gpt-6-astra --reasoning-effort high --max-tokens 8192 \
    --output-dir "$RUN_OUTPUT_ROOT"
) 2>&1 | tee "$RUN_OUTPUT_ROOT/terminal_seed_${CAPX_TASK_SEED}_$(date -u +%Y%m%dT%H%M%SZ).log"
```

no-tactile 使用另一个工作树、source 它自己的 runtime.env，并将输出根目录改为独立的 `none_weight_manual`；其余命令和 YAML 相对路径相同。正式运行改为清单内 seed，每 seed 调用一次现有入口。不要额外用 `--total-trials 30`，本模式要求 trials/resume_idx 同为本次 seed；seed 0 也是合法实验。

示例使用 umask 0022 让仿真子进程正常导出并保存 USD。本机之外曾复现 umask 0077 导致灯泡临时 USD 的 permissionToSave=false；不要以忽略 Save 错误解决。

当前 YAML 时间预算（秒）：

| 任务 | 执行阶段 alarm | trial timeout | 进程上限 |
|---|---:|---:|---:|
| 轻重、粗糙度、硬度、盲盒 | 1200 | 1800 | 2400 |
| 组合、灯泡 | 3600 | 4200 | 4800 |
| 弹力带 | 1800 | 1800 | 1800 |

`process_timeout_seconds` 会写入 protocol，但不是整个进程的内置 watchdog；必须像示例一样由外部 timeout/调度器落实，并按任务换成对应秒数。初始 reset 还有独立 HTTP 超时，不能将所有超时都称为“代码执行超时”。外层只管理自己启动的进程，不按进程名批量 kill。

## 7. 日志、成功率、物理步与 token

每次运行位于 `<output-dir>/<task>/seed_<seed>/<UTC时间>_<PID>/`，重点检查：

| 产物 | 内容 |
|---|---|
| `config.yaml`、`protocol.json`、`skills.json` | 配置副本、有效协议/请求、技能快照与 hash |
| `model/request.json`、`response.json`、`request_status.json`（均在 model 下） | 实际模型输入、响应、usage 和生成状态 |
| trial 子目录的 `code.py`、`summary.txt`、MP4 | 生成代码、执行输出、普通相机视频 |
| `actions_sent.jsonl`、`univtac_actions.jsonl` | 客户端动作请求和服务端执行事件 |
| `metrics.json`、`metrics.csv` | 本次指标和失败类型 |
| `private/` | 仿真日志及取得的原生判定，不投递给生成程序 |
| raw 的 `raw_tactile/` | 实际读取的原始触觉及索引 |

文件是否存在取决于执行到哪一步。初始化或模型调用失败时不能虚构 code、视频或原生判分。

| 指标 | 本机口径 |
|---|---|
| 完整任务成功 | `task_completed is True`，由原生任务判定 |
| 代码执行 | `code_executed`、`code_success` 单独记录；正常退出不等于任务成功 |
| Execute_steps | 实际发出的运动/开闭爪/旋转 API 请求次数，含失败请求；不代表物理步 |
| physics_steps | 策略动作对应的服务端物理步跨度之和；缺失动作区间则未知 |
| reset/query/end 步数 | 分别记录，不能混入策略动作物理步 |
| input_tokens / output_tokens | 服务端报告的实际 usage；缺失为 null，保留 provider_usage |
| elapsed_seconds | runner 记录的 elapsed，包含的阶段以代码为准；不是纯物理运动时间 |

同一 API 调用的物理步可以不同，因此不能用 Execute_steps 乘固定倍数代替 physics_steps。已测部分 `measured_physics_steps` 不能冒充完整总量。只有核对任务实际仿真频率后才能由物理步换算模拟时间。

每任务自动保存 `runs.csv` 和 `aggregate.json`。汇总会列重复 seed 和未知值，重复尝试必须明确选定使用哪次，不能挑成功结果。原版 aggregate 在缺判分/重复/未跑满时可能不输出最终 success_rate。

人工论文表按约定选定的 30 次尝试计算成功数/30；未取得判分的已运行尝试不算成功，并单列基础设施失败。未运行案例保持“未运行”。若另报已判分成功率，必须写明其不同分母。初始化、LLM 未生成、代码异常、动作失败及原生判分失败分别统计，不能全部归因于触觉。补测 LLM 未生成需保存原记录和替代关系，两组使用同一规则。

## 8. 真机重量分类如何沿用

这两个分支记录的是仿真接线，不会直接控制 Franka＋Robotiq＋Xense Photon。真机应复用已经工作的运动、位姿查询、录像与判分实现，再落实相同的信息边界：

- raw 开放当前 Photon 实际可提供的左右 RGB、marker RGB 和完整 depth，记录 SDK 版本、单位、参考帧、传感器身份和时间。SDK 未提供的通道标缺失，不伪造。原始 marker 坐标不属于本次 raw 契约。
- no-tactile 不绑定任何触觉接口，动作返回、异常、日志和技能也不带触觉观测或判断。保留相同普通视觉和允许的机器人/物体位置。
- 两组冻结运动控制模式和技能。若底层夹持由触觉闭环自动调整，必须显式记录并对齐，否则不能称为只有观测不同。
- 真机已标定的“左右 marker 平均变化向量长度相加”及阈值 2.6916 属于表征组；不要写进 raw/no-tactile 的 prompt、技能或隐藏控制器。
- raw 可保留动作前后采样建议，不能提供现成重量公式和阈值。no-tactile 仍要尝试抓取放置，分类不确定时按同一规则随机选一个合法类别。
- 真机的参考帧和采样动作以真实标定协议为准，不能套用仿真 depth 数值、左右轴向符号或分类阈值。
- 真机没有仿真 physics_steps，应报告实际动作执行时间；控制周期数只有在频率固定且记录完整时才可换算时间。API 调用数仍只作辅助。
- 配对物体/配重、初始姿态和预算，均衡轻重；真实重量/类别只供离线判分，不提供给生成代码。固定技能期间不根据测试结果更新。

本 README 只整理现有流程，不声称 raw/no-tactile 已迁移到真机，也不包含新的实验结果。
