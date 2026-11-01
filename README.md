


# CaP-X

### A Framework for Benchmarking and Improving Coding Agents for Robot Manipulation

[Project Page](https://capgym.github.io/) &ensp;|&ensp; [Paper](https://arxiv.org/abs/2603.22435) 

**Max Fu<sup>&#42;,1,2</sup>, Justin Yu<sup>&#42;,2</sup>, Karim El-Refai<sup>&#42;,2</sup>, Ethan Kou<sup>&#42;,2</sup>, Haoru Xue<sup>&#42;,1,2</sup>,
Huang Huang<sup>3</sup>, Wenli Xiao<sup>4</sup>, Guanzhi Wang<sup>1</sup>, Fei-Fei Li<sup>3</sup>, Guanya Shi<sup>4</sup>, Jiajun Wu<sup>3</sup>,
Shankar Sastry<sup>2</sup>, Yuke Zhu<sup>1</sup>, Ken Goldberg<sup>&dagger;,2</sup>, Jim Fan<sup>&dagger;,1</sup>**

<sup>1</sup>NVIDIA &ensp; <sup>2</sup>UC Berkeley &ensp; <sup>3</sup>Stanford University &ensp; <sup>4</sup>Carnegie Mellon University

<sup>&#42;</sup>Equal contribution &ensp; <sup>&dagger;</sup>Equal advising



---
**CaP-X** is an open-access framework for systematically studying Code-as-Policy agents in robot manipulation. It consists of four components:


| Component      | What it does                                                                                                                                                                                   |
| -------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **CaP-Gym**    | Interactive Gymnasium environments where agents control robots by generating Python code that composes perception and control primitives. 39 tasks across Robosuite, LIBERO-PRO, and BEHAVIOR. |
| **CaP-Bench**  | Systematic benchmark evaluating coding agents across abstraction levels, interaction modes, and visual grounding modalities. 8 tiers (S1-S4 single-turn, M1-M4 multi-turn).                    |
| **CaP-Agent0** | Training-free agentic framework with multi-turn visual differencing, auto-synthesized skill libraries, and parallel ensembled reasoning.                                                       |
| **CaP-RL**     | Reinforcement learning on the coding agent via GRPO, using environment rewards to post-train language models. Transfers from sim to real with minimal gap.                                     |


---

## 本分支：完整代码库与修复技能库（2026-10-29）

**实现代码补丁闭环框架，正在实验触觉key是否有用。** 当前验证任务为 OpenTacBench
Franka＋Shadow Hand＋单指 DIGIT 钢琴。下面是本分支的实现与实验状态，不表示已在其他任务上验证。

### 何时查询记忆

新 episode 优先读取兼容的合格完整程序，否则首次生成完整策略模块；执行期间持续记录公开触觉、动作、
实际手部状态与函数调用。**只有发生已有公开检查认可的错误、准备修复代码时才查询历史**，
正常执行与 episode 结束均不额外请求 LLM 整理经验。

```text
当前公开错误 → 定位函数调用与触觉窗口 → 兼容历史检索
            → 当前代码/错误/证据 + 历史修正 → LLM补丁
            → 校验并执行 → 原检查重检 → 保存修复经历与代码版本
```

未命中时，LLM仍用当前错误、源码和当前公开证据修复。代码从 run_piano 入口重新进入，
根据宿主的公开提交记录恢复；已提交音不重弹，当前音先读取实际手部与触觉状态，不能依靠
生成代码自己的计数，也不能把请求API期间的墙钟时间算作物理保持时间。

### 触觉 key 的表示设计

即时与时序表示并行存在，共同关联同一接触片段；**时序 key 不替换冻结的5D力估计器**。
这里的 key 指检索向量，不是琴键坐标。

| 表示 | 维数 | 输入与构造 | 用途 |
|---|---:|---|---|
| instantaneous_5d | 5 | 有效像素上绝对深度形变均值、形变超过0.01/0.05/0.2 mm的像素比例，以及超过0.05 mm比例的时间变化率 | 保留即时测力与触觉检索；力模型和特征顺序冻结 |
| window4 | 64 | 最近0.5秒的16帧，每帧使用均值＋三个像素比例；按冻结feature_mean/feature_std标准化，时间展开为16×4，再L2归一化 | 不经学习的时序检索基线 |
| tactile | 32 | 16帧深度相对参考面的有符号形变与有效覆盖图，降采样至24×32；冻结CNN＋GRU编码，再L2归一化 | 触觉轨迹检索 |
| tactile_action | 32 | 同上，额外输入每帧4D动作目标和4D实际状态；XYZ相对窗口首帧实际位置，使用冻结归一化 | 结合接触和动作过程的检索 |

实际状态字段顺序为 wrist_x_offset_m、wrist_y_m、wrist_z_m、index_flexion_rad。
四物理步一次动作时观测为60 Hz；时序窗口必须按物理时间选择16个30 Hz采样点，
不能直接取最后16条。允许从同episode的前一动作阶段补足真实历史，但不跨reset，
不插值、不补帧、不把缺失状态填成0。历史不足、缺失有效掩码、传感器或参考深度不兼容时
返回不可用，不伪造向量；时序通路不可用不影响原5D控制通路。

编码器通过 contract 和 encode(window) 接口接入，保留向量、版本、质量及原始样本来源。
现有适配器封装 FrozenTrajectoryEncoder.encode_window；后续编码器可以输出不同维数，
只需使用独立的编码器/预处理版本，不能与旧空间直接比较距离。

### 从 key 找到可执行经验

- `programs/` 保存完整程序，不进行语义搜索或历史函数拼接；`repair_skills/` 独立保存建议、
  数值、适用条件、错误窗口和局部验证。来源代码只用于追溯，检索技能不要求加载该程序。
- Skill Library 使用同一经验库；实际接受的代码修改均可检索，失败和未验证结果如实附带。
  完整公开轨迹保留为日志，不把每帧都建成可检索条目。
- 共同候选须在当前错误前已保存代码修改、来自其他 episode、未主动撤销且没有明确条件冲突。
  语言 key 不含触觉数值或诊断，使用固定 CPU multilingual-e5-small；触觉 key 固定一种
  表示，当前默认 tactile_action，检查传感器、预处理、窗口和编码器契约，不动态混用空间。
- `CAPX_SHADOW_SKILL_RETRIEVAL=language|tactile|joint` 选择通道；joint 两路各取前五个
  不同修正，等权 RRF(c=60)，最多返回两条。触觉缺失时显式回退语言，tactile 不回退。
  分数不是成功概率；没有标定数据就不设置信阈值。
- 查询窗口严格截止于错误事件，保留观测年龄和来源；修复后的数据只用于验证。
  LLM收到当前源码一份、当前公开证据和技能建议，不上传历史完整程序、完整轨迹或向量。
- 语言查询与历史被搜索文本统一为 `public_error_template.v2` 的修复前错误描述；校验
  context/text 的版本与 SHA256。先从搜索索引排名，再加载命中的技能正文；修正建议、
  修正数值和验证详情不参与文本编码。旧格式 key 保留但不混入新版语言检索。
- 即时5D控制和冻结力估计器不变。真实力与原生逐音判分不进入技能、编码或修复请求。
  宿主仅用私有判分筛选完整代码：同次运行、同源码及参数版本至少成功5音且正常收尾，
  才能直接复用。这个门槛不限制技能保存或局部验证。

已完成定向 CPU 验证及一条空库 GPU smoke：首次程序发生公开错误后，32D 触觉查询和一次完整程序修复均执行；
首音原生成功，但生成代码把测力质量写成 `held`，被原检查中断，最终 1/6、无 END。
技能草稿格式不合格而拒绝，完整代码未晋升；不能据此宣称有效历史技能复用。已补充字段格式提示，尚未再次 GPU 验证。
新配置、数据格式、测试边界与日志见
[独立代码与修复技能说明](docs/opentac-shadow-piano-code-memory.md)。

后续 v2 语言查询 smoke 未跑 CPU 测试：空库下错误查询 key 与技能、搜索索引中保存的 key 完全一致。
结果仍为 1/6、无 END；此次 quality 格式正确，停止于公开保持未确认/按压超时。
草稿数值来源标注不合格而拒绝，尚无历史命中或技能收益证据。完整记录见上述文档。

后续宿主来源绑定 smoke：经验草稿通过、完整补丁实际执行；因修复后的 XY 稳定检查仍失败，
记录为 `executed_failed`（0/6、无 END），不再因格式直接丢弃。条件文本可规范化，
唯一的字面量动作循环上限由宿主依据源码与实际执行行绑定；无法确认的数值仍待验证。
此次补丁使用命名常量 `ARRIVAL_LIMIT`，尚不在字面量绑定范围，原声明保留且未晋升。
按用户要求未跑 CPU 测试；该条不能证明有效技能复用。

2026-10-29 起简化经验门槛：不再要求逐数值来源匹配或局部成功才能保存、检索。
数值保留为报告的设置／拟议修正；原检查通过、后续新错误分别记录。正常结束不调用整理 API。
完整代码直接复用的同版 5/6 与正常收尾门槛不变。上述旧 smoke 结果保留，未重标为成功。

简化后的单条 GPU smoke 已完成：一次补丁实际执行，失败经验已保存且可检索；
原生3/6、公开提交2、无END，整段代码未晋升。没有历史命中，不能声称技能复用有效。
本轮未跑 CPU 测试，详见[实验账本](docs/opentac-shadow-piano-experiment-ledger.md)。

### 补丁与验证边界

当前通用证据配置使用 `shadow_piano_repair_scope: public_contract`：提供完整源码，允许返回
完整修正版、重组辅助函数与控制流程，不再按两个函数或保护变量的AST写法拒绝补丁。
仍检查Python语法、入口、公开接口与数据隔离；原生任务、力模型、力目标和动作/释放硬边界不变。
旧 `release_regions_v1` 协议保留用于复现历史局部释放实验。

公开异常、PIANO_STOP、逐音报告中的abort/无有效接触/保持未确认，以及未完成公开尝试链就
正常返回，均触发修复检查。兼容旧PIANO_NOTE key=value日志，报告失败时在下一动作前中断。
原生终止、全部尝试已提交或预算耗尽时无法继续修复，明确记录原因；不借助私有评分触发修复。
当前配置每episode最多1次修复，每次300秒、8192输出token，API清除代理；没有无条件结束后整理。
每次补丁、执行边界、候选验证和触觉检索分别归档。旧配置不显式设置时仍默认一次。

修复前错误永久保留；修复后从明确执行边界检查新函数是否实际调用、原检查是否通过及
是否出现新错误。**局部释放修复有效**可以作为检索经验，但不自动获得**整段策略可直接复用**
资格。原生终止后的合法提交仅用于生命周期收尾，不冒充释放验证或官方成功。

本轮已验证3300经历被3306检索，历史释放函数进入实际补丁；3306原生6/6，
其修正版在3301零API执行得到5/6，原释放错误未复发。无历史3306组因补丁哈希漏抄一位而
未执行，且也提出了合理计时修复；因此尚不能据此宣称触觉key提高了修复成功率。
相同首音产生了距离为0的容易匹配，仍需更多接触过程及语言检索对照。上述结果来自旧区域修复协议，
不能当作当前宽松审查配置的效果。

入口配置：[play_piano_shadow_digit_evidence_memory.yaml](env_configs/opentac/play_piano_shadow_digit_evidence_memory.yaml)，
在父仓库使用现有 ./run_responses。设置CAPX_SHADOW_TASK_SEED、CAPX_SHADOW_EVIDENCE_ROOT、
CAPX_SHADOW_TRAJECTORY_RUN及显式GPU；模型目录需包含cache/manifest.json、cache/normalization.json
和models/{tactile,tactile_action}/seed_0/best.pt。权重、连续触觉帧、视频及私有审计保留在数据盘，
不随Git上传；另一台服务器需另行配置这些路径。

实现：[编码器接口](capx/memory/trajectory/model.py)、[采样与预处理](capx/memory/trajectory/data.py)、
[公开证据索引](capx/integrations/opentac_shadow_online_skills.py)、
[错误检索与代码修复](capx/integrations/opentac_shadow_code_memory.py)。
详情见[代码记忆设计](docs/opentac-shadow-piano-code-memory.md)和
[实验账本](docs/opentac-shadow-piano-experiment-ledger.md)。账本中的服务器绝对路径用于定位原始产物，
在其他机器上需要映射或单独复制。

## Installation

CaP-X uses [uv](https://docs.astral.sh/uv/) for dependency management. Requires **Python 3.10** and a **CUDA-capable GPU**.

```bash
git clone --recurse-submodules https://github.com/capgym/cap-x && cd cap-x

# Or if already cloned without --recurse-submodules:
git submodule update --init --recursive

# Install uv (if not present)
curl -LsSf https://astral.sh/uv/install.sh | sh

uv python install 3.10 && uv venv -p 3.10

# Base install
uv sync
```

### Simulator-specific setup

Pick **one** simulator family to install, as Robosuite (1.5.0) and LIBERO (`robosuite==1.4.0`) would be in conflict.

#### Robosuite

```bash
uv sync --extra robosuite
```

#### LIBERO-PRO

LIBERO requires a **separate virtual environment**.

```bash
uv venv .venv-libero --python 3.12
source .venv-libero/bin/activate
uv sync --active --extra libero --extra contactgraspnet
```

See [docs/libero-tasks.md](docs/libero-tasks.md) for running any of 130+ LIBERO tasks.

#### BEHAVIOR (Isaac Sim)

BEHAVIOR tasks run on NVIDIA Isaac Sim via OmniGibson. Requires Python 3.10 and CUDA 12.x.

```bash
cd capx/third_party/b1k
./uv_install.sh --dataset          # installs OmniGibson, Isaac Sim, BDDL, cuRobo, and downloads assets
cd ../../..                        # back to repo root

# Post-install fix — copy cuRobo JIT headers (run with b1k venv active)
source capx/third_party/b1k/.venv/bin/activate
cp capx/third_party/curobo/src/curobo/curobolib/cpp/*.h \
   $(python -c "import sysconfig; print(sysconfig.get_path('purelib'))")/curobo/curobolib/cpp/
```

> The `--dataset` flag downloads robot assets, BEHAVIOR-1K scene/object assets, and 2025 challenge task instances. You will be prompted to accept the NVIDIA Isaac Sim EULA and BEHAVIOR dataset license. To auto-accept, add `--accept-dataset-tos`.

For headless servers:
```bash
sudo apt-get update && sudo apt-get install -y libegl1 libgl1
# Remove duplicate Vulkan ICD if present (causes segfault on multi-GPU systems)
sudo rm -f /usr/share/vulkan/icd.d/nvidia_icd.json
```

See [docs/behavior-tasks.md](docs/behavior-tasks.md) for task details and expected baselines.

### Optional extras

```bash
uv sync --extra verl             # RL training with VeRL/GRPO
uv sync --extra contactgraspnet  # Contact-GraspNet grasp planning
uv sync --extra curobo           # cuRobo GPU-accelerated IK & motion planning (requires CUDA)
```

## Quick Start

### 1. Perception servers (auto-launched)

Perception servers (SAM3, ContactGraspNet, PyRoKi) are **auto-launched** by the YAML config when you run an evaluation. No manual setup required for most configs.

> **SAM3 authentication:** SAM3 weights require HuggingFace access. Request access at the [SAM3 repo](https://github.com/facebookresearch/sam3), then authenticate locally with `huggingface-cli login`. Weights are cached after first download.

To pre-launch servers (e.g. for sharing across multiple eval runs):

```bash
# Start SAM3 + GraspNet + PyRoKi with automatic GPU allocation
uv run --no-sync --active capx/serving/launch_servers.py --profile default
```

Use `--dry-run` to preview the allocation. Other profiles:

```bash
--profile full      # All perception servers (SAM3, GraspNet, PyRoKi, OWL-ViT, SAM2)
--profile minimal   # PyRoKi only (for oracle/privileged evals)
```

### 2. Set up an LLM proxy

The evaluation harness queries an LLM through a local proxy that exposes an OpenAI-compatible API.

```bash
# OpenRouter (get a key at openrouter.ai/keys)
echo "sk-or-v1-your-key-here" > .openrouterkey
uv run --no-sync --active capx/serving/openrouter_server.py --key-file .openrouterkey --port 8110
```

> **Note:** `.openrouterkey` are git-ignored. The default server URL in configs is `http://127.0.0.1:8110/chat/completions`. 

See [docs/configuration.md](docs/configuration.md) for all provider options (OpenRouter, vLLM, custom).

### 3. Run evaluation

```bash
# Robosuite: single-turn benchmark (100 trials, 12 parallel workers)
uv run --no-sync --active capx/envs/launch.py \
    --config-path env_configs/cube_stack/franka_robosuite_cube_stack.yaml \
    --model "google/gemini-3.1-pro-preview"

# Robosuite: multi-turn with visual differencing
uv run --no-sync --active capx/envs/launch.py \
    --config-path env_configs/cube_stack/franka_robosuite_cube_stack_multiturn_vdm.yaml \
    --model "google/gemini-3.1-pro-preview"

# LIBERO-PRO: spatial task (requires .venv-libero)
source .venv-libero/bin/activate
uv run --no-sync --active capx/envs/launch.py \
    --config-path env_configs/libero/franka_libero_spatial_0.yaml \
    --model "google/gemini-3.1-pro-preview"

# BEHAVIOR: R1Pro radio pickup (20 trials) — requires b1k venv
source capx/third_party/b1k/.venv/bin/activate
OMNI_KIT_ACCEPT_EULA=YES OMNIGIBSON_HEADLESS=1 \
uv run --no-sync --active capx/envs/launch.py \
    --config-path env_configs/r1pro/r1pro_pick_up_radio.yaml \
    --model "google/gemini-3.1-pro-preview"

# Interactive Web UI
uv run --no-sync --active capx/envs/launch.py \
    --config-path env_configs/cube_stack/franka_robosuite_cube_stack.yaml \
    --web-ui True
# Open http://localhost:8200

# Regression tests
./scripts/regression_test.sh quick    # 10-trial smoke test (~30s)
./scripts/regression_test.sh test1    # Full single-turn (~3 min)
```

> **Tip (BEHAVIOR):** Isaac Sim uses `OMNIGIBSON_GPU_ID` (not `CUDA_VISIBLE_DEVICES`) to select the GPU. For best performance on multi-GPU systems, run perception servers on a separate GPU (e.g. `OMNIGIBSON_GPU_ID=0` for the eval, and pre-launch SAM3/GraspNet with `CUDA_VISIBLE_DEVICES=1`). Set `OMNI_KIT_ACCEPT_EULA=YES` and `OMNIGIBSON_HEADLESS=1` for headless evaluation.

---

## Documentation

| Guide | Contents |
| ----- | -------- |
| [Adding Environments](docs/adding-environments.md) | Creating simulators, task environments, YAML configs |
| [Adding APIs](docs/adding-apis.md) | Implementing and registering new robot control APIs |
| [Configuration](docs/configuration.md) | YAML format, CLI flags, LLM provider setup |
| [LIBERO-PRO Tasks](docs/libero-tasks.md) | Setup, running any of 130+ LIBERO tasks, suite reference |
| [BEHAVIOR Tasks](docs/behavior-tasks.md) | Setup, R1Pro tasks, expected baselines, environment variables |
| [Development](docs/development.md) | Testing, linting, LIBERO/GraspNet setup, checkpoints, known issues |
| [Real-World Franka Panda Bringup](docs/real-franka.md) | Bringup with robots_realtime, real-robot QuickStart |
| [RL Training](docs/rl-training.md) | CaP-RL with GRPO/VeRL, sim-to-real transfer |
| [Skill Library Compilation](scripts/skill_library_compilation/README.md) | Analyze eval outputs, compile reusable skill libraries |

---

## Citation

```bibtex
@article{fu2025capx,
  title     = {{CaP-X}: A Framework for Benchmarking and Improving Coding Agents for Robot Manipulation},
  author    = {Fu, Max and Yu, Justin and El-Refai, Karim and Kou, Ethan and Xue, Haoru and Huang, Huang and Xiao, Wenli and Wang, Guanzhi and Li, Fei-Fei and Shi, Guanya and Wu, Jiajun and Sastry, Shankar and Zhu, Yuke and Goldberg, Ken and Fan, Jim},
  journal   = {arXiv preprint arXiv:2603.22435},
  year      = {2025},
  url       = {https://arxiv.org/abs/2603.22435}
}
```

## License

This project is released under the [MIT License](LICENSE).
