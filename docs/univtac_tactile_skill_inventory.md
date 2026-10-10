# 触觉技能库盘点

> 目的：把散落在各处 prompt 与 API docstring 里的触觉知识清点出来，判断能不能收进一个
> 「技能库」，以及按 **技能 × 任务类型** 怎么切。
> 方法：四个来源全文抽取（13 个任务 PROMPT、共享 API docstring、两份手册），加机械重复度统计。
>
> **这是存档，不是现状（最后更新 2026-10-06）**。活文档是
> `docs/univtac_tactile_skill_library.md`（库长什么样、为什么）和
> `capx/tactile_skills/MAINTENANCE.md`（怎么往里加）；基线数字以 MAINTENANCE §五 为准。
> 本文留着两样别处没有的东西：**§4.5 那 11 条已证伪的负面知识**，和 **§6.5–§6.10 的
> 逐轮经过**（哪一轮死在什么上）。§1–§5 是 2026-10-06 的盘点原文，条数停在 52 条。
>
> **当时的结论（2026-10-06）**：§6 里选定的方案 ① 已实施完毕，见 **§6.5 已落地的东西**——
> §4 的 56 条活技能已全部写进 `capx/tactile_skills/`，配一套路由和**默认开启**的 prompt 入口
> （§6.6 记用法搬迁，§6.7 记合并，当时为 **52 条**）。
> §4.5 的 11 条已证伪**没有**进库，理由见那一节。
>
> 位置标记：
> - `API·<方法>[<标签>]` = `capx/integrations/tactile/univtac_api.py` 里该方法 docstring 中带
>   该 `[section:tag]` 的段落；不带标签的是人人可见的正文。
> - `PROMPT·<任务>` = `capx/envs/tasks/tactile/univtac_<任务>.py` 的 `PROMPT` 常量。
> - `REF§x` = `docs/univtac_api_reference.md`；`SKILL§` = `univtac_skill/SKILL.md`。

---

## 1. 现状：触觉知识散在四个地方

| 来源 | 是什么 | 体量 | 谁看得到 |
|---|---|---|---|
| `univtac_api.py` 的 docstring | 34 个配置共用的 API 说明书：只讲函数/参数/返回字段**是什么** | 10 个可见函数合计 1,407 词 | 13 个任务**都拿到同一份**（`api_doc_sections` 已删） |
| 13 个任务模块的 `PROMPT` | 每个任务的目标（触觉读法已移出到技能库） | 合计 3,012 词 | 各自任务（YAML 可整段覆盖） |
| `docs/univtac_api_reference.md` | 给人看的 API 参考与标定记录（含"已证伪"章节） | 610 行 | 人，不进 prompt |
| `univtac_skill/SKILL.md` | 非特权 agent 线的操作手册 | 294 行 | agent 线，与 eval 线是两套 |

**2026-10-06 起，"怎么用"从第一栏搬到了技能库**（§6.6）。原来按 `api_doc_sections` 分发的
7 个可选段落（螺纹 1,713 词 / 重量 720 / 纹理 525 / 硬度 493 / 记忆匹配 258 / 位置 235 /
力伺服 117，合计 4,060 词）连同机制本身一起删除；同一批知识现在按任务**类型**投递
（`taxonomy.yaml`），且 13 个任务全部开启。

**每个任务实际看到的字数**（PROMPT + API 文档 + 技能块）：

| 任务 | PROMPT | API 文档 | 技能块 | 合计 | 类型 |
|---|---|---|---|---|---|
| 灯泡拧入 | 551 | 1,407 | 557 | 2,515 | screw |
| 硬度分类 | 558 | 1,407 | 612 | 2,577 | classify |
| 记忆匹配 | 509 | 1,407 | 630 | 2,546 | classify |
| 重量分类 | 503 | 1,407 | 647 | 2,557 | classify |
| 纹理分类 | 207 | 1,407 | 610 | 2,224 | classify |
| 插入 HDMI | 217 | 1,407 | 497 | 2,121 | insert |
| 插入孔 | 74 | 1,407 | 497 | 1,978 | insert |
| 插入管 | 68 | 1,407 | 497 | 1,972 | insert |
| 搬运 5 个 | 48–151 | 1,407 | 451 | 1,906–2,009 | manipulate |

（搬之前是 2,113–4,321 词，见本文件历史版本；现在区间收窄到 1,906–2,577。）

任务类型（仓库里没有正式枚举，这是按目录与标签推的）：

| 类型 | 任务 | 触觉的角色 |
|---|---|---|
| 分类 classify | weight / hardness / grasp_classify、memory_match（多轴） | 触觉是**答案本身** |
| 插入 insert | insert_hdmi / insert_hole / insert_tube | 触觉是**过程信号**（接触、倾斜、卡住） |
| 搬运 manipulate | lift_bottle / lift_can / put_bottle_in_shelf / pull_out_key / tactile_transfer | 触觉只在接触与放置时出现 |
| 螺纹 screw | bulb_tightening | 触觉是**唯一裁判**（方向、导程、坐底） |

---

## 2. 仓库里已有的四个类似机制（先回答"以前做过吗"）

**做过，但都不是"按任务类型分类的触觉知识库"。** 四个各自解决别的问题：

| 机制 | 在哪 | 它是什么 | 为什么不是这个库 |
|---|---|---|---|
| 运行时技能库 | `capx/skills/` | 从成功的 trial 代码里抽函数、出现 ≥2 次就升格，落 `.capx_skills.json` | 抽的是**代码片段**不是知识；`evolve_skill_library` 默认关，没有任何 YAML 打开 |
| 离线技能编译 | `scripts/skill_library_compilation/` | 用 LLM 把生成代码整理成 `outputs/skill_library.txt`，分 perception/motion/grasping | 产物是给人读的文本，不进任何 prompt |
| agent 线手册 | `univtac_skill/SKILL.md` | 非特权 agent 的操作手册 + `tools/` 原语 + 逐任务记忆 | 只服务 agent 线；**两份手册里完全没有灯泡**——两条线的知识已经分叉 |
| 文档段落标签 | `API·*[section:tag]` + `api_doc_sections` | 按"任务需要什么能力"分发 API 文档段落 | 曾经最接近的祖先：7 个标签就是 7 个能力簇。**2026-10-06 已整体删除**，知识改按任务类型从技能库投递（§6.6） |

结论：这次要做的事，本质是**把第四个机制从"标签级"细化到"技能级"，并把另外三个散落处的知识收进来**。

---

## 3. 重复实测（只统计，不算观点）

**PROMPT 之间**：

- 重量分类与硬度分类：**一段 206 词的「搬运与放置」逐字相同**，另有目标模板（36 词）、
  场景命名（26 词）、"陷阱"说明（34 词）三段也逐字相同。
- 13 个任务共用的开头（20 词）与结尾（12 词）模板块：合计 ~380 词。
- 插入孔与插入管：**一句"小步增量"逐字相同**，另有一段目标描述同义。
- 可在 PROMPT 层直接消掉的重复：**~700 词**。

**API docstring 内部**（同一事实写了几遍）：

| 事实 | 写了几处 |
|---|---|
| 深度刻度（34 / 27–28 / 20，越小越紧） | 5 |
| 两垫镜像 ⇒ 同一滑移符号相反 | 3 |
| 重量读剪切差分 | 2（一处 61 行，一处 8 行浓缩） |
| 纹理只在各向异性里 | 2 |
| 松握必须读满 34.0 mm 远平面 | 3 |
| 标定数字不能跨物体搬 | 2 |

**docstring 与 PROMPT 之间**：同一技能两处措辞不同。最明显的是灯泡的四个技能 —— PROMPT 说
"怎么做"，把长度（30°/45°）、阈值（0.15 mm）、只读去程、置信度 0.70、坐底读比例、8 mm 起算
这些细节留给了 docstring，**两处必须一起读才完整**。

**docstring 与手册之间**：手册独有的使用策略（何时该用哪条规则、停止后怎么重规划）在
docstring 里没有；docstring 独有的数字（阈值、标定）在手册里也没有。两边都在教同一批技能。

---

## 4. 技能清单（合并去重后 56 条）

四条抽取合计 92 条，去掉互相重复后是下面 56 条。"现有副本"= 同一技能现在被写在第几个地方。
每条都标了**谁需要**（任务类型）。带 ⚠ 的是"陷阱"，是这一条最容易踩反的地方。

### 4.1 读数类 S —— 从凝胶读数得出物理量（22 条）

| # | 技能 | 一句话 | 现有副本 | 谁需要 |
|---|---|---|---|---|
| S1 | depth-grip-scale | | 指尖深度就是握紧程度的尺子：34 mm=空手/远平面，27–28=轻握，20=硬顶；⚠ **0.0 是另一个哨兵**——缺读数回 0.0，在标尺上读成"最硬" | API×5 + REF + SKILL | 全部 |
| S2 | gel-shear-weight | 重量读**两垫剪切的差**（镜像安装把共同挤压消掉、把负载留下），轻≈0 / 重 0.7–1.0 px，界 0.2 px | API·close[weight] + API·get_tactile_data[weight] + PROMPT·weight + REF§3.4 | 分类 |
| S3 | weight-baseline-unloaded | ⚠ 悬挂前必须先取无载基线；两垫剪切差 >0.4 px 说明已被提起，重物会读成轻 | API·close[weight] + REF§3.4 | 分类 |
| S4 | grip-force-by-mass | 握力按质量选：几十克用 28.5，300 g 罐要 ~27.7；**更紧反而把重量藏起来** | API·close[weight] + REF§3.4 | 分类 |
| S5 | lift-follow-height | 没有天平就比"抬起来跟不跟手" | API·close[weight] | 分类 |
| S6 | depth-is-not-weight | ⚠ 别拿开合深度/标记位移当重量：那是握紧程度，不是负载 | API×2 + PROMPT·weight | 分类 |
| S7 | hardness-two-closes | 软硬=两次位置指令（0.10→0.06）的**深度差**：硬掉 ~1.6 mm、软 ~0.8，界 1.2 | API·close[hardness] + PROMPT·hardness + REF§3.4 | 分类 |
| S8 | position-mode-for-yield | ⚠ 测屈服必须位置指令 + `adaptive=False`；伺服开着会把软的读成硬的（符号反过来） | API·close[position,hardness] + REF§3.4 | 分类 |
| S9 | finger-position-grip | 刚体能顶住手指时，用位置（0=闭 1=开）而不是深度定握点 | API·close[position] | 分类 |
| S10 | texture-only-in-anisotropy | 只有**各向异性**看得见纹理；其余标量都是整幅图像的平均，平均看不见结构 | API·get_tactile_data[texture]×2 + PROMPT·grasp + REF§3.9 | 分类 |
| S11 | texture-slope-two-grips | 纹理=轻/紧两次握的各向异性增量 ÷ 深度增量，界 0.06/mm | API·get_tactile_data[texture] + PROMPT·grasp + REF§3.9 | 分类 |
| S12 | can-texture-difference | 罐子用两次硬度握的各向异性**差**（界 0.02），且罐子必须刚性 | API·get_tactile_data[texture] | 记忆匹配 |
| S13 | shear-vs-displacement | 按要回答的问题选通道：问"往哪边"用剪切，问"动没动"用位移 | API·get_tactile_data | 全部 |
| S14 | two-sided-hold | 判"抓住了"看两个接触标志 + 两侧深度都在量程内，**别要求两侧位移对称** | API·get_tactile_data + PROMPT·memory + REF§3.9 | 全部 |
| S15 | slip-pair-baseline | 判滑移前先空调一次建基线（首调只建参考、不判）；判据=位移增量 >2 px 且 coherence >0.7 | API·detect_slip + REF§3.11 + SKILL | 全部 |
| S16 | contact-flag-not-proximity | ⚠ 找物体靠**命令位置**，别等接触标志——空手下降它永远是 False | API·detect_contact + API·move_delta + REF§3.10 | 全部 |
| S17 | reference-depth-offset | 两侧深度差是常数 ~0.3 mm；"更紧"的那侧取 min(L,R) | REF§3.4 | 全部 |
| S18 | far-plane-check | 松握必须读满 34.0 mm 远平面，否则这一握无效 | API×3 | 分类 |
| S19 | confidence-gate | ⚠ 标记丢失会给出**大小合理**的假读数；唯一办法是置信度门（项目用 0.70） | API·rotate_gripper[screw] + PROMPT·bulb | 螺纹（通用） |
| S20 | follow-is-signal | 低跟随、两垫不对称是数据不是垃圾；有效性只由 34 mm 松态 + 真实紧态定义 | API·close[memory_match] | 记忆匹配 |
| S21 | close-lands-on-target | 给了 `force_threshold` 的闭合是伺服的，落点≈目标（±0.05 mm）；读数对不上是**握歪了**，不是要减掉的那个误差 | API·close[servo] + REF§3.4 | 分类 |
| S22 | no-calibration-transfer | ⚠ 一类物体上量出的标定数字不能搬到另一类 | API×2 | 分类 |

### 4.2 动作类 A —— 用触觉驱动/终止动作（17 条）

| # | 技能 | 一句话 | 现有副本 | 谁需要 |
|---|---|---|---|---|
| A1 | reflex-stop-on-events | 把触觉事件判定放进服务端：接触 / 平台期 / 硬顶 / 滑移 / 剪切尖峰 / 不对称，触发即冻结 | API·move_reflexive + SKILL§Two-loop + REF§3.12 | 全部 |
| A2 | descent-until-contact | 下降用小步 + 读到接触就停，别命令一个固定深度 | API·move_delta + SKILL | 全部 |
| A3 | one-call-approach | 到抓取位姿要**一次** `goto_pose` 到位；分两次会差 ~1 mm 并把物体拖着走 | API·goto_pose + REF§3.1 | 全部 |
| A4 | lateral-only-align | 对孔只做横向微调，别在深度上试探 | PROMPT·insert_hole/tube | 插入 |
| A5 | small-step-insertion | 插装一律小步增量；单步下限 2 mm ⇒ 最后 1 mm 无法闭环 | PROMPT·insert_* + REF§3.2 + SKILL | 插入 |
| A6 | asymmetry-means-misaligned | 两垫深度差 = 偏斜，用它纠姿态 | PROMPT·insert_* + API·move_reflexive | 插入 |
| A7 | stall-means-jam | 推不动/滑移持续 = 卡住，退回来重试而不是加力 | PROMPT·insert_* + REF§3.14 | 插入 |
| A8 | depth-stop-seating | 压到位就停，靠位置不靠力 | PROMPT·insert_* | 插入/搬运 |
| A9 | preserve-existing-grasp | 已经抓好的别中途松开重抓 | PROMPT·manipulation | 搬运 |
| A10 | thread-direction-by-legs | 螺纹方向：正反两条腿比剪切符号，**只读去程**（往返残差是迟滞）；腿要够长（±4° 的 S/N<1） | PROMPT·bulb + API·rotate_gripper[screw] | 螺纹 |
| A11 | lead-by-overshoot | 导程：故意超进给一转，读回滞后量反解真实导程 | API·rotate_gripper[screw] + PROMPT·bulb | 螺纹 |
| A12 | feed-must-match-lead | ⚠ 扭转与下降必须锁在同一个导程上；进给差了 IK 直接拒 | API·rotate_gripper[screw] | 螺纹 |
| A13 | seat-by-slide-collapse | 坐底=滑移坍缩到指令下降的一小部分（比例 ∝ 导程，约 0.13）并配相干门 | API·rotate_gripper[screw] | 螺纹 |
| A14 | mirrored-shear-slide | 滑移只读**左−右**，不读任何单侧；18 px/mm 是差值的增益，不要再除 2 | API·rotate_gripper[screw] | 螺纹 |
| A15 | lattice-pitch-budget | 晶格间距是单次可信读数的上限（~0.3 mm 可定量，以上只定符号） | API·rotate_gripper[screw] | 螺纹 |
| A16 | wrist-limit-segmentation | 腕关节行程有限（每把拧入 ~120°）⇒ 分段 + 松手空转回 + 重抓 | PROMPT·bulb + API·rotate_gripper[screw] | 螺纹 |
| A17 | orientation-fix-high | 改姿态要在**高空**用工具系（`rotate_gripper` / `coord="ee"`），别用世界系、别在目标旁边转 | SKILL + REF§3.2/§3.5 | 搬运/插入 |

### 4.3 放置类 P —— 把东西放下并确认（7 条）

| # | 技能 | 一句话 | 现有副本 | 谁需要 |
|---|---|---|---|---|
| P1 | world-frame-transport | 搬运用世界系 `move_delta`；`goto_pose` 会重指腕姿态，落地时把物体拧歪（11–45° vs 0.2–0.3°） | PROMPT×2 + API·goto_pose + REF§3.1/§3.4 | 全部 |
| P2 | lift-before-lateral | 横向移动前先抬离台面 ~3 cm，否则把物体掀翻 | PROMPT×2 + REF§3.4 + SKILL | 全部 |
| P3 | seat-height-from-plate | 落座高度 = 台面高 + 物体半高，**算出来**而不是试出来 | PROMPT·weight/hardness | 分类/搬运 |
| P4 | descend-until-supported | 小步下降，盯着**物体自己**的高度；不再下降=已被支撑，绝不继续压 | API·open_gripper + REF§3.4 + SKILL | 全部 |
| P5 | unload-before-open | 松爪前先退 ~5 mm 卸掉手指的力；压着松会推倒，悬空 1 cm 松会摔歪 | API·open_gripper + REF§3.4 | 全部 |
| P6 | settle-after-release | 松手后继续小步直到物体静止才算放好 | REF§3.4 | 全部 |
| P7 | keep-closed-if-task-allows | 任务不要求松手就别松（weight_classify 实测松手 8/11 失败） | PROMPT·weight/hardness | 分类/搬运 |

### 4.4 协议类 R —— 顺序与设计原则（10 条）

| # | 技能 | 一句话 | 现有副本 | 谁需要 |
|---|---|---|---|---|
| R1 | differential-across-states | 要判一个性质，就设计"同一把里两个状态取差"——差把共同项消掉 | PROMPT·hardness/grasp + API 多段 | 全部 |
| R2 | one-grasp-multi-property | 一把抓取读多个性质，**顺序锁死**：先称重再夹紧 | API·close[hardness] + REF§2 | 分类 |
| R3 | weigh-before-squeeze | 夹紧那一步会毁掉重量读数；先称重 | REF§2 + API | 分类 |
| R4 | no-absolute-without-reference | 没有绝对参考就不做绝对判定，用差/比例 | PROMPT·memory | 分类/螺纹 |
| R5 | regrasp-after-slip | 滑了就重抓，别在原握里继续 | PROMPT·manipulation | 插入/搬运/螺纹 |
| R6 | cruise-height | 接近用巡航高度，别贴地飞 | PROMPT·manipulation | 插入/搬运 |
| R7 | readability-not-identity | "能读出差别"≠"能认出是谁"；判别力只保证前者 | PROMPT·memory | 分类 |
| R8 | tactile-cannot-locate | **东西去哪儿了要读图像或位姿**——触觉分不出"放好了"和"掉在半路" | SKILL | 全部 |
| R9 | grasp-verification | 抓取校验=接触标志 + 深度在量程内；空手已有接触读数时先别开爪 | SKILL + API | 全部 |
| R10 | channel-selection | 只有剪切与各向异性携带结构信息 | REF§3.9 | 全部 |

### 4.5 负面知识 —— 已证伪，要留"为什么不用"（11 条）

**这 11 条不在技能库里**（2026-10-05 决定），这里就是它们的存放处。理由：库里的每条都会被
投递进某个任务的 prompt，而"别这么做"永远不会被投递——它没有路由目标，只会让路由代码为
永不出门的内容养一条特殊分支。真正需要它们的读者是**写下一个判据的人**，那正是本文档。
其中与活技能同源的几条（深度≠硬度、伺服反号、位移≠重量、相干度判纹理），已经以"陷阱"
的形式写在对应活技能里。

| # | 曾经的做法 | 为什么不行 |
|---|---|---|
| N1 | coherence 判纹理 | n=10 seeds 零判别力（oracle 6/10 < 常数基线 7/10） |
| N2 | `rotate_gripper` 滑移判摩擦 | 两个棱柱摩擦系数相同，分不开 |
| N3 | 大滑移 = 卡住 | 唇口摩擦会被误读；要分级（≤0.5 噪声 / 0.5–3 摩擦 / 4–8 过唇 / ≥3 持续=真卡） |
| N4 | `marker_displacement_px` 判重量 | 与重量重叠（n=12） |
| N5 | 单垫 \|Δ剪切\| 判重量 | 混合深度下重叠 |
| N6 | 深度当硬度 | 两类落在同一深度，信号只有 0.18× |
| N7 | 2 mm 抬升 / 深度读数判插入到位 | 到位与卡住两种情况读数都不变 |
| N8 | `detect_slip` 判落座 | 慢压是压缩不是剪切，增量上不去 |
| N9 | "先 5 cm 再目标"式两段接近 | 差 ~1 mm |
| N10 | 世界系旋转修姿态 | 0.04 rad 把末端甩出 ~2 cm |
| N11 | 伺服开着测硬度 | 符号反过来，两类重叠 |

---

## 5. 技能 × 任务类型矩阵

图例：● 该类型必用 · ○ 相关但非关键 · 空白=无关。
分类=weight/hardness/grasp_classify/memory_match，插入=hdmi/hole/tube，搬运=lift_*/put_*/pull_out_key/tactile_transfer，螺纹=bulb_tightening。

| 技能 | 分类 | 插入 | 搬运 | 螺纹 |
|---|:-:|:-:|:-:|:-:|
| S1 depth-grip-scale | ● | ● | ● | ● |
| S2 gel-shear-weight | ● | | ○ | ○ |
| ~~S3 weight-baseline-unloaded~~ ⇒ S2 | ● | | | |
| S4 grip-force-by-mass | ● | | ○ | |
| S5 lift-follow-height | ● | | | |
| ~~S6 depth-is-not-weight~~ ⇒ S1 | ● | | | |
| S7 hardness-two-closes | ● | | | |
| S8 position-mode-for-yield | ● | | | |
| S9 finger-position-grip | ● | | | |
| ~~S10 texture-only-in-anisotropy~~ ⇒ R10 | ● | | | |
| S11 texture-slope-two-grips | ● | | | |
| S12 can-texture-difference | ● | | | |
| S13 shear-vs-displacement | ● | ● | ○ | ● |
| ~~S14 two-sided-hold~~ ⇒ R9 | ● | ● | ● | ● |
| S15 slip-pair-baseline | ○ | ● | ● | ○ |
| S16 contact-flag-not-proximity | ● | ● | ● | ● |
| ~~S17 reference-depth-offset~~ ⇒ S1 | ● | ● | ● | ● |
| ~~S18 far-plane-check~~ ⇒ R1 | ● | ○ | ● | ● |
| S19 confidence-gate | ○ | | ○ | ● |
| S20 follow-is-signal | ● | | | |
| S21 close-lands-on-target | ● | ○ | ○ | |
| S22 no-calibration-transfer | ● | | | |
| A1 reflex-stop-on-events | ○ | ● | ● | ● |
| A2 descent-until-contact | ○ | ● | ● | ● |
| A3 one-call-approach | ● | ● | ● | ● |
| A4 lateral-only-align | | ● | | |
| A5 small-step-insertion (+A8) | | ● | ● | |
| A6 asymmetry-means-misaligned | | ● | ○ | |
| A7 stall-means-jam | | ● | ○ | ○ |
| ~~A8 depth-stop-seating~~ ⇒ A5 | | ● | ● | |
| A9 preserve-existing-grasp | | ○ | ● | |
| A10 thread-direction-by-legs | | | | ● |
| A11 lead-by-overshoot | | | | ● |
| A12 feed-must-match-lead | | | | ● |
| A13 seat-by-slide-collapse | | | | ● |
| A14 mirrored-shear-slide | | | | ● |
| A15 lattice-pitch-budget | | | | ● |
| A16 wrist-limit-segmentation | | | | ● |
| A17 orientation-fix-high | | ○ | ● | |
| P1 world-frame-transport | ● | ● | ● | ○ |
| P2 lift-before-lateral | ● | ● | ● | ○ |
| P3 seat-height-from-plate | ● | ○ | ● | |
| P4 descend-until-supported | ● | ● | ● | |
| P5 unload-before-open | ● | ● | ● | |
| P6 settle-after-release | ● | ● | ● | |
| P7 keep-closed-if-task-allows | ● | | ○ | |
| R1 differential-across-states | ● | ○ | ○ | ○ |
| R2 one-grasp-multi-property | ● | | | |
| ~~R3 weigh-before-squeeze~~ ⇒ R2 | ● | | | |
| R4 no-absolute-without-reference | ● | | | ○ |
| R5 regrasp-after-slip | ○ | ● | ● | ● |
| R6 cruise-height | | ○ | ● | |
| R7 readability-not-identity | ● | | | |
| R8 tactile-cannot-locate | ● | ● | ● | ● |
| R9 grasp-verification | ● | ● | ● | ● |
| R10 channel-selection | ● | ○ | ○ | ● |

**从矩阵读出来的三件事**：

1. **12 条是所有类型共用的**（S1/S13/S14/S16/S17/A3/R8/R9 等）——这些是库的"通用层"，
   每个任务的 prompt 都在重复讲，最该收进库。
2. **分类类占了 S 表的大半**（S2–S12 全是分类专属）。分类任务的触觉知识最厚，也最该被
   复用：memory_match 就是"一个任务里跑完 S2+S7+S11 三条"。
3. **螺纹类是孤岛**：A10–A16 七条只有 bulb 用，且全部集中在 `rotate_gripper[screw]` 一个段落里
   ——已经是最接近"一个技能一个文件"的形态，最适合当库的样板。

---

## 6. 建议的库形态

### 6.1 目录

```
skills/tactile/
  sense/        S1..S22    读什么、怎么读、读不出来是什么样
  act/          A1..A17    什么信号触发什么动作
  place/        P1..P7     放下与确认
  protocol/     R1..R10    顺序与设计原则
```

一个技能一个文件，文件名就是 slug。原本还设想了一类 `falsified/` 装已证伪的 11 条，
实施时去掉了——它们永远不会被投递，留在库里只会让路由代码为"永不出门的内容"养一条特殊分支。
它们现在只住在 §4.5。

### 6.2 每个文件的骨架

```yaml
---
name: gel-shear-weight
summary: 用两垫剪切差读重量，而不是读抬升高度
task_types: [classify]
channels: [shear]          # shear | anisotropy | depth | force | vision
objects: any-hangable      # 换物体要重新标定的，在这里标出来
evidence: ViTaForge n=12，180 g vs 1.8 g，19× 分离，零重叠
---

## 做法
（步骤 + 数字 + 阈值）

## 陷阱        ← 原来散在 prompt 里那句"注意"
## 谁在用       ← 当前哪些任务的 PROMPT / docstring 里有它的副本
## 换物体要不要重标   ← 阈值里哪些是普适的、哪些是物体相关的
```

`objects` 与"要不要重标"这两栏是这次盘点里最值钱的东西：现在 S7 的 0.10/0.06、S11 的
27.5/20.5、A12 的握力 27.2 都是**物体相关数字**，混在普适规则里，读的人分不出来。

### 6.3 库怎么进 prompt（三条路，选了第一条）

| 方案 | 做法 | 代价 |
|---|---|---|
| **① 库当唯一真源**（已选） | 库文件是手写的；路由按任务类型，任务可覆盖 | 小。分发机制完全不动；不在 taxonomy 里的任务一字不变 |
| ② 按技能选择，替代标签 | YAML 里写 `api_skills: [gel-shear-weight, hardness-two-closes]`，比 7 个标签细 | 中。当时算的是"要改 `_apply_doc_sections` 与 13 份 YAML"——标签机制 2026-10-06 已删，这条路现在是"要改 13 份 YAML 并把路由从类型级改成技能级" |
| ③ 只做人读文档 | 库不进 prompt | 零。但 prompt 里的重复与分叉照旧 |

实施时①的具体形态和当初设想略有出入，记在 §6.5：入口开在**任务类属性**上（不是 YAML，
也不是构建期生成 `[section:tag]`），因为 YAML 的 `prompt:` 会整段覆盖类 PROMPT
（见 memory `capx-yaml-prompt-overrides-class-prompt`），走 YAML 反而更容易踩到"改了没生效"。

### 6.4 首批建议收的 12 条（实施时没照这个分批，56 条一次收完）

按"被复制次数 × 通用度"排：

`S1` `S13` `S14` `S16` `S17` `A1` `A2` `A3` `P1` `P4` `R8` `R9`

理由：这 12 条在矩阵里横跨全部四类，现在每类的 prompt 各讲一遍（措辞还不一样），收进库
收益最大、风险最小——它们都不含物体相关的数字。

第二批收分类专属的 `S2`–`S11` 与螺纹专属的 `A10`–`A16`，那两批收完，prompt 里可以删掉
~700 词重复 + 每个分类任务 ~250 词的段落。

### 6.5 已落地的东西（2026-10-05 落地，2026-10-06 摘要压成一句话规则）

代码在 `capx/tactile_skills/`，与 `capx/skills/` **不是一回事**：后者从成功的 trial 代码里
抽 Python 函数，前者装的是文字。

```
capx/tactile_skills/
  taxonomy.yaml            任务类型 → 任务名（任务名取自各 low_level_*.yaml 的 task_name）
  library.py               加载 / 校验 / 路由 / 两种渲染
  skills/
    sense/     16 条      act/       19 条
    place/      7 条      protocol/  10 条
    ── 合计 52，= §4 里全部活技能 + §6.6 新增的 4 条 − §6.7 合并掉的 8 条
```

**路由键**有两个，可叠加：`task_types:`（classify / insert / manipulate / screw，四个类型覆盖
全部 13 个任务）和 `tasks:`（点名单个任务）。`tasks:` 是 2026-10-06 才用起来的：**按物体标定
的数字**（方块 / 罐 / 灯泡三套互不通用的阈值）必须靠它分开——`hardness_classify`（方块）和
`tactile_memory_match`（罐）同属 classify，只按类型路由会让两边互相拿到对方的数字。这类技能
写成 `task_types: []` + `tasks: [<任务>]`，即"只给这一个任务"。

**两种渲染**：`compact`（一行一条，给 prompt）与全文（给来查这一条的读者）。全文是 compact
的 5 倍以上，所以 prompt 侧默认 compact。compact 的一行是 `- <名字>: <摘要>`——**不带内部
编号**（`[S1]` 之类只在本库内有意义，prompt 查不到它）。

**摘要 = 一句话规则**（2026-10-06 定稿）。写作口径：一条规则 + 它自己的判据数字，**不写**
产生这条规则的推理；理由、阈值来源、失效条件全留在正文。全库 52 条摘要合计 857 词、平均
16.5 词，最长 23 词。这是投递量的唯一杠杆——正文不进 prompt，所以正文长短不影响预算。

**每个任务会拿到多少**（compact，2026-10-06 合并后实测，52 条；括号里是压成一句话规则
**之前**的数）：

| 类型 | 条数 | 词数 | 每行均 | 代表任务 |
|---|---|---|---|---|
| classify | 29–30 | 537–557（1225） | 18.5 | weight_classify |
| screw | 29 | 539（902） | 18.6 | bulb_tightening |
| insert | 25 | 464（818） | 18.6 | insert_hole |
| manipulate | 24 | 436（736） | 18.2 | lift_bottle |

压掉约 44%。⚠ **但每个类型仍然拿到全库的 46–58%** —— 路由把"哪些技能和这个任务相关"答成
了"大部分都相关"。这符合盘点的判断（§5 矩阵里过半技能是跨类型的），但作为 prompt 预算它
意味着：开启一个任务等于给它 440–560 词的清单。数字已由测试
`test_compact_block_is_one_line_per_skill_and_within_budget` 钉住（每行 <25 词、每类 <800
词），跟着长回去就红。

**怎么开**：**2026-10-06 起默认开**——任务名在本库 `taxonomy.yaml` 里就按类型投递，不在表里
的任务（非 UniVTAC）拿到 `""`，所以 13 个任务类上不用各写一行。覆盖方式有两种：
`tactile_skills = ()` 关掉、`tactile_skills = ("skill-a",)` 点名；再加
`tactile_skills_verbose = True` 换成全文。测试 `test_no_task_class_overrides_the_hook`
钉住"没有任何任务类改写默认路由"，`test_an_empty_tuple_turns_the_block_off` 钉住关闭键。

**校验**（37 条测试）：任务名与 `env_configs/univtac/*/low_level_*.yaml` 对不上就报错、
未知类型/未知任务/文件名与 slug 不符/重名/未知字段/`[[交叉引用]]` 悬空/有条目够不到任何
任务/字数超预算——全部在加载期抛错，不静默。另有一条钉住全仓不再出现 `[section:` 残留。

### 6.6 2026-10-06：把「怎么用」从 docstring 搬进库

前面几节记的是库怎么建起来的；这一节记的是**用法从哪来**。

**搬之前**：`univtac_api.py` 的 docstring 同时装两样东西——函数是什么，和函数该怎么用。
后者用 7 个 `[section:tag]` 包着（合计 4,060 词），靠 13 个任务类各自的
`api_doc_sections` 筛选投递，所以同一个任务只看到自己声明的那几段。两边还已经漂移：
同一条规则在 docstring 和技能库里阈值不同，或者数字在一处、caveat 在另一处。

**搬之后**，判定规则收敛成一句：一句话写进 docstring，当且仅当它回答
"这个函数 / 参数 / 返回字段**是什么**"。

| 留在 docstring | 进技能库 |
|---|---|
| 参数取值范围与含义 | 该取哪个值 |
| 返回字典里每个字段的定义与量纲 | 拿这个字段怎么下判断 |
| 硬性限制（每轴 ≤0.10 m、≥2 mm、被拒会报错） | 因此该怎么绕 |
| 服务端行为（首调 `detect_slip` 只建基线） | 因此该怎么用 |
| 读数的标尺（34 mm 远平面、20 mm 硬钳位） | 读数怎么组合成判据 |

**删除的机制**：`DOC_SECTION_TAGS`、`ApiBase._apply_doc_sections` / `doc_sections`、
13 个任务类的 `api_doc_sections`、`base.py` 里的接线，以及
`tests/integrations/test_api_doc_sections.py`（144 条）。现在**一份 API 文档发给 13 个任务**，
正文 6,054 词 → 1,407 词。

**新增 4 条技能**（都带 `tasks:`）：

| 文件 | 装什么 | 为什么不能按类型路由 |
|---|---|---|
| `act/bulb-close-force` | 灯泡闭合力 27.2 | 该任务没设默认力，不给就一路夹到机械限位 |
| `act/bulb-grasp-pose` | 灯泡抓取位姿每次重建 | 走真值分支，松手重抓后要重新问一次 |
| `act/grasp-pose-from-offset` | 空手时用首把量到的 offset 自己算 | `sample_grasp_pose` 的缓存只对首调时 EE 正上方那个物体有效 |
| `protocol/memory-match-one-grasp` | 罐的五步整把协议 + 两套数字 | 罐的 0.50/0.25、10 mm 分界与方块的 0.10/0.06、1.2 mm 互不通用 |

**没搬的三样**（有意）：

1. `get_tactile_image` 的取材建议——该函数不在 `functions()` 里，LLM 根本看不到，
   搬进库只会给 13 个任务各加一条用不上的规则。
2. `force_threshold=24.0` 那个单次读数纹理判据——旧文自己就标注了"只在抓取落点和标定时
   一致才成立"，是**被否掉的替代方案**，与 §4.5 的负面知识同性质，但不值得占一行预算。
3. 按段落投递这个机制本身：库的路由粒度是任务类型，不是"在哪个调用点看到"。

**验收网**（一次性脚本，没留仓库）：把旧 docstring 的每句话拿去在「新 docstring ∪ 全库
（含 evidence）」里做字面匹配，178 句没有字面落点。逐条人工过完，结论是两类：句子被改写了
（同一规则的另一种措辞），或者是有意丢掉的**替代形式**。真正丢掉的只有一条——
`goto_pose` 的 `z_approach=0.1`（抓取/放置用 0.1、纯移动用 0.0），已补进
`one-call-approach` 的**摘要行**。

⚠ 那条补丁第一版补错了地方：写进了 `## How` 正文。**正文不进 prompt**，摘要才是投递的
那一行——补完要拿 `library.render_for_task(task)` 复核，不能只看文件里有了就算数。

**同一次改动里被数字网捞出来的**：`21.27-21.34 / 26.62-26.66 / 12.66-12.73 / 7.34-7.38`
在库里是四舍五入后的 `21.3 / 26.6 / 12.7 / 7.4`（`memory-match-one-grasp` 的 evidence），
不是丢了；`13 mm`（灯泡自由行程）在库里被有意泛化成"most of the free travel（约 8 mm）+
行程是任务几何不是常数"。

### 6.7 2026-10-06：合并同一条规则的两个家（60 → 52）

上一节搬完"怎么用"之后，库里出现了同一批规则被写成两条技能的情况。清了一遍，**8 组**合并：

| 删掉 | 并进 | 判据 |
|---|---|---|
| `depth-is-not-weight` | `depth-grip-scale` | 后者的陷阱第 3 条已逐字写着"深度只说夹得多紧，从不说多重" |
| `reference-depth-offset` | `depth-grip-scale` | 后者的 `## How` 已写"取 min(L,R)，两垫恒定差 0.3 mm" |
| `weight-baseline-unloaded` | `gel-shear-weight` | 前者整条就是后者的第 1 步，连"0.4 px""开爪重到位""减掉更大基线不管用"三句都一样 |
| `two-sided-hold` | `grasp-verification` | 同一套协议；后者正文里直接写着"（见 two-sided-hold）"——自己指向自己 |
| `depth-stop-seating` | `small-step-insertion` | 两段 `move_reflexive(stop_on={"plateau":…})` 一字不差 |
| `texture-only-in-anisotropy` | `channel-selection` | 后者表格里已有"表面怎么塑造凝胶 → 两握位各向异性"这一行 |
| `weigh-before-squeeze` | `one-grasp-multi-property` | 后者的摘要原文就是"先称后夹"，How 是同一套三步顺序 |
| `far-plane-check` | `differential-across-states` | "松握必须读 34.0 mm"是两态取差的**前提**，且 `follow-is-signal` / `no-calibration-transfer` 里已各写了一遍 |

⚠ **合并省的是摘要行，不是正文字数**。compact 投递只带 `- <名字>: <摘要>` 一行，正文永远
不进 prompt ⇒ 正文里各抄一遍的"陷阱"在 prompt 里本来就不花钱，它们花的是**维护成本**
（改一处忘另一处，正是 §6.6 那批漂移的成因）。所以预期收益要按"行"算，不能按"文件"算。

实测（52 条）：classify 610–647 → **537–557**、screw 557 → **539**、insert 497 → **471**、
manipulate 451 → **443**。manipulate 只掉 8 词是刻意的：`depth-stop-seating` 原本覆盖
插入+搬运 8 个任务，而 `small-step-insertion` 只覆盖插入 3 个 ⇒ 合并时把它**扩到
`[insert, manipulate]`**，宁可多发一条也不让 5 个搬运任务丢掉这条规则。

**同时消掉的正文重复**（5 处，省 0 词，只降漂移风险）：导程别加相干门（`lead-by-overshoot` /
`seat-by-slide-collapse`）、0.04 rad ≈ 2 cm（`world-frame-transport` / `orientation-fix-high`）、
2 mm 下限（`descent-until-contact` / `small-step-insertion`）、低跟随不算失败
（`memory-match-one-grasp` / `follow-is-signal`）、方向与导程都是**每轮实测**
（`thread-direction-by-legs` / `lead-by-overshoot`）。做法统一：**留一个正主，其余改成
`[[正主]]` 指针**。

**看着像重复、但故意不合并的三处**：

1. `bulb-grasp-pose` / `grasp-pose-from-offset`——同一个函数，但灯泡走真值分支、其余走缓存。
   合了等于把灯泡特例发给 13 个任务。
2. `descend-until-contact` / `descend-until-supported`——停的信号不同：一个是垫子离开远平面，
   一个是被夹物体**自己**的高度不再下降。测量对象不同，不是措辞不同。
3. `keep-closed-if-task-allows` / `preserve-existing-grasp`——"没理由别松手" vs
   "没理由别重抓"，且前者还带着一条独立发现（weight_classify 11 轮里 8 轮死在松手）。

**验收网**（一次性脚本，没留仓库）：把 8 个被删文件的每句话拿去在「合并后的全库
（摘要+evidence+正文）」里做 5-gram 最佳匹配，21 句落在 0.55 以下 → 逐条人工过完，
**全部是改写、没有丢失**。两处**有意降级**：`0.3 mm`（两垫间距，判"握歪没歪"时要）与
`0.4 px`（基线的有效性检查）从摘要行移进正文，因为合并后的摘要行装不下两句话。

**顺带发现的一个路由缺口（当天已补）**：`confidence-gate`（0.70 门槛）原本是
`task_types: [screw]`，但它的规则是"任何标记读数都要过门"，插入和搬运也读标记剪切。
已改成四类全发。**代价是它让每类的投递量都长了十几个词**——这是刻意的，不是漂移
（见 MAINTENANCE §5 的组间说明）。

### 6.8 2026-10-06：任务 PROMPT 瘦身，副本清到零

§6.6 搬完 docstring，这个库的**来源**——13 个任务的 PROMPT——还没动。`copies:` 字段
当时就是欠账本：**52 条里 28 条记着"这句也写在某个任务的 PROMPT 里"**。这一节把它还完。

**四件事，按做的顺序：**

**① 先修技能，再删 prompt。** `seat-height-from-plate` 原本只写"加半个高度"——那对
原点在物体中心的物体是对的，对这批场景里的方块/罐/垫（原点在底）是错的，差的是半个物体，
表现出来是压下去或摔下来，而不是一个看着不对的数字。prompt 里的旧版删掉之前，
技能里必须先是正确的那一版。**这是这次唯一的"删之前要先改"**：删副本的前提是正主是对的。

**② 删 prompt 里的副本，每个留一句指路句。** 13 个 prompt 合计 **2870 → 2049 词（−29%）**
（前 = 上一次提交；词数按 `len(PROMPT.split())`）：

| 任务 | 前 → 后 | 删掉的是什么 |
|---|---|---|
| `hardness_classify` | 546 → 263 | "怎么夹出硬度"整表 + "怎么搬到盘子上"整表 |
| `tactile_memory_match` | 500 → 352 | 巡航高度、有效性/对称性、可读性三条 |
| `weight_classify` | 497 → 248 | "怎么称重"整表 + 搬运整表 |
| `bulb_tightening` | 401 → 333 | 六条机制长句压成四个问题各一行 |
| `insert_hdmi` | 217 → 167 | 插入配方 |
| `grasp_classify` | 201 → 171 | 判据那一段 |
| `tactile_transfer` | 151 → 154 | 放置配方 → 指路句（净增 3 词）|
| `insert_tube` | 68 → 72 | 同上（净增 4 词）|
| `insert_hole` | 74 → 74 | 同上（删 17 词、加 17 词，正好抵消）|

**5 个 prompt 一个字没动**（`lift_bottle` 50 / `lift_can` 65 / `pull_out_key` 48 /
`put_bottle_in_shelf` 52）：它们本来就只是目标陈述，没有可删的副本。
`insert_*` 和 `tactile_transfer` 是净增的——那句指路句比原来那两行"Key rules"更长，
**这个方向是对的：删掉的是配方，留下的是指向配方的一句话，不是把所有字都删掉。**

留下的指路句统一形状：**"怎么做在末尾的技能里"**，不重复技能内容。
理由：万一哪天那个任务把技能块关掉（`tactile_skills = ()`），句子还在，不至于删到只剩目标。

**③ 顺手堵掉一个真的泄口：消融配置拿到了凝胶配方。**
27 个 `*_no_tactile*.yaml` 把 `UniVTACTactileApi` 换成 `UniVTACMotionApi`，
**但保留 `task_name`**，而路由只看任务名 ⇒ 一个没有任何触觉读取通道的机器人，
收到了一整页"怎么读凝胶"。修法是在投递钩子里加一道 `_reads_the_gel()`：
看这个任务拿到的 API 里有没有 `get_tactile_data`，没有就不发。显式点名
（`tactile_skills = ("...",)`）跳过这道检查——那是人的决定，照发。

> **已知的观感问题（刻意留着）**：5 个带指路句的任务在消融配置下，句子还在、块没了，
> 读起来像"说好的技能在哪儿"。修它要给 15 个配置各写一份 prompt 覆盖，正是这个库在反对的事。
> 代价是一句话悬空，收益是那句凝胶配方不会被发给读不到凝胶的机器人——**接受**。

**④ 清 `copies:` 里的 PROMPT 行（28 条）和后来的 API 行（21 条）。**
今天这个字段从"52 条里 43 条有"变成"27 条有"，剩下的一律指向
`docs/univtac_api_reference.md`（25 处）和 ViTaForge 侧 `univtac_skill/SKILL.md`——
两份都是给人看的文档，不进任何 prompt。

**验收**：`tests/integrations/test_tactile_skills.py` 全绿（当时 39 条，新增 2 条钉住凝胶门）；
`render_for_task` 复核 13 个任务的块仍在、条数与类型对得上；`grep PROMPT capx/tactile_skills/skills/` 无结果。

**未验证**：13 个 prompt 全变了，**分数一次没跑过**（要服务端、要挑任务）。按 §4.4 的规矩，
这次改动在跑过 eval 之前都算未验证。

---

### 6.9 2026-10-06：技能块一条也没发出去（路由 bug）

**这是本轮最严重的发现，也是"用户去看真实 prompt，发现和我们说的不一样"的根因。**

#### 症状

用户在 `outputs/kimi-k3/univtac_bulb_tightening/trial_01_taskcompleted_0/` 里翻到
`initial_prompt.txt`，2045 词 = 任务 prompt 333 + API docstring 1711，**没有技能块**。
按 §6.5–6.8 的描述，这个任务应当拿到 29 条技能。同一目录下的
`outputs/kimi-k3/univtac_bulb_tightening/initial_prompt.txt` 同样 0 命中。

#### 根因：`cfg.low_level` 是路径字符串，代码当对象读

投递链是 `_tactile_skill_block()` → `_task_name()` → `library.task_type_of(name)`。
`_task_name()` 读的是：

```python
low = getattr(self.cfg, "low_level", None)
name = getattr(low, "task_name", None)      # ← 只有这一条路
```

而仓库里**每一个**配置交给它的是低层 YAML 的**路径字符串**
（`env_configs/univtac/bulb_tightening/low_level_bulb_tightening.yaml`），
`_build_low_level` 是在进环境时才去读这个文件的，路径字符串原样留在 cfg 里。
于是：

```
cfg.low_level = 'env_configs/univtac/bulb_tightening/low_level_bulb_tightening.yaml'  (str)
旧实现 _task_name() -> ''
task_type_of('') -> None   ⇒  _tactile_skill_block() 返回 ""
```

**34 个配置，无一例外。从 2026-10-05 落地到 2026-10-06 修复之间，
技能块一次都没进过任何 trial。**

#### 为什么 39 条测试全绿

测试用的桩是 `SimpleNamespace(low_level=SimpleNamespace(task_name=task))`——
**生产里根本不长这个形状**。测试测的是"我设想的配置长什么样"，不是配置实际长什么样。
这和 §6.8 里 `_reads_the_gel()` 修的那个泄口是同一类错误的两面：
一个漏发了不该发的，一个压根没发。

#### 修法

1. `base.py::_task_name()` 按三条路依次取，先真后假：
   **已建好的低层环境**（`self.low_level_env.task_name`，服务端认的那个名字）→
   **解析过的 `cfg.low_level`**（对象或 dict）→ **现读 YAML**（`.yaml`/`.yml` 结尾才试，
   读不动就当"不是个有名字的任务"，不抛）。
2. `low_level.py::UniVTACStubEnv.task_name` 提为公开属性并注明它就是路由键。
3. 新增 `test_a_shipped_config_resolves_its_task_name`：**拿 34 个真配置各走一遍**，
   断言 `_task_name()` 等于 `low_level_*.yaml` 里的 `task_name`、且在 taxonomy 里。
   这是本该有的那张网——它现在会在任何人再动 `_task_name()` 时当场红。

#### 验收

```
旧实现 _task_name() -> ''
新实现 _task_name() -> 'bulb_tightening'
prompt words: 2601   has block: True
```

末尾三行是
`depth-grip-scale` / `shear-vs-displacement` / `slip-pair-baseline`。
测试 40 条全绿。另外复核了三种配置形状：本任务带触觉 → 发；
`_no_tactile`（换成纯运动 API）→ 名字仍解析得到、但凝胶门挡下，不发；
`_multiturn` → 发。

#### 要改口的旧结论

- **§6.5–6.8 里所有"投递量"数字量的都是拼装结果，不是真实投递。**
  骨架（几条、多少词、投给谁）没错，但"13 个任务都拿到技能块"这句话
  **在 2026-10-06 修复之前是假的**。
- **已有的 eval 输出全部是修复前的**，包括 `outputs/kimi-k3/univtac_bulb_tightening/trial_01*`。
  拿它们评价技能库等于评价一个空块。
- **要拿到真正的证据，必须重跑。** 分数仍然一次没跑过。

---

### 6.10 2026-10-06：修复后的第一轮真实 eval —— 技能块送到了，四处摘要不够用

修复后当天 17:46–18:19 跑了一轮 `kimi-k3 / univtac_bulb_tightening / trial_01`，
seed 1，1462 秒，**Reward 0.0**，一个代码块、无重试，异常终止。

#### 好消息：投递链通了，而且技能被读进去了

`trial_01_taskcompleted_0/prompts_and_responses/initial_prompt.txt` 是 2601 词，
末尾 29 条技能块完整（§6.9 的 bug 已不再复现）。代码里能看到技能在起作用：
`close_gripper(force_threshold=27.2)`、`sample_grasp_pose`、`z_approach=0.1`、
先调一次 `detect_slip` 建基线、相干度 0.7 门槛、两条 30° 腿探方向、段长收到 100°。
**这是这个库第一次真正影响一次运行。**

#### 死因（服务端逐 step 物理轨迹为证）

`/tmp/univtac_server/bulb_tightening/work/1/physical_trace.jsonl`，192 行：

| 量 | 结果 |
|---|---|
| `signed_world_rotation_deg` | 0 → **+225.97°** 单调爬升，正好停在那里被拒 |
| `advance_m` | **−0.0025 m**（灯泡是往上退出来的） |
| `torque_Nm` / `seat_force_N` | 全程 **0 / 0.015 N**，从未坐底 |
| `peak_pad_normal_force_N` | 爬到 **121 N** |
| `probe_complete` | 全程 false |
| `phase` | 全程 `setup` |

服务端报 `IK servo refused at 45.5 deg (task failure: screw_IK_limit)`，
`_TASK.failure` 只在 reset 清 ⇒ 一次拒绝闩死整轮（[[univtac-bulb-ik-failure-is-sticky]] 的机制原样复现）。

#### 四处摘要不够用（都已改）

判据都是同一条：**决定性的那句在正文里，而正文不进 prompt。**

1. **读错了剪切轴。** 模型写的是
   `left_marker_shear_dy − right_marker_shear_dy`，而 oracle 与环境自己的专家读的都是
   **`dx`**（增益 17.6–20.8 px/mm 也是量在 `dx` 上的）。摘要当时只说"左减右、不要平均"，
   没说**哪个分量**，而 `dy` 是个真实的、看着完全合理的数 ⇒ 它的导程搜索等于在盲搜。
   → `mirrored-shear-slide` 摘要补上 x 通道与"保留符号"，正文加一条陷阱。
2. **方向腿没有验收线。** 摘要说"用两条 30° 腿，靠得近就升 45°"，
   而**正文**那句"分开不到 0.15 mm 就再转、不许猜"才是决定性的。模型的代码里
   两条相干度都低于门槛时直接 `SIGN = +1.0` —— 掷硬币。
   它转反了：手是**往下**走的（喂 0.8–3.0 mm/转），灯泡却**往上**退了 2.5 mm
   —— 只有螺纹往外顶才可能。这是 v8/v9（+235.3° ⇒ 推进 −2.82 mm）的同一个指纹。
   → 摘要补 `under 0.15 mm apart, turn further, never guess`，正文加一整段"没有默认值"。
3. **手腕额度写成了"总行程"。** 正文原话是"限制是一把之内的总行程"，
   **本轮轨迹直接否掉**：探针那 4 条腿走了 120° 行程、每次都回原位，**代价为零**；
   真正花掉的是**没卷回来的净转角**。模型把复位只放在坐底循环末尾，
   前面找导程的 6 段 30°（180°）一路单向拧下去，等真正开始坐底，
   第一段走到 45.5° 就撞墙。
   → 摘要改成"花的是没卷回来的转角，探针也算账"，正文按实测重写。
   ⚠ 顺带记录一条方向不对称：拧紧方向 135° 被拒过两次（oracle 注释里的一手记录），
   而本轮**反向**走到 226° 才被拒 ⇒ **226° 不是拧紧方向的额度**，别拿它外推。
4. **导程搜索认不出"拒绝"。** `lead-by-overshoot` 正文说"超过导程滑移转负"，
   摘要只说"直到螺纹跟不上"——模型自己发明了一个"滑移突增 1.6 倍"的判据，
   而实测读数 2.47 → 2.72 → 3.03 → 3.24 → 3.27 → 3.63 单调，比值 ~1.1，
   于是六段全跑完、一次没退出，最后停在最大的 3.0 mm/转上。
   → 摘要改成"走到滑移转负为止——那就是螺纹在拒绝"，正文加一条陷阱。

#### 这一轮**没有**证明的东西

- **分数**：0 分是这一轮的结果，不是技能库的质量评价——它死在方向判错上，
  后面 12 条判据一次都没跑到。
- **226° 与"总行程"的取舍**只在一轮、一个 seed 上量到，机制推断（是关节行程用尽、
  还是构型翻转）**没有分开验证**；摘要里现在只写"没卷回来的转角"，不写具体度数上限。
- **`dx` 优于 `dy`** 是从 oracle 与环境自己的读取器推出来的，本轮没有对照实验。

#### 验收

`tests/integrations/test_tactile_skills.py` 40 条全绿；13 个任务的块仍在
（classify 577–597 / insert 495 / manipulate 474 / screw **575**，screw 比上一组 +19 词）。
基线表已同步（MAINTENANCE §5 第五组）。

---

## 7. 明确**不**搬进库的东西

| 不搬 | 为什么 |
|---|---|
| 任务目标与评分判据 | 属于任务，不属于技能；库只回答"怎么读/怎么做" |
| 场景里的物体名、位姿、台面高度 | 属于任务；库里的例子要写成"一个可悬挂物体" |
| oracle 内部的判定代码 | 是判据实现，不是可复用技能；且特权/非特权分叉要保持 |
| 特权的量（真值位姿、`check_success` 细节） | 库对所有任务可见，不能夹带特权信息 |
| 物体相关的标定数字本身 | 要搬，但必须挂在 `objects:` 栏下并标明"换物体要重测"，不能混进普适规则 |

---

## 8. 这份盘点还没覆盖的

- **agent 线 vs eval 线的分叉**：`SKILL.md` 与 docstring 教同一批技能但用不同措辞，
  本盘点只做了合并，没有决定"合成一份还是保持两份"——那是个独立决定。
- **`univtac_skill/tools/` 里的原语**：那里有代码级的技能实现，本盘点只看了文字。
- **矩阵是我的第一遍判断**，不是实测出来的；用之前值得对每一类过一遍。
