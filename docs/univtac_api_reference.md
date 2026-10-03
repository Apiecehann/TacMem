# UniVTAC API Reference

> 机器人控制 API 参考。三个 API 类共享同一台仿真服务器
> (`capx/serving/launch_univtac_server.py`)，彼此的差别只有两点：**暴露哪些函数**，
> 以及**物体位姿来自 ground truth 还是相机感知**。
>
> 贯穿全文的规则：**LLM 只看得见 `functions()` 里列出的函数**——它同时决定 prompt
> 正文（`combined_doc`）和 exec 沙箱的绑定。下文标为 **agent 专用**的方法 LLM
> 既看不到也调不到。

---

## 0. 三条运行线

| | **特权** | **非特权** | **agent 线（robo）** |
|---|---|---|---|
| API 类 | `UniVTACTactileApi` | `UniVTACPerceptionApi` | `UniVTACPerceptionApi` |
| `get_object_pose` | prim 世界原点，四元数可信 | SAM3 分割点云**质心**，四元数恒等 | 同非特权 |
| 配置文件 | `*_multiturn*.yaml` | `*_non_privileged.yaml` | `univtac_skill/` |
| 需要的服务 | UniVTAC (8120) | UniVTAC (8120) + SAM3 (8114) | 同非特权 |

- **`UniVTACPerceptionApi` 只重写 `get_object_pose`**；运动、触觉、`check_success` 全部继承。
  `sample_grasp_pose` 仍是 ground-truth 抓取采样（抓取感知不在范围内）。
- **agent 线永久非特权、没有开关**：`univtac_skill/robo.py` 直接用 `UniVTACPerceptionApi`，
  想换回真值位姿只能改代码。唯一例外是 `check_success` 的数值 `detail`（cap 评测线对 LLM
  隐藏、agent 线可见——已知接受项）。
- ⚠️ **质心 ≠ 原点**：非特权的 `position` 是分割点云**质心**，特权是 prim 原点。按质心语义
  写非特权的 PROMPT/ORACLE，不要照搬原点的「上方 1 cm」阈值。

### `/observation` 的相机字段

`{cam}_rgb_base64` / `{cam}_depth_base64` 是**归一化、丢失尺度**的显示用图；米制反投影
（非特权感知）要用新增的一组（`head` / `wrist` 对称）：

| 字段 | 含义 |
|---|---|
| `{cam}_depth_float32_base64` | 米制深度（float32 原始字节 → base64），按 `{cam}_image_shape` reshape 成 (H, W) |
| `{cam}_intrinsics` | 3×3 内参矩阵（fx / fy / cx / cy） |
| `{cam}_cam_pos_w` | 相机世界位置 `[x, y, z]`（米） |
| `{cam}_cam_quat_w_ros` | 相机世界朝向 `[w, x, y, z]`（ROS 约定：光轴 +Z，相机系 → 世界系） |

> 客户端 `get_observation()` 只解码 `*_rgb` / `*_depth` 和上面四个标定键；响应里另有
> `tactile` / `ee_position` / `gripper_qpos`，不透出。

---

## 1. 三个 API 类

| | `UniVTACTactileApi` | `UniVTACMotionApi` | `UniVTACPerceptionApi` |
|---|---|---|---|
| **文件** | `capx/integrations/tactile/univtac_api.py` | `univtac_motion_api.py` | `univtac_perception_api.py` |
| **用途** | 标准评测（触觉 + 运动，特权位姿） | ablation 基线（仅运动，无触觉） | 非特权：位姿来自 SAM3 |
| **Motion 返回值** | `dict`（含触觉） | `None` | `dict`（继承） |
| **夹爪控制** | 自适应夹持（触觉反馈停止） | 位置闭合；无 `position` / `adaptive` | 自适应夹持（继承） |
| **`get_object_pose`** | ground truth prim 位姿 | ground truth prim 位姿 | SAM3 分割质心 |

---

## 2. 函数总览

「LLM」列 = 是否进 cap-x 的 prompt 与 exec 沙箱（即是否在 `functions()` 里）。

| 函数 | `TactileApi` | `MotionApi` | LLM | 返回 |
|---|---|---|---|---|
| `goto_pose` | ✅ | ✅ | ✅ | `dict` / `None` |
| `move_delta` | ✅ | ✅ | ✅ | `dict` / `None` |
| `open_gripper` | ✅ | ✅ | ✅ | `dict` / `None` |
| `close_gripper` | ✅ | ✅ | ✅ | `dict` / `None` |
| `rotate_gripper` | ✅ | ✅ | ✅ | `dict` / `None` |
| `get_object_pose` | ✅ | ✅ | ✅ | `(pos, quat, bbox\|None)` |
| `sample_grasp_pose` | ✅ | ✅ | ✅ | `(pos, quat)` |
| `get_tactile_data` | ✅ | ❌ | ✅ | `dict` |
| `detect_contact` | ✅ | ❌ | ✅ | `bool` |
| `detect_slip` | ✅ | ❌ | ✅ | `bool` |
| `check_success` | ✅ | ✅ | ❌ 见下 | `bool` |
| `move_reflexive` | ✅ | ❌ | ❌ **agent 专用** | `dict` |
| `get_tactile_image` | ✅ | ❌ | ❌ **agent 专用** | `ndarray` \| `None` |
| `get_observation` / `get_ee_pose` | ✅ | ❌ | ❌ **agent 专用** | `dict` |

- **`check_success` 在 `TactileApi` 里被 `_prompt_hidden` 隐藏**：框架在代码末尾自动追加一次
  调用并自行算分。`MotionApi` 无隐藏项 ⇒ 它的 prompt **包含** `check_success`，也无追加调用。
- `move_reflexive` / `get_tactile_image` **只在 agent 线可用**（`robo.py` 直接绑定方法，绕过
  `functions()`）；cap-x 的 eval 面 v1 明确不暴露。

### 三条判读配方（最常用；推导与实测见 §3.4 / §3.9）

| 判什么 | 读什么 | 关键数 |
|---|---|---|
| **重量**（无参考物） | 轻握悬挂后凝胶标记的**跨指剪切差分** `(左悬−左基) − (右悬−右基)` 的 x 分量 | 轻 ≈ 0（<0.05 px）／重 0.7–1.0 px，**劈在 0.2 px** |
| **硬度** | 同一次抓握内**两次夹紧位置**的 `min(L,R) depth` 之差 | 硬 ~1.6 mm／软 ~0.8 mm，**劈在 1.2 mm** |
| **纹理** | 两次握力之间 `marker_anisotropy` 的**增量** ÷ 两次读数的深度差 | 粗糙 0.10–0.13/mm／光滑 0.01–0.03/mm，**劈在 0.06** |

**顺序锁死：先称重，后测硬度**——硬度那一步是紧夹，会把重量读数毁掉。

---

## 3. 详细函数参考

### 3.1 `goto_pose` — 移动到目标位姿

```
goto_pose(position: (3,) ndarray, quaternion_wxyz: (4,) ndarray,
          z_approach: float = 0.0) -> dict | None
```

`z_approach=0.1` 用于抓取/放置（在目标上方加一个接近航点），`0.0` 用于自由空间运动。

**命令的是末端执行器，不是被夹物体**——两者差一个抓取偏移。要把手里的物体放到 T，
得命令 `T + offset`（`offset = grasp_pos − object_pos`）。直接命令 T 会把物体压进桌面，
规划器报 `Motion planning failed`。

**抓取位姿要一次到位。** 拆成「先到上方 5 cm、再到目标」两步会差到约 1 mm，随后的
闭合会把物体横向拖过夹爪。

**移动被夹物体优先用世界系 `move_delta`**：不用做偏移算术，且不重新命令手腕朝向（`goto_pose`
会——物体正在落台接触时那个朝向修正会在轻握里把它拧歪，见 §3.4）。

返回 8 字段触觉 `dict`（`{left,right}_{contact, depth_mm, marker_displacement_px,
marker_coherence}`；`MotionApi` 返回 `None`）。规划失败时抛 `RuntimeError("Motion failed: ...")`。

---

### 3.2 `move_delta` — 增量移动

```
move_delta(dx=0.0, dy=0.0, dz=0.0, drx=0.0, dry=0.0, drz=0.0,
           coord="world") -> dict | None
```

接触丰富任务（插入、对齐）的主要原语。

**每步限位：** 平移每轴 ≤ **0.10 m**、旋转 ≤ **0.5 rad**（约 30°）；平移下限
**2 mm**、旋转下限 **0.02 rad**；目标 Z ≥ **0.01 m**（低于桌面直接拒绝）。

⚠️ **下限那条最坑：每个轴都低于下限时整条命令被拒绝**，抛 `RuntimeError: Motion
failed: Delta is below the 2 mm / 0.02 rad minimum on every axis - the arm did NOT
move.`（早期版本是静默 no-op 且回 `success: True`，会把「收敛到目标」的循环变成永不
退出——现已改为明确报错。）⇒ **闭环下降无法收敛到比 2 mm 更细的容差**：至少 2 mm
一步，物体到位就停，不要在最后一毫米上硬凑。

返回 8 字段触觉 `dict`，可用于接触即停的闭环控制（`MotionApi` 返回 `None`）。

```python
# 接触即停（仅 TactileApi）
while True:
    fb = move_delta(dz=-0.005)
    if fb["left_contact"] or fb["right_contact"]:
        break
```

> ⚠️ **旋转分量的坐标系陷阱：** `coord="world"`（默认）下，旋转是把末端执行器的
> **位姿绕世界原点（机器人基座）**整体旋转，位置 `p` 也会被转动——工作区在
> x≈0.55 m 处，0.04 rad 的旋转就会让夹持物体沿弧线甩动约 2 cm。**不要**用世界系
> 旋转微调夹持物体的朝向：原地旋转用 `coord="ee"`；定向调整优先用 `rotate_gripper`。

---

### 3.3 `open_gripper` — 打开夹爪

```
open_gripper() -> dict | None
```

完全打开夹爪。返回打开后的触觉反馈（`MotionApi` 返回 `None`）。

**释放是「放置」而不是「开爪」**（2026-09-20 由任务 PROMPT 移入 docstring——它描述的是
夹爪的释放语义，不是某一条任务的口诀）：

- 只在物体**已经搁在表面上**时才开。用小步（~5 mm）下降，盯着**被夹物体自己的高度**——
  那个高度一旦不再下降，说明它已被支撑，**再也不要往下压**。
- 开爪前先**后退 ~5 mm**（一步，把手指压在物体上的重量卸掉），然后才开。手指还压着物体时
  开爪会把它**推歪或楔死**；从悬空 ~1 cm 以上开爪会把它**摔歪**。
- 开爪后**继续小步执行**直到物体静止——只有稳稳停住才算放置完成，**不要在它还在下落时结束**。
- 若某次开爪把它弄歪或楔住：抬 ~3 cm，重新抓取，重新放置。

> ⚠️ **与 §3.4 的 ViTaForge 配方相反，不要混用**：`weight_classify` 类任务**不要求松手**，
> 到位后保持夹持即可（甚至不能开爪，见 §3.4）。是否开爪取决于**本任务**的 `check_success`
> 是否要求释放，而不是通用习惯。

---

### 3.4 `close_gripper` — 闭合夹爪

```
close_gripper(force_threshold: float | None = None,
              position: float | None = None,
              adaptive: bool = True) -> dict | None
```

**TactileApi** 自适应夹持：实时监测凝胶变形量，达到目标深度即停，返回触觉 `dict`。
**MotionApi** 纯位置闭合，无下述三个参数，返回 `None`。

**参数（仅 TactileApi）：**

- **`force_threshold`**：目标凝胶变形深度（mm）。深度随接触变硬而**减小**，所以数值
  越**大**抓力越**松**：34 = 未接触，~27–28 = 轻握，~20 = 重压。`None` 用任务默认
  （实到深度散落，典型 ~25.5 mm 的紧握）。
- **`position`**：直接指定夹爪行程百分比（0.0 = 全闭，1.0 = 全开），按**指间距**而不是
  凝胶深度设定夹紧力。物体刚性太强、手指被顶住、深度伺服没有可停区间时用它：取略大于
  物体宽度就是最轻的接触。与 `force_threshold` 是两种选择，同时给出时谁先到谁停。
- **`adaptive`**：是否让深度伺服参与这次闭合。`True`（默认）向 `force_threshold` 驱动；
  `False` 直接命令手指行程，手指走到哪就是哪——**要用 `position` 量「物体让了多少」时
  必须配 `False`**。

**实到深度 ≠ 请求值**：偏差可达零点几毫米，且随 server session 漂移。判读一律用**实到
值**：回读 `left_depth_mm` / `right_depth_mm`（更深的一侧是 `min(left, right)`，两指常差
~0.3 mm）。偏差大时张开后重夹一次 `target - (realized - target)` 即可命中（伺服对同一
目标重复同样的落点误差）。在轻握深度重夹不会破坏重量读数。

#### 硬度：读凝胶对「夹紧变化量」的响应，不读任何单次闭合的绝对深度

软物体被夹过屈服点后不再抵抗：手指继续走而凝胶几乎不动，多出来的行程被物体吸收；硬物体
原样传下去。判据是**同一次抓握内两次不同夹紧位置的差分**：

1. `close_gripper(position=0.10, adaptive=False)`，读 `min(left_depth_mm, right_depth_mm)`；
2. 再 `close_gripper(position=0.06, adaptive=False)`（更紧），再读一次。

硬物体 depth 掉 ~1.6 mm；软物体只动 ~0.8 mm，**劈在 1.2 mm**。用差分而不是绝对深度：
差分把「这次落在哪」抵消掉了，绝对值不能。

> **不要**用 `force_threshold` 去夹紧柔顺物体：软物体一路屈服到指爪触底（depth ≈ 26.4 mm、
> qpos 0），任何比 ~26.5 更紧的目标伺服**永远到不了**，闭合**直接报错**（`RuntimeError:
> Motion failed: Gripper action failed`），而不是"停在半路"。
>
> **`adaptive=True` 会把符号整个反过来**：同样 `position=0.10→0.06`，伺服把软物体推到指爪
> 触底，凝胶读到的是**死夹**——soft 反而比 hard 更"硬"（实测 hard 0.63 vs soft 0.99/0.80 mm，
> 两类重叠）。`adaptive` 不是性能开关，是这条判据成不成立的前提。同一个触底原因，在称重用
> 的轻握深度上单点读 depth 两类落点相同 ⇒ **对硬度零信号**（0.18×）：硬度必须夹到比称重
> 紧得多的地方去读。

#### 重量：读凝胶的切向剪切，不读抬升高度

载荷只能靠指垫摩擦承担，于是把标记图案横向拖走（`marker_shear_dx/dy`，见 §3.9）。读法是
**同一抓握内的差分**——闭合本身造成的挤压远大于载荷的贡献、且随落点变化，必须抵消：

1. `close_gripper(force_threshold=27.7)`（见本节末：**质量会挪走这个工作点**，
   28.5 只适用于几克重的刚性物体）；
2. 物体**还搁在桌上**时读一次 `get_tactile_data()`（凝胶上还没有悬挂载荷，这是基线）；
3. 分几步抬 ~3 cm（每步 ~1 cm）；
4. 悬挂静止后再读一次。

两指镜像安装，同一次下滑在两侧图像里符号相反，所以取
`(左_悬挂 − 左_基线) − (右_悬挂 − 右_基线)` 的 **x 分量**：挤压被抵消、载荷被叠加。
轻物体 ≈ 0（`|·| < 0.05 px`），重物体 0.7–1.0 px，阈值 **0.2 px**。
实测（ViTaForge，方块 180 g vs 1.8 g，同 seed 只有质量不同、n=12 随机标签、T=28.5）：
重 0.72–0.99 px、轻 0.001–0.045 px，零重叠；**匹配实际挤压深度仍有 19× 分离 ⇒ 是质量，
不是夹持深度**。

> ⚠️ **先验基线是否真的空载。** 闭合本身会把物体横向拖进夹爪、把凝胶剪切得和重载差不多。
> 有效基线里两指的 `shear_dx` 相差应在 ~0.4 px 以内；超出说明这次抓握已经在承担物体重量，
> 第 3 步的抬升就不再增加读数，**重物会被读成轻的**。补救是张开、**用单次 `goto_pose`
> 重新接近**（拆分下降正是让闭合拖动物体的原因）、再闭合（§3.1）。**改成减去更大的基线
> 不成立**——剪切已经到了这个握力能到的极限，不是单纯偏置。

> **不要**改用 `marker_displacement_px`（丢掉了方向）或 `marker_coherence`（n=12 实测两者
> 都与重量重叠）。`min_pad |Δshear|` 在混杂深度下也重叠（只在匹配深度带内才分开），所以
> 判据要用**跨指差分**而不是单指幅度。

**已废弃的两套读法：** `trail = (EE 抬升) − (物体抬升)`（要 agent 专用的 `get_ee_pose()`，
分离度只 ~5×）与代码 docstring 里 `force_threshold=27.7`「抬 ~2 cm 比谁跟得上」那套。两者
读的都是**抬升高度的下游后果**，而轻重的信息本来就在凝胶里；有剪切差分可用时不要用它们。
（顺带：刚性物体上 ~28.5 有信号、到 ~26 就塌——夹得越紧越会把重量藏起来。）

> ⚠️ **但 28.5 是在「轻」物体上标的，质量会把这个工作点挪走**（2026-09-20 实测）。
> 方块 1.8 g / 180 g 在 28.5 上被稳稳带着走；而 **300 g 及以上的刚性罐子（memory_match）
> 在 28.5 直接滑出去**——凝胶几乎不加载（`marker_displacement_px` 停在 ~0.02 px）、抬
> 2 cm 罐子留在桌上，**要把闭合收到 ~27.7**（与 oracle 自己标定的甜点 27.70 一致，
> ≥27.9 时连轻罐也滑）。**抬起来物体没跟上 = 握得太轻**，正确反应是**夹得更紧再抬**，
> 而不是「这次读数作废」：同一条命令重复执行返回同一个读数（纯运动学），只有夹得更紧
> 才会改变答案。活体证据：同一批 10 个 memory_match LLM trial 里，探针用 28.5 的
> （1/3/4/6/7/10）**没有一个拿到有效读数**，用 27.7 的（5/8/9）全部拿到读数、其中 8 和 9
> 通关；两类之间没有第三种结局。

#### 放置与释放（ViTaForge `weight_classify` 实测）

**搬运用世界系 `move_delta`，到位后不要开爪。**

- **搬运不要用 `goto_pose`**：`move_delta` 只平移（方块被刚性夹住，手移多少它就移多少），
  而 `goto_pose` 会顺带把腕部朝向命令回 `grasp_quat`——方块正在落台接触时这个朝向修正会在
  轻握里**把方块拧歪**：同 seed 实测倾角 **11–45°**，世界系平移只有 **0.2–0.3°**。
- **坐到盘面后不要开爪**：1.8 g 的方块凝胶抓得住，一张开就把方块拖下盘或掀翻——同 seed
  **11 次尝试里 8 次失败**（oracle 与 LLM 一样中招），换更轻的握法也救不回来。
- **任务本就不要求松手**：ViTaForge 专家 `_play_once()` 就是 `place_actor(..., is_open=False)`
  之后结束，全文件唯一一次 `open_gripper(0.5)` 在抓取**之前**；`check_success` 只判方块位姿，
  与夹爪是否张开无关。⇒ 可靠配方 = 触觉判重 → 世界系搬运 → 降到盘面 → **保持夹持**，实测 3/3。
- **还压在桌上时不要横拖**，必倒：先抬 3 cm 再横移。

---

### 3.5 `rotate_gripper` — 旋转夹爪

```
rotate_gripper(object_name: str, angle_rad: float, steps: int = 4) -> dict | None
```

旋转持物的夹爪，用于大角度旋转（>0.3 rad），而非 `move_delta`。服务端把旋转拆成 `steps`
步执行，然后**验证物体是否真的转了**：至少达到请求角度的 30%（下限 0.05 rad）；不达标就
报错（物体没被夹牢）。返回旋转后的触觉反馈；`MotionApi` 返回 `None`。

---

### 3.6 `get_object_pose` — 获取物体位姿

```
# 特权
get_object_pose(object_name, return_bbox_extent=False) -> (pos, quat, bbox|None)
# 非特权（UniVTACPerceptionApi）多一个相机参数
get_object_pose(object_name, return_bbox_extent=False, camera="head") -> ...
```

名称匹配顺序：精确名 → 前缀/子串（`"prism"` 可匹配 `"rough_prism"`）→ 任务角色名
（如 `reference_object`）。多个候选时取**离工作区中心最近**的那个。

**返回值（永远是三元组，不要拆成两个）：**

- `position`：**prim 的**世界原点 XYZ（米）。**是 prim 原点，不一定是几何中心。**任务
  物体多以原点在底面著录（静止时贴桌面，z≈0.002–0.01）。
- `quaternion_wxyz`：WXYZ 四元数（非特权下恒等、不可用）。
- `bbox_extent`：`return_bbox_extent=True` 时返回世界包围盒**总尺寸** `[dx, dy, dz]`（米，
  完整宽/高，**不是半长**）；否则为 `None`。物体不支持时抛 `NotImplementedError`
  （而非静默返回 `None`）。

> **放置高度（原点在底面的物体）：** 物体顶面高度 = `position[2] + bbox[2]`。把 prism 放到
> pad 上：`target_z = pad_pos[2] + pad_ext[2] - 0.001`（1 mm 下压余量）。**不要用 `bbox/2`**：
> position 已在底面、bbox 已是总高，再取半会把物体悬空半个身高。

> ⚠️ 以上是**特权语义**，仅适用于 eval 特权配置。非特权 `UniVTACPerceptionApi`
> （robo / cap 非特权线）语义完全不同（质心、恒等四元数、`np.ptp` bbox），见第 0 节。
> 非特权感知还有启发式门限：mask 必须反投影出 30–10000 个有效深度点、且不能以相机 5 cm
> 内的点为主（那是机械臂自己）；两次尝试都失败则抛 `RuntimeError`。

---

### 3.7 `sample_grasp_pose` — 获取抓取位姿

```
sample_grasp_pose(object_name: str) -> tuple[ndarray, ndarray]
```

返回桌面物体的抓取位姿。配合 `goto_pose(..., z_approach=0.05)` 实现安全的自上而下接近。
**两个 API 均可用，非特权下也仍是 ground-truth 抓取采样。**

> ⚠️ **只对场景锚定的那个物体可靠**（EE 初始正上方那个）。抓取偏移在**第一次调用**时从
> 当时的 EE 位姿缓存；EE 一旦移到别处（比如你已经把第一个物体放好了），这个缓存就对不上
> 其它物体，返回的位姿可能离目标很远。对 EE 当前不在其正上方的任何物体**不要调它**：
> 自己用测得的偏移构造 `grasp_pos = object_pos + offset`——「EE 相对所持物体中心的偏移」
> 对所有物体都一样，复用第一次成功抓取的偏移即可。

`object_name` 是**仿真器 prim 名**（`"object_a"` / `"object_b"`），不是感知短语：它查的是
仿真器的 ground-truth 场景，传 SAM3 短语（如 `"orange can"`）会 404。（感知短语只对
`get_object_pose` 有意义。）

---

### 3.8 `check_success` — 检查任务是否完成

```
check_success() -> bool
```

在代码末尾调用以验证。**在 `TactileApi` 里它对 LLM 是隐藏的**（`_prompt_hidden`）：框架
在代码末尾自动追加一次调用并自行逐步算分。`detail` 里的判据字段（`prism_pos` /
`target_pos` / `ground_truth_texture` / `expected_pad` / `reasons`）在 cap 评测线对 LLM
隐藏，agent 线可见。

---

### 3.9 `get_tactile_data` — 读取触觉传感器（仅 TactileApi）

```
get_tactile_data() -> dict
```

读取 GelSight Mini 触觉传感器。`{side}` ∈ `{left, right}`：

| 键 | 描述 |
|---|---|
| `{side}_contact` | bool，该指尖是否有接触 |
| `{side}_depth_mm` | 凝胶变形量（mm）。34 = 远平面（未接触），**数值越小夹得越紧**：~27 轻握、~20 硬卡 |
| `{side}_marker_displacement_px` | 自本轮 reset 无接触基准以来的**平均绝对**标记位移。**任何**凝胶形变（接触/插入）都会让它上升，单独不是干净的滑移信号 |
| `{side}_marker_coherence` | 标记运动一致性 (0-1)。高 = 一起动（剪切/打滑），低 = 非相干（压缩/形变/追踪丢失） |
| `{side}_marker_shear_dx` / `_dy` | 平均标记位移**向量**（px，传感器 UV 帧）——保住了方向，是重量判读（§3.4）与 `move_reflexive` 事件的基础 |
| `{side}_marker_anisotropy` | 标记图**行梯度 ÷ 列梯度**。1.0 = 各向同性（未加载/均匀加载）；高 = 形变有水平结构（**纹理**）。**客户端由 marker 图现算，不是服务端标量** |

> **「双侧夹住」要按 `{side}_contact` 和「两侧深度都明显小于远平面」来判，不要按两侧位移相近
> 来判。**（2026-09-20 由任务 PROMPT 移入 docstring。）`marker_displacement_px` 随局部凝胶
> 形变走，真夹住时也常常是**单侧**的；要求位移对称会把几乎所有读数都判成无效，最后没有东西可比。

> **判读只用 shear 和 anisotropy**（配方与阈值见 §3.4）：重量看跨指 shear 差分，纹理看两次
> 握力间的 anisotropy 增量。**其余标量都是整幅凝胶图的平均，而平均看不见结构** ——
> `marker_displacement_px` 丢掉了方向、`marker_coherence` 只在真打滑时才高，单独都不能判重
> 或判纹理。

> ⚠️ **已废弃的旧判据：「粗糙表面相干度低 ~0.55 / 光滑高 ~0.75，抓稳后读一次 coherence
> 即可分类」。** n=10 seed 实测 coherence 轴**零判别力**（oracle 6/10 < 常数基线 7/10）；
> `rotate_gripper` 的滑脱校验同样分不开（两棱柱摩擦系数相同，滑移行为一致）。

> **注意：** TactileApi 的所有 motion 函数也返回 per-side 的 `contact` / `depth_mm` /
> `marker_displacement_px` / `marker_coherence` 这 4 个字段（共 8 个键）的 dict，大部分场景下
> 无需单独调用此函数。其余字段（shear / anisotropy / std）只有 `get_tactile_data` 才给。

---

### 3.10 `detect_contact` — 检测接触（仅 TactileApi）

```
detect_contact() -> bool
```

任一指尖凝胶**是否正被压**（深度图已有比远平面更近的像素）。`get_tactile_data()` 的便捷
封装。⚠️ **它不是接近传感器**：张开的夹爪从物体旁边下降时返回 `False`（还没有东西压凝胶），
不要用它来停下降，下降要靠目标位置驱动。只有 `close_gripper` 真的夹住了什么它才会变 `True`；
用它来在抬升前确认夹持已经啮合。

---

### 3.11 `detect_slip` — 检测滑移（仅 TactileApi）

```
detect_slip(threshold_px: float = 2.0, coherence_threshold: float = 0.7) -> bool
```

**自上次调用以来的位移增量**：真打滑/卡住时标记继续沿同一方向剪切（coherence 高），位移比
上次读数增长超过阈值。持稳状态——哪怕之前已经滑过——不会再报。**第一次调用只建立参考读数
（返回 False）**，请在被测运动之前先调用一次，或接受首个运动不会被判定。`threshold_px`
1.0 = 敏感、5.0 = 宽松；`coherence_threshold` ~0.7 区分均匀剪切与压缩/形变。

> ⚠️ **不要用它判「物体落到支撑面了」。** 缓慢下压主要压缩凝胶而不是剪切它，位移增长到不了
> 阈值，于是**不会**报滑移——循环会继续下降，指尖楔到物体下面把它顶出夹爪。要放置物体：小步
> 下降 + 用 `get_object_pose()` 盯物体自身高度，一旦不再下降就释放，**之后绝不再压**。

> `*_marker_displacement_px` 是「自本轮 reset 以来的绝对位移」——一次真卡死后会持续保持高位，
> 不要直接拿它和阈值比较；`detect_slip` 内部已做增量处理，增量为负（标记松弛回弹）不算滑移。

---

### 3.12 `move_reflexive` — 触觉反射式增量移动（快回路，agent 专用）

```
move_reflexive(dx=0, dy=0, dz=0, drx=0, dry=0, drz=0,
               coord="world", stop_on: dict | None = None) -> dict
```

与 `move_delta` 相同的增量语义与每步限位（含 2 mm / 0.02 rad 下限），但运动过程中服务器
**在每个仿真步**评估触觉事件，**第一个命中的启用条件即冻结机械臂**并返回事件摘要——无需在
慢循环里轮询小步。（架构与阈值来源见 `docs/univtac_tactile_reflex_architecture.md`。）

**`stop_on` 条件表**（全部 OR；键不出现即禁用；阈值缺省取 `capx/serving/tactile_reflex.py`
的 `DEFAULT_THRESHOLDS`）：

| 键 | 默认 | 含义 |
|---|---|---|
| `contact=True` | far_plane 34.0 mm | 任一指尖触物（depth < 34 − 0.1） |
| `hard_depth=<mm>` | 20.0 | 任一侧 depth ≤ 阈值（硬卡/过压保护） |
| `shear_spike=<px>` | 2.0 | 每步标记位移增幅 ≥ 阈值且 coherence ≥ 0.7（摩擦事件） |
| `slip=<deg>` | 30.0 | 剪切方向相邻步转角 ≥ 阈值（需 ≥2 样本、幅度噪声门 2 px、coherence ≥ 0.7）——方向感知打滑，优于 `detect_slip` 的幅度盲判 |
| `asymmetry=<mm>` | 3.0 | `\|L_depth − R_depth\|` ≥ 阈值且至少一侧接触（偏航不对齐） |
| `plateau={"mm":0.5,"steps":5}` | 0.5 mm / 5 步 | 接触后深度持续平坦（卡住/已坐实），需 depth ≤ 27.5 mm |
| `max_steps=<n>` | — | 安全网：评估 n 步后停止 |

**返回：** `tactile`（同 `move_delta` 的 8 字段）、`terminated_by`（停止运动的事件名，跑完为
`None`）、`event`（摘要：`steps` / `max_shear_px` / `shear_dir_deg` / `asymmetry_mm` /
`min_depth_mm` / `contact_step` / `events` / `callback_error`）。

**语义要点：** `success=True` 可能意味着「条件停止」而非「到达目标」——先看 `terminated_by`
再推理位姿；触发后**绝不重试**（不会以更小尺度重跑同一段接触），触发是成功结局；
`stop_on=None` 为监控模式（运动跑完，摘要仍填充）；反射激活时每步一次完整渲染（约 2 FPS），
单次调用显著长于 `move_delta`，请保持 deltas ≤ 0.05 m；触发后 `plan_success` 已恢复，
后续 `move_delta` 不受影响。用法（取代 while 循环轮询）见 `univtac_skill/SKILL.md`。

---

### 3.13 `get_tactile_image` — 指尖 GelSight marker 图（agent 专用）

```
get_tactile_image(side: str = "left") -> np.ndarray | None
```

解码一个指尖的 marker 视图图像（凝胶上印刷的 marker 网格，约 320×240）——位移/相干度标量
背后的原始视觉信号。**仅在决策点调用**：每次调用都是一次完整渲染（约 0.5 s），运动中切勿
取图。存到 scratch/ 后像相机图一样检查（裁剪/放大感兴趣区域）。返回 `(H, W, 3)` uint8；
不可用时返回 `None`。

---

### 3.14 插入状态判读（insert_hdmi 专用，2026-08-19 标定）

> **该任务已放弃**（2026-08-27；是感知瓶颈，非 prompt 可修）。结论留档备查。

**滑移幅度分级（`detect_slip` 位移增量，px）：** ≤0.5 正常噪声；0.5–3 摩擦/接触变形（都不是
卡死，继续下降）；**4–8 且 1–2 步内衰减 = 正在穿过插座口沿 → 继续下降，不要提拉**；≥3 且多步
持续、插头不再下沉 = 真卡死 → 提 ≤2 步 + 侧推重试。**滑移本身绝不等于卡死。**

**坐入 vs 口沿卡死（横向探针 + 位姿回查）：** 插头不再下沉时侧推 ~3 mm 再回查位姿——**坐入**：
位姿几乎不变（rel Y 偏 0.3–0.8 mm）→ 停；**口沿卡死**：一推就脱开（rel Y 偏 45–52 mm）→ 拉回
重试。位姿用非特权 `get_object_pose` 或 `check_success()` 的 `detail.prism_pos`。

**✗ 已证伪：** 提拉探针（两种情形的 2 mm 提拉滑移都 ~0.01 px）；深度读数（卡死时无变化）；
「大滑移 = 卡死」（穿口沿的摩擦会被误判）。

---

## 4. 错误处理

所有 HTTP 调用均受错误翻译保护，LLM 在 `{console_stderr}` 里看到可操作的 `RuntimeError`：

| 场景 | LLM 看到的错误 |
|---|---|
| 运动规划失败 | `Motion failed: <服务端消息>` |
| 步长过大 | `Motion failed: \|dz\|=0.213 exceeds per-step limit (0.100). Use multiple smaller steps instead.` |
| 步长低于下限 | `Motion failed: Delta is below the 2 mm / 0.02 rad minimum on every axis - the arm did NOT move.` |
| 目标 Z 低于桌面 | `Motion failed: Target Z=-0.042 is below the table surface. Reduce the downward delta.` |
| 柔顺物体被 `force_threshold` 夹到触底 | `Motion failed: Gripper action failed`（见 §3.4）|
| 旋转时物体滑脱 | `Motion failed: Object 'prism' rotated only 1.1° (requested 8.6°). The object may not be firmly gripped — ... re-grasp.` |
| 物体未找到 | `Object 'can' not found in scene` |
| 非特权感知两次都失败 | `Perception for '...' from camera 'head' rejected on both attempts (...)` |
| POST 超时（600 s 无响应） | 先探 `/health`：**回了** → 操作极慢或卡住后恢复，**场景状态未知**，重新查位姿、不要假设动作发生没发生；**静默** → 服务器被 C++/CUDA 冻结（持有 GIL），外部看门狗 120 s 后 SIGKILL、supervisor 自动重启，Isaac 冷启动要几分钟——**这一轮按失败处理，不要盲目重试** |
| GET 超时 | `Server did not respond within 30s for GET {path}` |
| 连接失败 | `Cannot connect to simulation server at http://127.0.0.1:8120/move. The server may not be started, may have crashed, or the freeze watchdog may have just SIGKILLed it — wait for /health to answer before retrying.` |

> 超时预算：POST 客户端等 600 s（服务端运动看门狗 550 s + 5 s 派发宽限），GET 等 40 s
> （服务端查询看门狗 30 s + 5 s）。`UniVTACMotionApi` 是独立的一套：POST 150 s / GET 30 s。

---

## 5. 使用示例

> 两例都是**特权语境**（prim 原点 + 可信四元数），其中的位姿数值与阈值对 agent 线（非特权）
> **不成立**；非特权的正确语义见 `univtac_skill/SKILL.md`。

### 5.1 标准触觉抓取-分类-放置流程（TactileApi）

```python
import numpy as np

def contacts_ok(fb):
    return fb["left_contact"] and fb["right_contact"]

grasp_pos, grasp_quat = sample_grasp_pose("prism")
goto_pose(grasp_pos, grasp_quat, z_approach=0.05)   # 一次到位，见 §3.1

fb = close_gripper()
if not contacts_ok(fb):
    open_gripper()
    close_gripper(force_threshold=25.0)

# 重量先测（§3.4）：轻握 27.7（28.5 只给几克重的刚性物体，见 §3.4 末）、
# 还搁在桌上读基线、抬 ~3cm、悬挂后再读
close_gripper(force_threshold=27.7)
base = get_tactile_data()
for _ in range(3):
    move_delta(dz=0.01)
hang = get_tactile_data()
shear = ((hang["left_marker_shear_dx"]  - base["left_marker_shear_dx"])
       - (hang["right_marker_shear_dx"] - base["right_marker_shear_dx"]))
weight = "heavy" if abs(shear) > 0.2 else "light"

# 纹理再测（§3.9）：两次握力的 anisotropy 增量，不是 coherence（旧判据已废弃）
close_gripper(force_threshold=27.5); light = get_tactile_data()
close_gripper(force_threshold=20.5); firm  = get_tactile_data()
d_depth = (min(light["left_depth_mm"], light["right_depth_mm"])
           - min(firm["left_depth_mm"],  firm["right_depth_mm"]))
d_aniso = np.mean([firm[f"{s}_marker_anisotropy"] - light[f"{s}_marker_anisotropy"]
                   for s in ("left", "right")])
texture = "rough" if d_aniso / max(d_depth, 1e-6) > 0.06 else "plain"
# 纹理判定是紧夹 ⇒ 必须在重量测完之后做（顺序锁死，§3.4）

# 放置前确认物体还在夹爪里（掉出去就重抓）
p, _, _ = get_object_pose("prism")
if not contacts_ok(get_tactile_data()) or p[2] < 0.03:
    goto_pose(grasp_pos, grasp_quat, z_approach=0.05)
    close_gripper()
    p, _, _ = get_object_pose("prism")

# 放置用世界系平移，不要用 goto_pose（会在轻握里把物体拧歪，见 §3.4）。
# position 是 prim 原点、bbox 是总尺寸 ⇒ pad 顶面 = pad_pos[2] + pad_ext[2]。
pad_pos, _, pad_ext = get_object_pose(
    "orange_pad" if texture == "rough" else "green_pad", return_bbox_extent=True)
move_delta(dx=pad_pos[0] - p[0], dy=pad_pos[1] - p[1])   # 已抬起 3cm，横移不会刮倒
while True:                                              # 每步 ≥2 mm（§3.2 下限）
    p, _, _ = get_object_pose("prism")
    if p[2] - (pad_pos[2] + pad_ext[2]) <= 0.002:
        break
    move_delta(dz=-max(p[2] - pad_pos[2] - pad_ext[2], 0.002))

open_gripper()      # 本任务要求松手；ViTaForge 类任务放到盘面即止、不要开爪（§3.4）
move_delta(dz=0.08)
check_success()
```

### 5.2 无触觉运动流程（MotionApi）

```python
import numpy as np

grasp_pos, grasp_quat = sample_grasp_pose("can")
goto_pose(grasp_pos, grasp_quat, z_approach=0.05)
close_gripper()
move_delta(dz=0.05); move_delta(dz=0.05)        # 抬离桌面
rotate_gripper("can", np.pi / 2, steps=6)

can_pos, _, bbox = get_object_pose("can", return_bbox_extent=True)
table_z = can_pos[2] - bbox[2] / 2.0
lower_dist = can_pos[2] - max(bbox) / 2.0 - table_z - 0.001

move_delta(dz=-lower_dist)
open_gripper()
move_delta(dz=0.06)
check_success()
```
