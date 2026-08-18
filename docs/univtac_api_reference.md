# UniVTAC API Reference

> LLM 代码生成所用的机器人控制 API 文档。多个 API 类共享同一台仿真服务器
> (`launch_univtac_server.py`)，区别在于是否暴露触觉感知函数、夹爪控制策略，以及
> **物体位姿来自 ground truth 还是视觉感知**。

---

## 0. 特权模式 vs 非特权模式

仿照原始 cap-x 的 `privileged` 区分：

| | **特权（privileged）** | **非特权（non-privileged）** |
|---|---|---|
| 位姿来源 | 服务器 `/object_pose` 直接读仿真器 ground truth（prim 位姿） | SAM3 文本分割 + 深度反投影（相机感知） |
| API 类 | `UniVTACTactileApi` / `UniVTACMotionApi` | `UniVTACPerceptionApi` |
| 配置文件 | `*_multiturn.yaml` 等 | `*_non_privileged.yaml` |
| 需要的服务 | 仅 UniVTAC 服务器 (8120) | UniVTAC (8120) + SAM3 (8114) |

`UniVTACPerceptionApi` 继承 `UniVTACTactileApi`，只重写 `get_object_pose`；运动、
触觉、`check_success` 均继承不变（`sample_grasp_pose` 仍是 ground-truth 抓取采样，
抓取感知后续再做）。

> **注意：非特权 `get_object_pose` 的 `position` 是分割点云的质心，不是 prim 原点**
> （特权模式返回 prim 原点），四元数不可靠（恒等）。所以按质心语义写非特权的
> PROMPT/ORACLE，不要照搬原点的「1 cm 上方」阈值。

---

## 1. 三个 API 类

| | `UniVTACTactileApi` | `UniVTACMotionApi` | `UniVTACPerceptionApi` |
|---|---|---|---|
| **文件** | `capx/integrations/tactile/univtac_api.py` | `capx/integrations/tactile/univtac_motion_api.py` | `capx/integrations/tactile/univtac_perception_api.py` |
| **用途** | 标准评测（触觉 + 运动，特权位姿） | Ablation 基线（仅运动，无触觉） | 非特权：位姿来自 SAM3 + 深度 |
| **配置文件** | `*_multiturn_vdm.yaml` 等 | `*_no_tactile*.yaml` | `*_non_privileged.yaml` |
| **触觉感知函数** | ✅ 暴露 | ❌ 不暴露 | ✅ 暴露（继承） |
| **Motion 返回值** | 返回 `dict`（含触觉反馈） | 返回 `None` | 返回 `dict`（继承） |
| **夹爪控制** | 自适应夹持（触觉反馈停止） | 位置闭合 | 自适应夹持（继承） |
| **`get_object_pose`** | ground truth prim 位姿 | ground truth prim 位姿 | SAM3 分割质心 |

---

### 非特权感知所需的 `/observation` 新字段

服务器 `/observation` 在原有 `*_rgb_base64` / `*_depth_base64`（归一化、丢失尺度）
之外，新增了可做米制反投影的字段（`head`/`wrist` 对称）：

| 字段 | 含义 |
|---|---|
| `{cam}_depth_float32_base64` | 米制深度（float32 原始字节 → base64），按 `{cam}_image_shape` reshape 成 (H, W) |
| `{cam}_image_shape` | `[H, W]` |
| `{cam}_intrinsics` | 3×3 内参矩阵（fx/fy/cx/cy） |
| `{cam}_cam_pos_w` | 相机世界位置 `[x, y, z]`（米） |
| `{cam}_cam_quat_w_ros` | 相机世界朝向 `[w, x, y, z]`（ROS 约定：相机光轴 +Z，直接映射相机系→世界系） |

---

## 2. 函数对比总览

| 函数 | TactileApi | MotionApi | 类别 |
|---|---|---|---|
| `goto_pose` | ✅ → `dict` | ✅ → `None` | 运动 |
| `move_delta` | ✅ → `dict` | ✅ → `None` | 运动 |
| `open_gripper` | ✅ → `dict` | ✅ → `None` | 运动 |
| `close_gripper` | ✅ → `dict` | ✅ → `None` | 运动 |
| `rotate_gripper` | ✅ → `dict` | ✅ → `None` | 运动 |
| `get_object_pose` | ✅ | ✅ | 感知 |
| `sample_grasp_pose` | ✅ | ✅ | 感知 |
| `check_success` | ✅ | ✅ | 感知 |
| `get_tactile_data` | ✅ | ❌ | 触觉 |
| `detect_contact` | ✅ | ❌ | 触觉 |
| `detect_slip` | ✅ | ❌ | 触觉 |

---

## 3. 详细函数参考

### 3.1 `goto_pose` — 移动到目标位姿

```
goto_pose(position: (3,) ndarray, quaternion_wxyz: (4,) ndarray,
          z_approach: float = 0.0) -> dict | None
```

将末端执行器移动到目标位姿。`z_approach=0.1` 用于抓取/放置（在目标上方加一个接近航点），
`z_approach=0.0` 用于自由空间运动。

| API | 返回值 |
|---|---|
| **TactileApi** | `dict` — 触觉反馈：`left_contact`, `right_contact` (bool), `left_depth_mm`, `right_depth_mm` (float), `left_marker_displacement_px`, `right_marker_displacement_px` (float), `left_marker_coherence`, `right_marker_coherence` (float) |
| **MotionApi** | `None` |

**错误：** 运动规划失败时抛出 `RuntimeError("Motion failed: ...")`。

---

### 3.2 `move_delta` — 增量移动

```
move_delta(dx=0.0, dy=0.0, dz=0.0, drx=0.0, dry=0.0, drz=0.0,
           coord="world") -> dict | None
```

接触丰富任务（插入、对齐）的主要原语。

**每步限制：**
- 平移：**最大 0.10 m（10 cm）每轴**
- 旋转：**最大 0.5 rad（约 30°）**
- 超出限制立即被拒绝并返回清晰的错误信息
- 目标 Z 必须保持在桌面上方（Z >= 0.01 m）

| API | 返回值 |
|---|---|
| **TactileApi** | `dict` — 触觉反馈。可用于接触即停的闭环控制 |
| **MotionApi** | `None` |

**闭环示例（仅 TactileApi）：**
```python
while True:
    fb = move_delta(dz=-0.005)
    if fb["left_contact"] or fb["right_contact"]:
        break  # 接触到表面，立即停止
```

**参数：** `coord="world"` 在世界坐标系中移动；`coord="ee"` 在末端执行器本地坐标系中移动。

> **旋转分量（drx/dry/drz）的坐标系陷阱：** `coord="world"`（默认）下，旋转是把
> 末端执行器的**位姿绕世界原点（机器人基座）**整体旋转，位置 `p` 也会被旋转矩阵
> 转动——工作区在 x≈0.55m 处，0.04 rad 的旋转就会让夹持物体沿弧线甩动约 2cm。
> 因此**不要**用 `move_delta` 的世界系旋转来微调夹持物体的朝向。要原地旋转（位置
> 不动）请用 `coord="ee"`；夹持物体的定向调整优先用 `rotate_gripper`。

---

### 3.3 `open_gripper` — 打开夹爪

```
open_gripper() -> dict | None
```

完全打开夹爪。

| API | 返回值 |
|---|---|
| **TactileApi** | `dict` — 打开后的触觉反馈 |
| **MotionApi** | `None` |

---

### 3.4 `close_gripper` — 闭合夹爪抓取物体

```
close_gripper(force_threshold: float | None = None) -> dict | None
```

闭合夹爪以抓取物体。

| API | 行为 |
|---|---|
| **TactileApi** | 自适应夹持：利用触觉反馈实时监测凝胶变形量，达到目标深度后自动停止，确保握持稳固且不压坏物体。返回闭合后的触觉反馈 `dict`。 |
| **MotionApi** | 简单位置闭合。返回 `None`。 |

**参数（仅 TactileApi 生效）：**
- `force_threshold`：目标凝胶变形深度（mm）。`None` 使用任务默认值。数值越大抓力越紧。

---

### 3.5 `rotate_gripper` — 旋转夹爪

```
rotate_gripper(object_name: str, angle_rad: float, steps: int = 4) -> dict | None
```

旋转持物的夹爪。用于大角度旋转（>0.3 rad），而非使用 `move_delta`。
服务端会验证物体是否实际旋转（至少达到请求角度的 30%），若物体滑脱则报错。

**参数：**
- `object_name`：被持物体的名称
- `angle_rad`：总旋转角度（弧度）
- `steps`：增量步数（默认 4）

| API | 返回值 |
|---|---|
| **TactileApi** | `dict` — 旋转后的触觉反馈（可用于检测物体是否滑脱） |
| **MotionApi** | `None` |

---

### 3.6 `get_object_pose` — 获取物体位姿

```
get_object_pose(object_name: str, return_bbox_extent: bool = False)
    -> tuple[ndarray, ndarray, ndarray | None]
```

获取物体的 3D 位姿。支持名称前缀匹配（例如 `"prism"` 可匹配 `"rough_prism"`）。

**返回值：** `(position_XYZ_m (3,), quaternion_WXYZ (4,), bbox_or_None (3,) | None)`

- `position`：USD prim 的**世界原点** XYZ（米）。**注意：是 prim 原点，不一定是几何中心。** 任务物体都以原点在底面的方式著录（静止时贴桌面，z≈0.002–0.01）
- `quaternion_wxyz`：WXYZ 格式的四元数
- `bbox`：如果 `return_bbox_extent=True`，返回世界包围盒**总尺寸** [dx, dy, dz]（米，完整高度/宽度，**不是半长**）；否则为 `None`

> **放置高度（原点在底面的物体）：** 物体顶面高度 = `position[2] + bbox[2]`。
> 把 prism 放到 pad 上：`target_z = pad_pos[2] + pad_ext[2] - 0.001`（1 mm 下压余量）。
> 注意不要用 `bbox/2`：position 已在底面，bbox 已是总高，再取半会把物体悬空半个身高。

**两个 API 均可用。**

---

### 3.7 `sample_grasp_pose` — 获取抓取位姿

```
sample_grasp_pose(object_name: str) -> tuple[ndarray, ndarray]
```

返回桌面物体的抓取位姿。配合 `goto_pose(..., z_approach=0.05)` 可实现安全的自上而下接近。

**返回值：** `(position_XYZ_m (3,), quaternion_WXYZ (4,))`

**两个 API 均可用。**

---

### 3.8 `check_success` — 检查任务是否完成

```
check_success() -> bool
```

检查任务是否完成。在代码末尾调用以验证。

**两个 API 均可用。**

---

### 3.9 `get_tactile_data` — 读取触觉传感器（仅 TactileApi）

```
get_tactile_data() -> dict
```

读取 GelSight Mini 触觉传感器。

**返回字典包含：**
| 键 | 类型 | 描述 |
|---|---|---|
| `left_contact` | bool | 左指尖是否有接触 |
| `right_contact` | bool | 右指尖是否有接触 |
| `left_depth_mm` | float | 左凝胶变形量（mm） |
| `right_depth_mm` | float | 右凝胶变形量（mm） |
| `left_marker_displacement_px` | float | 左指尖标记位移（像素，自本轮 reset 无接触基准以来的平均绝对位移——随任何胶形变（接触/插入）也随滑移上升，单独不是干净的滑移信号） |
| `right_marker_displacement_px` | float | 右指尖标记位移（像素，同上） |
| `left_marker_coherence` | float | 左指尖标记运动一致性 (0-1) — 高 = 标记一起移动（剪切/打滑，或光滑表面），低 = 标记非相干移动（压缩/形变/追踪丢失，或粗糙表面）。插入类任务中与位移结合判滑移，grasp_classify 中用作纹理判别 |
| `right_marker_coherence` | float | 右指尖标记运动一致性 (0-1，同上) |
| `left_marker_std_px` | float | 左指尖标记位移分布 (像素) |
| `right_marker_std_px` | float | 右指尖标记位移分布 (像素) |

> **纹理判别：** 粗糙表面抓握时标记流散乱（相干度低 ~0.55），光滑表面
> 一致剪切（相干度高 ~0.75）。抓稳后读一次相干度即可分类，无需旋转。`rotate_gripper` 的
> 滑脱校验无法区分两种纹理（两棱柱摩擦系数相同，滑移行为一致），不要作为主信号。

> **注意：** 所有 motion 函数（`goto_pose`、`move_delta`、`open_gripper`、
> `close_gripper`、`rotate_gripper`）在 TactileApi 中也返回相同字段的 dict，
> 因此大部分场景下无需单独调用此函数。

---

### 3.10 `detect_contact` — 检测接触（仅 TactileApi）

```
detect_contact() -> bool
```

检查任一指尖是否与物体接触。`get_tactile_data()` 的便捷封装。

---

### 3.11 `detect_slip` — 检测滑移（仅 TactileApi）

```
detect_slip(threshold_px: float = 2.0, coherence_threshold: float = 0.7) -> bool
```

检查被抓物体是否正在滑脱。滑移判定基于**自上次调用以来的位移增量**：物体真正打滑/卡住时标记继续沿同一方向剪切（coherence 高），位移比上次读数增长超过阈值；持稳状态——哪怕之前已经滑过——不会再报。**第一次调用只建立参考读数（返回 False）**，请在被测运动之前先调用一次建立基线，或接受首个运动不会被判定。

**参数：**
- `threshold_px`：位移增量阈值（像素，相对上次调用）。1.0 = 敏感，5.0 = 宽松。
- `coherence_threshold`：标记相干度下限 (0-1)。约 0.7 区分均匀剪切（滑移）与压缩/形变。

> **注意：** `get_tactile_data()` 里的 `*_marker_displacement_px` 仍是「自本轮 reset 以来的绝对位移」——一次真卡死后会持续保持高位，不要直接拿它和阈值比较；`detect_slip` 内部已做增量处理。增量读数为负（标记松弛回弹）不算滑移。

---

## 4. 错误处理

所有 HTTP 调用均受错误翻译保护。LLM 在 `{console_stderr}` 中看到清晰且可操作的错误信息：

| 场景 | LLM 看到的错误 |
|---|---|
| 运动规划失败 | `RuntimeError: Motion failed: <服务端消息>` |
| 请求超时 | `RuntimeError: Server did not respond within 90s for POST /move. ... This usually means the motion planner (cuRobo IK) is stuck. Check that the target pose is physically reachable ...` |
| 服务器崩溃/未启动 | `RuntimeError: Cannot connect to simulation server at http://127.0.0.1:8120/move. The server may have crashed or not been started yet.` |
| `move_delta` 步长过大 | `RuntimeError: Motion failed: \|dz\|=0.213 exceeds per-step limit (0.050). Use multiple smaller steps instead.` |
| 目标 Z 低于桌面 | `RuntimeError: Motion failed: Target Z=-0.042 is below the table surface. Reduce the downward delta.` |
| 物体未找到 | `RuntimeError: Object 'can' not found in scene` |
| 旋转时物体滑脱 | `RuntimeError: Motion failed: Object 'prism' rotated only 1.1° (requested 8.6°). The object may not be firmly gripped — try closing the gripper again with a higher force_threshold, or re-grasp.` |

---

## 5. 使用示例

### 5.1 标准触觉抓取-分类-放置流程（TactileApi）

```python
import numpy as np

def contacts_ok(fb):
    return fb["left_contact"] and fb["right_contact"]

# 获取抓取位姿并以安全接近方式到达
grasp_pos, grasp_quat = sample_grasp_pose("prism")
goto_pose(grasp_pos, grasp_quat, z_approach=0.05)

# 抓取并验证两侧指尖均有接触
fb = close_gripper()
if not contacts_ok(fb):
    open_gripper()
    close_gripper(force_threshold=25.0)

# 抬起物体，待凝胶稳定后再探测
move_delta(dz=0.05)

# 纹理分类首选标记流相干度：
# 粗糙表面标记流散乱 → 相干度低 (~0.55)；光滑表面一致剪切 → 相干度高 (~0.75)。
fb = get_tactile_data()
coh = 0.5 * (fb["left_marker_coherence"] + fb["right_marker_coherence"])
texture = "rough" if coh < 0.65 else "plain"

# 放置前确认棱柱仍在夹爪中（探测可能使其滑脱）
prism_pos, _, prism_ext = get_object_pose("prism", return_bbox_extent=True)
fb = get_tactile_data()
if not contacts_ok(fb) or prism_pos[2] < 0.03:
    open_gripper()
    goto_pose(grasp_pos, grasp_quat, z_approach=0.05)
    close_gripper()
    prism_pos, _, prism_ext = get_object_pose("prism", return_bbox_extent=True)

# 放置高度：position 是 prim 原点（prism/pad 均在底面），bbox 是总尺寸，
# 所以 pad 顶面 = pad_pos[2] + pad_ext[2]。
target_pad = "orange_pad" if texture == "rough" else "green_pad"
pad_pos, _, pad_ext = get_object_pose(target_pad, return_bbox_extent=True)
ee_pos = np.array(grasp_pos) + np.array([0.0, 0.0, 0.05])
offset = ee_pos - prism_pos                 # 末端 → prism 原点
target_prism = np.array([pad_pos[0], pad_pos[1],
                         pad_pos[2] + pad_ext[2] - 0.001])   # 1 mm 下压
goto_pose(target_prism + offset, grasp_quat, z_approach=0.1)

# 开爪前用位姿校验下降高度：prism 底应已贴住 pad 顶（宁低勿高）
for _ in range(10):
    p, _, _ = get_object_pose("prism")
    gap = p[2] - (pad_pos[2] + pad_ext[2])
    if gap <= 0.002:
        break
    move_delta(dz=-max(gap, 0.002))

open_gripper()
move_delta(dz=0.08)
check_success()
```

### 5.2 无触觉运动流程（MotionApi）

```python
import numpy as np

grasp_pos, grasp_quat = sample_grasp_pose("can")
goto_pose(grasp_pos, grasp_quat, z_approach=0.05)

close_gripper()

# 抬起罐子
move_delta(dz=0.05)
move_delta(dz=0.05)

# 旋转罐子至竖直
rotate_gripper("can", np.pi / 2, steps=6)

# 使用几何信息计算放置高度
can_pos, _, bbox = get_object_pose("can", return_bbox_extent=True)
can_height = max(bbox)
table_z = can_pos[2] - bbox[2] / 2.0
lower_dist = can_pos[2] - can_height / 2.0 - table_z - 0.001

move_delta(dz=-lower_dist)
open_gripper()
move_delta(dz=0.06)
check_success()
```
