# 分层触觉控制架构(快速反射环 + 慢速决策环)

> 设计文档,描述 caP-X UniVTAC 触觉感知的**双层回路架构**:**服务器端确定性触觉反射(快速环)** 与 **LLM 慢速决策环**。
> 实现:2026-08-28。API 契约见 [univtac_api_reference.md](univtac_api_reference.md),agent 用法见 `univtac_skill/SKILL.md` 的 "Two-loop control" 章节。

## 1. 背景与动机

观测到的事实:LLM 没有充分使用触觉信息,或者说不知道怎么用。诊断结论:

- **呈现方式有问题**:现有触觉呈现是每侧 6 个冗余标量(contact / depth_mm / displacement_px / coherence / std × 2)。`_marker_stats` 早已算出剪切方向向量 `mean_vec`,却丢弃方向只留范数;没有图像通道;没有事件语义——LLM 拿不到"正在滑""卡住了""坐实了"这类判断。
- **时序适配有问题**:触觉是快速变化信号,LLM 只能低频观察。把触觉流式喂给 VLM 不现实(约 2 FPS 的渲染成本 + 上下文爆炸),LLM 每次都"晚一步"。

**结论:分层架构,不需要深度学习。** 手工几何特征对快速环足够;慢速环由 VLM 直接看原始图像。

## 2. 架构总览

```
LLM (慢速环,决策频率) ──┬── move_reflexive(stop_on={...}) ──> 事件摘要 digest
                        └── get_tactile_data() / get_tactile_image(side)
                            └── 决策点按需调用,绝不 mid-motion

服务器 (快速环,每 step ≈ 0.5 s) ── 控制循环内评估触觉事件,命中 → 冻结机械臂 → 返回 digest
```

- **快速环(本架构的核心新增)**:`move_reflexive` 把触觉评估放进 motion 内部。服务器在每一步控制频率评估事件,第一个启用的事件命中就冻结机械臂并返回事件摘要。LLM 无需轮询,一次调用拿回"发生了什么"。
- **慢速环(现有能力,重新定位)**:LLM 在决策点(反射停住之后、动作之间)读 `get_tactile_data()` 标量、按需取 `get_tactile_image()` 原始 GelSight marker 图存 scratch,像相机图像一样 zoom/检查。

## 3. 事件词汇表

停止条件是 7 个事件的 OR 组合。契约定义(键、默认阈值、逐条含义、digest schema)
以 [univtac_api_reference.md](univtac_api_reference.md) §3.11 为准,阈值常量单一
来源是 `capx/serving/tactile_reflex.py` 的 `DEFAULT_THRESHOLDS`。本处只记录设计要点:

- **阈值 grounded**:far_plane 34.0 / light_grasp 27.5 来自 `robot_cfg.py`;shear 2.0 /
  coherence 0.7 来自客户端 `detect_slip` 默认(实测标定值);`mean_disp` 与服务器
  `marker_displacement_px`(每 marker L2 均值)逐字一致,使 `shear_spike` 与
  `detect_slip` 同一阈值尺度(早期用过展平 L1,尺度不符,已修正)。
- **severity 序**(同一步多事件时决定 `terminated_by`):`hard_depth > slip >
  shear_spike > plateau > asymmetry > contact`。
- **`plateau` 窗口内重检接触**(逐步 `depth < far_plane - 0.1`),悬空波动不算坐实。
- **`shear_dir_deg` 带符号**(atan2(dy,dx),传感器 UV 帧)——顶入与卡死的剪切朝向
  不同,是 digest 里唯一的方向信号。

## 4. 关键机制(均已对照代码逐一验证)

1. **冻结必须双管齐下**。`plan_success=False` 单独挡不住机械臂:`set_arm` 的 `set_dof_positions(force=True)` 是**立即写入 physx** 的 kinematic teleport(`articulation.py`),`take_dense_action` 在 `_step` 之前调用它。因此触发时同时:
   - 置 `_TASK.plan_success = False`(冻结物理积分 + 跳过 delay);
   - 把 `_TASK._robot_manager.set_arm` 的**实例属性**影子化为 no-op(实例 `__dict__` 优先于类方法)。恢复用 `del instance.set_arm`——不能赋回 unbound function,会破坏 self 绑定。
2. **`_step_callback` 实例属性遮蔽**:`_step` 以 `self._step_callback(status_dict)` 调用(`_base_task.py`),实例属性优先于类 staticmethod → 安装闭包即可逐 step 拦截。安装/恢复走 `_TASK.__dict__` + `try/finally`。
3. **中止语义**:`move()` 无 try/except,入口有 `plan_success` 守卫;触发后剩余迭代 = no-op `set_arm` + early-return `_step`,手臂原地保持;`delay(10)` 在 `plan_success=False` 时自动跳过。`move()` 返回 True(仅规划失败返回 False)→ 可区分"触发了" vs "规划失败"。
4. **既有 `_sim_move_delta` 重试死代码**:规划失败后 `plan_success=False` 挡住下一次 `move()` 入口,后续 scale 的重试实际不会执行。reflex 版在 attempt 之间**显式恢复 `plan_success=True`** 修正此问题;`_sim_move_delta` 本身不重构(加法式约束)。触发后的 motion **绝不**在更小尺度重跑(会重演同一接触),直接 break。
5. **response_model 陷阱**:FastAPI 经 response model 序列化,未声明的字段会被剥离 → `MoveReflexiveResponse(ActionResponse)` 必须显式声明 `event` / `terminated_by`;4 个 shear 字段同样在 `TactileResponse` 上声明。
6. **渲染成本**:服务器 collect 模式 + save_frequency=0 时,每 step 必须 `_update_render()` 才有新鲜触觉 → 约 2 FPS。100 步 ≈ 50 s,接近服务器 120 s / 客户端 150 s 超时 → 文档要求 deltas ≤ 0.05 m。反射回调**每 step 都渲染**(慢速环的 `get_tactile_image` 每次也是完整渲染,mid-motion 禁止调用)。
7. **`_MARKER_REFERENCE`**:reset 时捕获的 marker[1] clone,零接触基线。sim 内部的 initial 帧不可用作参考,`_read_tactile_features` 与 `_sim_get_tactile_data` 同语义(reference 缺失回退 marker[0])。
8. **`mean_disp` 尺度 grounded**:`shear_spike` 的 2.0 px 阈值语义来自 `detect_slip` 的 `threshold_px=2.0`(施加在服务器 `marker_displacement_px` 上,即每 marker L2 均值)。因此 reflex 的 `mean_disp` 定义为与 `marker_displacement_px` 逐字一致,而非展平 L1(实现早期版本用过展平 L1,与阈值尺度不符,已修正)。
9. **回调永不 raise**:内部 try/except + error latch(`ctx.error`),异常后各步 no-op,`move()` 不会因回调异常解卷。回调不录像。

## 5. 代码映射

| 文件 | 内容 |
|---|---|
| `capx/serving/tactile_reflex.py`(新,纯模块,无 Isaac 依赖) | 阈值常量 `DEFAULT_THRESHOLDS`、`SideFeatures`、`extract_side_features`(marker `[2,M,2]` + reference + depth → 特征)、有状态 `TactileEventDetector`(滚动历史、running max、dominant 侧剪切方向、`summary`)、`evaluate`(severity 序 + max_steps) |
| `capx/serving/launch_univtac_server.py` | `TactileResponse` + 4 shear 字段;`StopOn` / `MoveReflexiveRequest` / `MoveReflexiveResponse`;`_read_tactile_features` / `ReflexContext` / `_install_reflex` / `_uninstall_reflex`;`_sim_move_reflexive`(prelude 逐行镜像 `_sim_move_delta`,注释 keep-in-sync);`/move_reflexive` 端点 |
| `capx/integrations/tactile/univtac_api.py` | `move_reflexive`(body = move_delta 同款 + `stop_on`;返回 8 字段修剪 tactile + `terminated_by` + `event`);`get_tactile_image(side)`(base64 PNG → `(H,W,3)` uint8,空/缺键 → None) |
| `univtac_skill/robo.py` | re-export `move_reflexive` / `get_tactile_image`,扩展 `__all__` |
| `univtac_skill/SKILL.md` | "Two-loop control" 章节:条件选择表、digest 阅读规则、显式取代慢速 while 循环接触模式 |
| `docs/univtac_api_reference.md` | §3.11 `move_reflexive`(stop_on 参考表 + digest schema)、§3.12 `get_tactile_image`,概览表更新 |
| `tests/integrations/test_tactile_reflex.py` | 25 个纯 CPU 测试:特征手算核对、6 事件含边界、evaluate 序、pydantic 模型、客户端 wire、hook 生命周期(duck-typed fake `_TASK`) |

## 6. 明确不做(v1 边界)

- eval `functions()` 不新增(robo.py 交互面即 v1;eval 暴露留待另行决定)。
- 不重构 `_sim_move_delta`(镜像复制 + keep-in-sync 注释;共享 helper 提取留作后续)。
- 不动 `univtac_action_log`、不动录像器、回调内不录像(触发帧留 v1.1)。
- 慢速环的 agent 侧推理策略由 SKILL.md 引导,不写代码。
- 冻结机制依赖 `set_arm` 影子化这一**实现细节**——如果 `take_dense_action` 的遥传路径在以后的重构中改变,冻结机制需要重新验证(见 §4.1)。

## 7. 验证

**无 sim(CI 可跑,已通过)**:`pytest tests/integrations/test_tactile_reflex.py tests/integrations/test_univtac_perception_semantics.py tests/integrations/test_skill_tools_zoom.py -q` → 48 全绿(31 + 17,含 6 个阈值覆写回归);`from robo import move_reflexive, get_tactile_image` 导入正常;prelude 与 `_sim_move_delta` 逐行一致。

**有 sim(insert_HDMI,2026-08-28 人工验证,全部通过)**:

1. `/tactile` 返回 4 个新 shear 字段,数值与手工 atan2(mean marker delta) 一致。
2. 悬空下压 `move_reflexive(dz=-0.005, stop_on={"contact": true})` → `success=True`、`terminated_by="contact"`、`min_depth_mm ≈ 30-33`,且 **EE 实际早停**(前后位移 << 完整 delta)——这是 set_arm 冻结补丁的决定性检查。
3. 已接触时触发 → 本次 motion 第一步即停(`contact_step=1`,EE 零位移)。
4. hard_depth:默认 20 mm 在 insert_HDMI **不可达**(插座咬住插头,深度恒 ~27.2 mm,压不动);覆写 `{"hard_depth": 28.0}` → 第一步触发、`terminated_by="hard_depth"`、EE 零位移。
5. 触发后下一次 `move_delta` 正常执行(证明 `plan_success` 恢复)。
6. `stop_on=None` 干净运动 → `success=True`、`terminated_by=None`、digest 仍有值(监控模式)。
7. 墙钟 ≈ 步数 × 0.21-0.55 s(2 FPS 预算 vs 120 s / 150 s 超时)。
8. 动作日志:触发运动单 atom,无 `_retry50`/`_retry75` 后缀(触发后不重试)。
9. 回放:episode_video 含 reflexive move 的 `:before`/`:after` 帧。

注:digest 的 `steps` 是全局 step_count(与动作日志同源);单次 motion 的评估步数看 `contact_step`。

**验证中抓到并修复的真 bug**:

- **plan_success 泄漏**:`_sim_move_reflexive` 收尾原为「仅未触发时恢复 `plan_success`」——触发路径保持 False,下一次 `move()` 入口守卫直接返回 False(step 不变)。修复:无条件恢复。回归:触发后 `move_delta` 正常。
- **阈值覆写被丢弃**:`_merge_thresholds` 以 stop_on 短名键(`hard_depth` 等)查长名键字典,恒 miss——所有数值覆写静默失效,`{"hard_depth": 28.0}` 实际用默认 20.0。修复:短名→长名别名映射(plateau 的 dict 分支保留);回归:6 个覆写用例。
