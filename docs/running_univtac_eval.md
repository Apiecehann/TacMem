# UniVTAC Eval

LLM 代码生成评测，基于 UniVTAC 触觉操控 benchmark（8 个接触密集型任务）。

## 架构

```
LLM → caP-X Launcher → UniVTAC Server (Isaac Sim + GelSight + cuRobo IK)
  ↑                     │
  └── stdout/stderr ────┘ (multi-turn feedback)
```

UniVTAC Server 是一个自包含的单体服务（仿真 + 感知 + IK + 触觉），不需要 sam3 / graspnet / pyroki。

---

## 启动服务

### 0. 前置依赖

1. 将 [UniVTAC](https://github.com/univtac/UniVTAC) 克隆到本仓库的**同级目录**（即 `cap-x/../UniVTAC`）：
   ```bash
   cd <cap-x 的父目录>
   git clone https://github.com/univtac/UniVTAC.git
   ```
2. 若 UniVTAC 不在同级目录，用环境变量 `UNIVTAC_ROOT` 指向其根目录。
3. 需在 UniVTAC 的 conda 环境（名 `UniVTAC`，含 Isaac Sim）中运行。

### 1. UniVTAC Server（必须先启动）

```bash
conda activate UniVTAC
cd <cap-x 仓库根目录>
bash scripts/launch_univtac_server.sh <task> 8120 0
```

任务名：`insert_hole` `insert_tube` `insert_hdmi` `lift_bottle` `lift_can` `pull_out_key` `put_bottle_in_shelf` `grasp_classify`

### 2. OpenRouter Server（如需要）

```bash
./.venv/bin/python3 capx/serving/openrouter_server.py --key-file .openrouterkey --port 8110
```

---

## 6 种配置

每个任务有 6 个 YAML，对应 3 种模式 × 2 种 API：

| # | 模式 | API | YAML |
|---|---|---|---|
| 1 | Single | Tactile | `univtac_<task>.yaml` |
| 2 | Multi | Tactile | `univtac_<task>_multiturn.yaml` |
| 3 | Multi+VDM | Tactile | `univtac_<task>_multiturn_vdm.yaml` |
| 4 | Single | Motion | `univtac_<task>_no_tactile.yaml` |
| 5 | Multi | Motion | `univtac_<task>_no_tactile_multiturn.yaml` |
| 6 | Multi+VDM | Motion | `univtac_<task>_multiturn_vdm_no_tactile.yaml` |

VDM = Visual Differencing Model，使用 VLM 比较前后帧图像。  
VDM 模型必须支持多模态（看图），与代码生成模型是分开的。
`--model` 指定代码模型，`--visual-differencing-model` 指定 VDM 模型。
默认 VDM 模型是 `google/gemini-3.1-pro-preview`（国内不可用），
需要显式指定可访问的 VLM（如 Qwen）。

API 对比：

| 函数 | Tactile | Motion |
|---|---|---|
| `goto_pose` `move_delta` `open/close_gripper` `rotate_gripper` `get_object_pose` `check_success` | ✅ | ✅ |
| `get_tactile_data` `detect_contact` `detect_slip` `sample_grasp_pose` | ✅ | |

---

## 执行命令（以 lift_can 为例）

```bash
MODEL="openrouter/deepseek/deepseek-v4-pro"
TASK=lift_can

# 1. Single + Tactile
./.venv/bin/python3 capx/envs/launch.py \
    --config-path env_configs/univtac/$TASK/univtac_${TASK}.yaml \
    --model "$MODEL" --total-trials 10

# 2. Multi + Tactile
./.venv/bin/python3 capx/envs/launch.py \
    --config-path env_configs/univtac/$TASK/univtac_${TASK}_multiturn.yaml \
    --model "$MODEL" --total-trials 10

# 3. Multi+VDM + Tactile (VDM 需要多模态模型，指定 --visual-differencing-model)
./.venv/bin/python3 capx/envs/launch.py \
    --config-path env_configs/univtac/$TASK/univtac_${TASK}_multiturn_vdm.yaml \
    --model "$MODEL" \
    --visual-differencing-model "openrouter/qwen/qwen3-235b-a22b" \
    --total-trials 10

# 4. Single + Motion (无触觉对照)
./.venv/bin/python3 capx/envs/launch.py \
    --config-path env_configs/univtac/$TASK/univtac_${TASK}_no_tactile.yaml \
    --model "$MODEL" --total-trials 10

# 5. Multi + Motion
./.venv/bin/python3 capx/envs/launch.py \
    --config-path env_configs/univtac/$TASK/univtac_${TASK}_no_tactile_multiturn.yaml \
    --model "$MODEL" --total-trials 10

# 6. Multi+VDM + Motion (VDM 需要多模态模型)
./.venv/bin/python3 capx/envs/launch.py \
    --config-path env_configs/univtac/$TASK/univtac_${TASK}_multiturn_vdm_no_tactile.yaml \
    --model "$MODEL" \
    --visual-differencing-model "openrouter/qwen/qwen3-235b-a22b" \
    --total-trials 10
```

---

## 批量运行

```bash
bash scripts/run_all_univtac.sh "openrouter/deepseek/deepseek-v4-pro" 10 0
```

---

## 结果

输出目录 `outputs/<model>/univtac_<task>/`：

```
trial_01_sandboxrc_0_reward_1.000_taskcompleted_1/
├── code.py                 # LLM 生成的代码
├── summary.txt             # 完整日志
├── video_combined.mp4      # 操作录像
└── prompts_and_responses/  # prompt 历史
```

命名规则：`reward=1.000` + `taskcompleted=1` = 成功。

```bash
# 快速统计
cd outputs/<model>/univtac_<task>/
echo "$(ls -d trial_*_taskcompleted_1 2>/dev/null | wc -l) / $(ls -d trial_* 2>/dev/null | wc -l)"
```

---

## 推荐模型

| 模型 | CLI |
|---|---|
| DeepSeek R1 | `openrouter/deepseek/deepseek-r1` |
| DeepSeek V3 | `openrouter/deepseek/deepseek-chat-v3-0324` |
| DeepSeek V4 Pro | `openrouter/deepseek/deepseek-v4-pro` |

---

## 论文 Baseline

| Method | Success |
|---|---|
| ACT (vision) | 30.9% |
| ACT + VITaL | 40.5% |
| ACT + UniVTAC | 48.0% |
| LLM Code Gen | TBD |

---

## 常见问题

- **Server 起不来**：`nvidia-smi` 查 GPU，`kill $(pgrep -f launch_univtac)` 清残留
- **Reset 超时**：首次 reset 含 cuRobo 编译，等 1-2 分钟
- **IK 失败**：别用手算四元数，用 `rotate_gripper()`；目标 z > 0.05m
- **Object not found**：`"can"` 匹配 `can_d4/d5/d6`，`"prism"` 匹配 `prism*`
- **403 ToS**：Google/Anthropic 封机器人控制，换 DeepSeek
