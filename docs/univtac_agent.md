# UniVTAC Agent 接入（交互式 coding agent）

让 codex / cursor / claude code 等交互式 coding agent 直接操作 UniVTAC 仿真，
复用 cap-x 现有的触觉 API（`capx.integrations.tactile.*`），**不经过 cap-x
的评测 harness**。

## 架构

```
coding agent (codex / claude code / ...)
   │  从 univtac_skill/ 目录启动
   │  只 import: from robo import goto_pose, get_tactile_data, get_observation, ...
   ├─ HTTP :8120 ──────────────┐    ┌─ HTTP :8114（get_object_pose 的 SAM3 分割）
   ▼                           ▼    ▼
UniVTAC server            SAM3 server
(launch_univtac_server.sh, (launch_sam3_server.sh, 文本分割 + 深度反投影)
 Isaac Sim + GelSight + cuRobo)
```

- `univtac_skill/robo.py` —— 薄 client，复用现有
  `UniVTACPerceptionApi`（非特权版），一行 API 代码都不用重写。
  `get_object_pose` **永久非特权**：不做真值读取，走 SAM3 文本分割 +
  深度反投影（噪声质心）；无特权/非特权切换开关。另有只读的
  `get_ee_pose()`（末端位姿，诊断用：验证 move_delta 是否真的执行、
  检测漂移、下潜前重新锚定，不用于对象对准——同样不进 `functions()`）。
- `univtac_skill/tools/` —— 任务无关的**原语**（warmup / 逐步记录 /
  快照 / 裁剪放大 `zoom`（像素 box 或 center）与分割引导裁剪
  `segmented_zoom`（SAM3 2D mask，客户端裁剪——服务器对相机图透传，
  无隐藏更高分辨率）），agent 用 `from tools import ...` 复用，避免每次
  重写；运动循环（下降/提拉/探针）、状态读取、收尾由 agent 自己写
  （tools 只保留 agent 实际使用的原语，曾提供的 stepwise / object_z /
  read_state / finish 因零使用已删除）。用法见 SKILL.md「Reusable
  helpers」和「Resolution matching」。
- `univtac_skill/SKILL.md` —— agent 手册（信息边界 / warmup / 控制流程 /
  安全规则），挂到 agent 的 skill 系统。
- `univtac_skill/config.json` —— 任务名、任务描述与 server URL（可用
  环境变量 `UNIVTAC_TASK` / `UNIVTAC_TASK_DESCRIPTION` /
  `UNIVTAC_SERVER_URL` 覆盖）。`task_description` 是 agent 的目标，
  即“任务”本身；agent 也通过 `robo.TASK_DESCRIPTION` 读取。

与 cap-x 评测的隔离：`get_observation()` 只存在于 API 类的方法层，**没有
注册进 `functions()`**，所以 cap 评测的 LLM 既看不到也调不到它；反过来，
`check_success` 对评测 LLM 是 `_prompt_hidden`，但 agent 可以正常调用。

## 快速开始

1. **启动两个 server**：

   ```bash
   bash scripts/launch_sam3_server.sh 1 8114          # SAM3，默认 GPU 1、端口 8114
   # 另开一个终端：
   conda activate UniVTAC
   bash scripts/launch_univtac_server.sh insert_HDMI 8120 0 1          # UniVTAC，默认 GPU 0、端口 8120、seed 1
   ```

   SAM3 server 跑在 cap-x `.venv`（torch 2.8.0+cu128），UniVTAC server
   跑在 UniVTAC conda 环境；两者都带 supervisor 循环自动重启。

2. **启动 coding agent**，工作目录设为 `univtac_skill/`，并让它读取
   `SKILL.md` 再动手：

   ```bash
   cd univtac_skill
   # codex / claude code / cursor 在此目录启动，先读 SKILL.md
   ```

   agent 首次运行会做一次触觉 warmup（无接触基线、首次接触深度），
   结果保存在 `univtac_skill/memory/warmup_<task>.json`（已 gitignore，
   不会提交）。

   启动时给 agent 的提示词（其余规则都在 `SKILL.md` 里，不重复）：

   ```
   You are operating a Franka robot arm with GelSight Mini tactile sensors in the UniVTAC simulation (task: <TASK>).

   Your goal is config.json's `task_description` (robo.TASK_DESCRIPTION).

   Read univtac_skill/SKILL.md and follow it exactly. Then complete the task through the robo API.
   ```

   把 `<TASK>` 换成当前任务名即可。SKILL.md 是唯一的行为规范，提示词只
   做四件事：交代身份与传感器、指定任务、指定目标所在（config.json 的
   `task_description`）、指向 SKILL.md。

## 新会话前必须重置场景

agent 没有 `reset()`：服务器状态（机械臂位姿、场景、episode 目录）跨会话
持续存在。**每次开新的 agent 会话前，操作员必须重置场景**——否则新会话
连上的是上一个会话的残留状态：录像混进旧 episode 目录（新旧视频同一
时间戳、无法分辨）、warmup 与真实初始状态不符、agent 从漂移位姿起步。
上一次失败会话（20260827_100604）就是这样被污染的：场景没重置，新会话
的视频与旧会话混在同一个 episode 目录。

重置方式（任选其一）：

```bash
curl -s -X POST http://127.0.0.1:8120/reset            # 重置并开新 episode 目录
# 或重启服务器：
bash scripts/launch_univtac_server.sh insert_HDMI 8120 0 1
```

`/reset` 会打开新的 episode 目录（`<时间戳>`），录像、动作日志随之分家；
重启服务器效果相同。**不做这个步骤就开始新会话 = 在污染的环境里跑**，
失败结论不能归因于 agent。

## 切任务

改 `univtac_skill/config.json` 的 `task`、`task_description` 与
`sam3_prompts` 字段，并重启对应任务的 server。切换任务后 agent 会做新的
warmup（按任务名存不同的 memory 文件）。

`task_description` 是给 agent 的目标（只含目标 + 对象名，不含策略、不含
ground-truth 答案）；`sam3_prompts` 是 `get_object_pose` 用的 SAM3 分割
提示词（键 = `task_description` 里的对象名词，值 = 标定过的判别性短语，
例如 bare `"prism"` 会误分割 socket 体，`"black connector"` 才可靠）。
**两个字段必须一起切**，否则 agent 用 bare 名词分割不到正确对象。
各任务的描述如下，切任务时复制到 config.json 即可（`sam3_prompts`
需按任务单独标定）：

| task | `task_description` |
|---|---|
| `insert_hdmi` | Insert the prism (HDMI plug) straight down into the slot (HDMI socket) until seated. The prism is already grasped in your gripper, held just above the slot; keep the gripper closed throughout and trust the grasp — do not re-align from perceived positions (they are noisy SAM3 centroids). Descend straight down in small steps; when the plug stops advancing downward, it is jammed on the socket rim — lift at most a couple of steps, nudge sideways 1-3 mm, and retry. Never keep pressing at a jam: if it does not advance after one small retry, lift back clear of the rim, re-align, and try again — pressing a wedged plug pries it out of the light grasp and it falls flat beside the socket. When it stops advancing and holds position, confirm seating with a small sideways probe. Once seated, lift the gripper a few centimeters — the plug stays wedged in the socket, and the gripper lifting clear is the final confirmation of a proper seat — but only if the plug is visually confirmed standing in the slot afterwards: far-plane fingers after the lift also mean the plug fell out during the descent and lies flat on the base. Zoom the plug region to check standing-in-slot vs lying-flat before trusting the lift test. After lifting, verify with check_success() — the tactile signature alone cannot distinguish a proper seat from the plug coming out with the gripper (both read as light grasp); do not trust the signature alone. |
| `insert_hole` | Insert the grasped prism into the tilted hole until it is fully seated. |
| `insert_tube` | Insert the grasped prism into the tilted slot until it is fully seated. |
| `lift_bottle` | Grasp the bottle leaning against the wall, rotate it upright, and release it. |
| `lift_can` | Grasp the can lying on its side, rotate it upright so it stands on the table, and release it. |
| `pull_out_key` | Grasp the key, rotate it in the slot to unlock, and pull it straight out. |
| `put_bottle_in_shelf` | Lift the bottle, maneuver it past the shelf lip, place it into the shelf bay, and release it. |
| `grasp_classify` | Grasp the prism, use tactile sensing to determine whether its surface texture is rough or smooth, and place it on the matching pad: Rough → orange pad, Smooth → green pad. |

## 信息边界（防作弊）

`SKILL.md` 里写死了约束：agent 只许读 `univtac_skill/` 目录和 API 返回，
**禁止读 `capx/`、`env_configs/`、`task_config/`**。对 `grasp_classify`
这类含 ground-truth（rough/smooth texture）的任务尤其重要——答案只能来自
agent 自己的触觉观察。若需严格隔离，可把 agent 放进容器，只挂载
`univtac_skill/` 目录。

## 执行过程录像回看

agent 跑完之后，可以在本地回看它的执行过程（动作级缩时回放）。机制在
**服务器侧**，独立于 agent / 界面：即使 UI 卡住、agent 挂掉、甚至服务器
被 `SIGKILL`，帧都已经落盘，不会丢。

**工作原理**

- 服务器在**每个 API 动作前后**抓一帧复合图（head 相机 + wrist 相机 +
  左右触觉，320×1120），见 `launch_univtac_server.py` 的 `_record_frame()`。
- 每帧立即写成 PNG——但写到**临时目录**
  `/tmp/univtac_frames/univtac_<task>/<时间戳>/frames/`（不在 outputs/），
  所以进程被杀帧也不丢；outputs/ 里最终只留成片，不堆素材。
- `robo.episode_video()` 一调用就**立即**用 ffmpeg 把当前 episode 的 PNG
  序列拼成 `episode.mp4` 并写 `index.json` 到
  `outputs/agent/univtac_<task>/<时间戳>/`（目录层级效仿评测 harness 的
  `outputs/<model>/univtac_<task>/`），随后**删除临时帧**；之后 reset 或
  服务器正常退出时也会对当时的 episode 兜底合片。`index.json` 记录每帧
  对应的动作标签（`move:tag=…:before/:after`、`gripper:open/close`、
  `rotate:step`、`reset:initial`）。

**回看路径**

- agent 结束时通过 `robo.episode_video()` 拿到 MP4 路径（调用即出片，
  `mp4_ready=True`），会写进它的总结；你也可以直接看：
  `outputs/agent/univtac_<task>/<时间戳>/episode.mp4`。
- 用 `index.json` 对照帧号 → 动作，跳到你关心的那一步。

**服务器被 SIGKILL（没拼出 MP4）**

临时 PNG 帧还在 `/tmp/univtac_frames/` 里，用补拼脚本手动拼（从临时
帧拼到 outputs/ 并清掉临时帧，等价于一次正常的 finalize）：

```bash
python scripts/assemble_univtac_video.py <episode_dir>   # 单个 episode
python scripts/assemble_univtac_video.py <task>          # 某任务全部 episode
python scripts/assemble_univtac_video.py --all           # 所有 episode
```

幂等：已有 `episode.mp4` 会跳过，加 `--force` 重拼，`--fps` 改帧率。

**关闭 / 换位置**

- `UNIVTAC_RECORD=0`：完全关闭录像。
- `UNIVTAC_RECORD_DIR=<dir>`：把成片输出目录换到别处（默认
  `cap-x/outputs/agent/`，已 gitignore）。
- `UNIVTAC_FRAMES_DIR=<dir>`：把临时帧目录换到别处（默认 `/tmp`）。

**一个顺带修掉的 bug**

此前 `contact.yml` 里 `video_frequency: 2` 会让 UniVTAC 的 `VideoHandler`
每次 `/reset` 打开一个 ffmpeg 管道且从不关闭（孤儿进程泄漏）。现在服务器
强制 `video_frequency = 0`，录像改走上面的 `_record_frame()` 路径。

## 动作轨迹与错误日志

agent 每次跑任务的**动作调用序列与错误**会由服务器自动追加到
`outputs/agent/univtac_<task>/<时间戳>/univtac_actions.jsonl`（JSONL，
每行一个事件）——不用翻完整会话记录就能定位"第几步出了什么错"。与录像
互补：录像看画面，日志看调用与失败。**日志与视频在同一 episode 目录**，
一次运行的全部产物（`episode.mp4` + `index.json` +
`univtac_actions.jsonl`）自包含于该 episode 目录。

**记录粒度**

- **动作端点**（`move` / `move_delta` / `gripper` / `rotate_gripper` /
  `reset`）：每次调用都记，含参数、成功/失败、耗时；每次动作都附左右触觉
  深度（`tactile`，数据已在返回里、零额外调用），日志自含物理进度——
  物理楔住（动作返回成功但深度骤变/位移飙升）在日志里一眼可辨。
- **只读端点**（`tactile` / `observation` / `object_pose` / `ee_pose` /
  `gripper_state` / `check_success` / `grasp_pose`）：只在失败时记一行
  （503 仿真忙 / 500 异常 / 404 对象不存在），正常读取不刷屏。
- 每次 `/reset` 记一行 `evt:"reset"` 分隔标记（带任务、seed、episode 目录名），
  服务器启动记一行 `evt:"server_start"`。

**行格式**（`t_ms` 是墙钟毫秒，与录像 `index.json` 同源，可直接对齐帧）：

```jsonc
{"t_ms": 1760000000000, "ep": "20260824_230104", "kind": "action",
 "a": "move_delta", "args": {"dz": -0.005}, "ok": false,
 "msg": "Motion planning failed", "dur_s": 2.1,
 "tactile": {"L_depth_mm": 12.3, "R_depth_mm": 0.0}}
```

`ep` = episode 目录名（文件已在 episode 目录内，`ep` 字段保留以便跨文件过滤）。
每次 `/reset` 开新文件，旧 episode 的文件即已定稿。

**回看路径**

```bash
jq 'select(.ok == false)' outputs/agent/univtac_<task>/<时间戳>/univtac_actions.jsonl  # 只看失败
jq 'select(.a == "move_delta") | {t_ms, ok, msg}' outputs/agent/univtac_<task>/<时间戳>/univtac_actions.jsonl  # 单个动作
```

`UNIVTAC_ACTION_LOG=<path>` 可换成一个固定的全局单文件（默认每 episode 一个，
在 `cap-x/outputs/agent/univtac_<task>/<时间戳>/` 下）。

这个文件是**操作员侧的调试记录**：agent 不知道也不读它（API 无读取端点，
SKILL.md 亦不提及），内容只是 agent 自己发出的请求 + 服务器返回，无新增信息面。
