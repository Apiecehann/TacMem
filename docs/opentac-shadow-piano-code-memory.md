# 钢琴可执行代码记忆实验

## 2026-10-29：完整程序与修复技能独立维护

当前 `play_piano_shadow_digit_evidence_memory.yaml` 启用 `shadow_piano_independent_memory: true`。
仍通过 t-cap 的 `./run_responses` 启动。按后续授权，在必要 CPU 检查后运行单条 GPU smoke；结果见本节末尾。下文旧 A/B/C、函数拼接、bundle 晋升规则为历史记录，不用于此配置。

### 两个独立存储

数据根由 `CAPX_SHADOW_EVIDENCE_ROOT` 指定，位于 `/mnt/data/ljz/opentacbench`。

| 路径 | 内容 |
|---|---|
| `programs/versions/<version>/` | 完整 `policy.py`、独立 `parameters.json` 与校验值、接口/传感器/力模型/任务要求兼容条件、函数 AST 版本、差异与公开验证记录 |
| `programs/active/` | 每种兼容契约的合格 active 指针；全局 `active.json` 和历史日志便于追溯 |
| `programs/rejected/` | 未通过静态检查的完整提案，不可执行 |
| `repair_skills/skill_<identity>/` | 不可变修订 JSON 与原子 head 指针；不依赖来源程序文件可读 |
| `episodes/` | 连续公开实验日志、RGB/depth/有效掩码附件与调用记录；不逐帧建立检索条目 |

完整程序只按兼容契约及输入类型选择。**同一次运行、同一源码及执行参数版本至少有 5 个原生成功音，且完整公开执行和 END 检查通过，才可直接复用。** 旧公开验证标记、pending、跨 seed 累计和跨版本完成的音均不能绕过该门槛。5/6 正常收尾允许晋升；不要求整条任务成功。原生结果只在宿主私有 `shadow_program_qualification.jsonl` 中记录，技能和 LLM 输入不含判分。没有合格代码时重新生成完整程序，不拼接历史函数。

### 技能记录和状态

每条记录包含 `skill_id/version/status/applicable_conditions/failure_context/numeric_correction/repair_advice/public_validation/provenance`，以及错误 `keys`、实际修改摘要与可选代码差异。来源程序不参与加载依赖。Skill Library 与修复经验共用一套存储，`verified_view()` 仅是可选的已验证展示，不是检索入口。

2026-10-29 起：**接受并产生实际代码修改就保存为可检索经验，验证状态不作为入库或检索门槛。** `has_code_change` 由宿主源码版本差异给出，`available_at_ns` 记录当前修订的保存时间；拒绝的代码、没有修改的请求保留日志但不冒充修改经验。旧记录保留，不自动导入新冷启动。

`numeric_correction` 保存 `parameter/before/after` 或 `rule`、`unit/limitations`。未知值为 null；数值是报告设置或拟议修正，不宣称逐项实测。不再要求 AST、变量、默认参数或采样字段来源匹配。任务目标、保持／释放要求、硬边界与估计器不能被建议覆盖。

修改未执行记 `unexecuted`；执行后原检查明确失败记 `executed_failed`；修改实际执行且原检查通过记 `local_verified`；执行了但未观察到对应检查记 `executed_unverified`。后续其他错误独立保留，不抹去原检查通过。缺失或不合格的经验草稿不会丢弃合法代码修改：保存宿主修改差异与结果，建议保持未知。代码静态检查拒绝才记 `rejected`。

上述实际修改无论成功、失败或未执行都可检索，返回内容明确状态和限制；“有修改”不等于“有效”。相同建议、数值和条件追加经历，数值或条件变化保留变体；反例留在经验中，不因失败自动隐藏。主动撤销、兼容性、同 episode 排除和错误前时间边界仍生效。完整代码的 5/6 晋升条件独立不变。

结构示例（占位符表示宿主绑定值，不是实测结果）：

```json
{
  "skill_id": "skill_<correction_sha256>",
  "version": 1,
  "status": "unexecuted",
  "has_code_change": true,
  "available_at_ns": "<host revision timestamp>",
  "applicable_conditions": {"compatibility": {}, "phase": "release", "public_conditions": {}},
  "failure_context": {"kind": "release_rejected", "physics_step": 0, "error_event_seq": 0},
  "numeric_correction": [],
  "repair_advice": "从实际到达释放姿态后累计连续时间，继续使用原公开释放检查。",
  "public_validation": {"executed": false, "passed": false},
  "provenance": {"episode_id": "<source episode>", "before_source_version": "<sha256>"},
  "keys": {"language": {"template": "public_error_template.v2", "basis": "pre_repair_error", "context": {}, "context_sha256": "<sha256>", "text": "<canonical error text>", "text_sha256": "<sha256>"}, "tactile": []}
}
```

局部验证使用宿主实际记录的释放、XY 到位或公开测力采样检查。公开保持检查仍只是动作步采样证据，不冒充原生逐物理步达标。无法对应原检查时保持未验证，但仍可检索；数值来源解析不再影响该检查结果。

### 仅在错误时检索

首次生成、正常动作和正常结束不搜索技能，也不额外调用整理 LLM。原生终止、END、动作预算或修复预算耗尽时停止。每 episode 最多一次修复。

语言 key 由固定模板构造，不做 LLM 摘要，不加入 depth/估计力序列或触觉诊断。CPU 文本编码器为 `intfloat/multilingual-e5-small`，revision `614241f622f53c4eeff9890bdc4f31cfecc418b3`；双方 `query:` 前缀、512 token 上限、掩码均值池化、L2 归一化、余弦排序。模型、tokenizer、运行库版本和 SHA256 在 `/mnt/data/ljz/opentacbench/repair_text_encoder_v1/multilingual-e5-small/freeze.json`。隔离 `cpu-env` 只安装文本依赖，不修改 Isaac 环境的包。模型请求只读本地文件，不在实验过程中下载。

语言检索使用 `public_error_template.v2`：

| 内容 | 使用时机 |
|---|---|
| 当前错误的规范化修复前描述 | 查询文本 |
| 历史技能对应的修复前描述，使用完全相同的模板 | 被编码、被搜索的文本 |
| 历史修正建议、具体修正值、适用条件与验证证据 | 命中后加载，交给修复 LLM |

`make_repair_language_key` 统一生成当前和历史 key；历史记录直接保留当时冻结的 key，不在修复后用新状态重建。固定字段为任务、公开请求、错误类别、原检查、API、函数调用、职责、阶段及修复前控制设置。音名规范为 MIDI；力度去空白并统一大小写；等价整数/浮点和参数组别名统一；未知字符串为 `unknown`，未知数值为 null，不补 0。调用职责未知时不从函数名猜测。编码文本是排序、紧凑的 JSON，同样由 E5 添加 `query:`。

记录包含结构化 context、规范化 text、两者 SHA256、模板版本和 `pre_repair_error` 来源标记；错误 episode/step/event 序号另存审计来源，不进入文本。查找时从 context 重建并核验文本，不允许把建议、修复后数值或验证结论塞进被搜索文本。旧 v1、未标版本或校验不符的 key 不混入语言通道，记录跳过原因；不自动迁移或删除旧经历。

每条技能修订同时保存只含资格条件和 key 的 `search_<sha256>.json`。检索先读这些索引，排名后仅加载最多两条命中的完整技能修订；读取绑定到候选快照中的版本，避免过程中 head 变化带入新的修正方法。这是修复技能库内部的索引视图，不另建技能库，不依赖历史完整程序。建议和数值的变更不能反向改写原错误 key。

2026-10-28 19:03 UTC 的 v2 smoke 按用户要求只跑 GPU、未跑 CPU 测试。运行：`language_key_v2_seed3300_smoke_rep1_20261028T190349Z`，空库、GPU 0、gpt-6-sol。首次 `approach_timeout` 后生成 v2 key；一次补丁实际执行，正常路径检索 0 次。运行产物确认 `retrieval.json` 的语言 key 与技能正文、搜索索引中的 key 完全相同，索引不含建议、修正数值或验证详情。当前无合格历史候选，因此只验证空库 miss 与错误 key 原样入库，没有实测历史命中或排名收益。

结果为原生 1/6、原生尝试 1、公开完成记录 0、生成/清场动作 241/220、代码块 2、修复 1、END=False、exit=1。修正版正确报告 `quality=ok`，但 `press_timeout/public_hold_confidence=False` 触发原有停止机制。经验条件字段类型已正确；草稿把旧 90 步的源码出处错误标为 `task_request.current_source._approach`，数值来源核验失败，仍为 rejected，代码未晋升。该来源不是公开请求字段，未替模型伪造来源或认定技能有效。

初次/修复 API 请求耗时分别 51.341/49.538 秒，provider-reported token 为 32375。产物与源码快照位于 `/mnt/data/ljz/opentacbench/shadow_piano_language_key_v2_3300_20261028T190345Z`。
[运行日志](/mnt/data/ljz/opentacbench/capx-runs/language_key_v2_seed3300_smoke_rep1_20261028T190349Z/run.log) · [错误查询记录](/mnt/data/ljz/opentacbench/capx-runs/language_key_v2_seed3300_smoke_rep1_20261028T190349Z/tactile_error_repair/retrieval.json) · [私有 smoke 审计](/mnt/data/ljz/opentacbench/capx-private-audits/shadow_piano_visual_force_v1/language_key_v2_seed3300_smoke_rep1_20261028T190349Z_6db7098d949342eda9d61fc4e964ee51/language_key_v2_smoke_review.json)

触觉 key 默认冻结为 `tactile_action`。最近 0.5 秒由物理时间选取 16 个 30 Hz 点，不能直接取 60 Hz 的最后 16 条。使用错误事件之前的观测；同一物理 step 的晚到事件也被排除。不跨 reset，不补造动作或实际状态。纯判断调用记录借用观测的来源和年龄。无接触与缺失分开；5D 力估计链路保持原样。修复后数据只用于验证，不进入查询 key。

触觉距离沿用 `sqrt(mean((query - history)**2))`，按距离升序。默认输入 16 帧触觉、动作和实际状态，比较冻结编码器输出的 32D 归一化向量；显式选择 `window4` 时才是标准化后展开的 64D 向量。不同编码/预处理契约不比较距离。

`CAPX_SHADOW_SKILL_RETRIEVAL=language|tactile|joint` 选择排序。候选须已保存实际代码修改、其当前修订早于错误、来自其他 episode、未主动撤销且无明确适用冲突；验证状态仅作为返回内容。两路各取前 5 个不同修正；joint 使用等权 RRF `c=60`，最多返回 2 条。触觉缺失时 joint 明确回退语言，tactile 不回退语言；文本编码器失败也不会伪装成词法检索。没有候选按原路径无历史修复；分数不是成功概率，不设未标定的阈值。

修复请求只传当前完整源码一份、公开错误/调用/进度、当前公开观测摘要与最多两条技能。不发送完整历史程序、轨迹或向量；宿主绑定 hash。证据大小和原有 300 秒／8192 输出 token 预算保持，API 清除代理。

`repair_skills/usage.jsonl` 分别记录 retrieved、cited、applied、locally_verified；不能证明具体建议实际应用时 applied 为 null。`piano_independent_memory_result.json` 分别报告代码晋升和技能局部状态。完整程序的原生成功不能给所有候选技能自动记功。

### 验证边界

CPU 测试与真实编码器日志保存在 `/mnt/data/ljz/opentacbench/test-tmp/independent-memory/`。包括模拟错误→完整模块修复→公开释放检查→技能入库、5/6 宿主筛选、检索去重/缺失处理/因果边界、数值来源与私有隔离。真实文本编码器和冻结触觉编码器也单独在 CPU 检验；这不等于新的 GPU 任务成功率验证。保持旧环境和历史数据，不自动导入旧候选；新实验需指定空数据根。

后续按用户要求缩减 CPU 测试：最终关键链路 7 项通过（`final-critical.log`），语言模板隔离 1 项通过（`language-final.log`）。真实 CPU 编码器分别输出 E5 384D 和 tactile_action 32D，均为 L2 归一化向量；触觉实际选取 0.5 秒内 16 个 30 Hz 点，日志为 `text-encoder.log`、`tactile-encoder.log`。模拟验证包含局部释放检查通过但完整程序不晋升；并不等于 GPU 修复有效。

### 单条 GPU smoke（2026-10-28 UTC）

均通过原 `./run_responses`，GPU 0，模型沿用配置 `gpt-6-sol`，代理清除，单 trial、一次修复、不自动重试。

- 17:39 的诊断发现启动器遗漏新配置开关，实际走旧 bundle 路径；已用 SIGINT 停止本次进程，保留日志，不计为新机制验证。已补齐 `launch_utils._load_config` 并检查实际配置传递。
- 17:44 的重跑使用全新的空库，实际兼容标记为 `independent.v1`。目标：F4 轻、A4 轻、C#4 重、A4 轻、A4 重、E4 轻，各 0.12 秒。

| seed | 原生成功/请求 | 原生尝试 | 生成/清场动作 | 代码块/修复 | 初始/修复 API 秒 | token | END/退出码 |
|---|---|---|---|---|---|---|---|
| 3300 | 1/6 | 1 | 233/220 | 2/1 | 42.704/42.064 | 33162 | 否/1 |

首份程序将接近过程消耗在按压预算里，出现 `press_timeout`。错误事件序号 3298，查询来源最大序号 3266；同 episode、无错误后帧，触觉质量 ok、32D。空库返回 `no_compatible_verified_skill`，无历史补救请求正常返回，替换模块实际执行。公开输入为 22714 字节（紧凑 JSON），当前完整源码一份、无历史程序或向量；正常执行检索 0 次，结束整理 API 0 次。

修正版增加独立的实际到位检查，原生确实记录首音成功。但其 PIANO_NOTE 把 `quality` 写成控制结果 `held`，原有失败识别器将该非 `ok` 值作为公开错误；一次修复预算耗尽，未完成公开收尾与 END。经验草稿把 `applicable_conditions` 返回成字符串，状态 rejected；合法代码仍执行，证明草稿失败不阻塞代码。完整程序未达到门槛，active 未被覆盖。**本条未产生可推荐技能，也未证明跨 episode 触觉检索收益。**

运行后明确了 quality 和草稿字段格式提示，补全独立参数文件校验、语言 key 控制参数白名单及 press_timeout 对应公开保持检查的映射。这些后续改动只通过定向 CPU 检查，尚未再次跑 GPU；未放宽失败判断、释放保护或原生规则。代码输入参数版本指宿主注入的执行参数，程序内的动态控制值仍保存在公开动作/调用日志中。

产物：

- [运行日志](/mnt/data/ljz/opentacbench/capx-runs/independent_memory_v2_seed3300_smoke_rep1_20261028T174439Z/run.log)
- [独立维护结果](/mnt/data/ljz/opentacbench/capx-runs/independent_memory_v2_seed3300_smoke_rep1_20261028T174439Z/piano_independent_memory_result.json)
- [私有 smoke 审计（不进入检索）](/mnt/data/ljz/opentacbench/capx-private-audits/shadow_piano_visual_force_v1/independent_memory_v2_seed3300_smoke_rep1_20261028T174439Z_78772734e4844c9ab64978b8fffe43ed/independent_memory_smoke_review.json)
- [HEAD 视频](/mnt/data/ljz/opentacbench/capx-runs/independent_memory_v2_seed3300_smoke_rep1_20261028T174439Z/trial_01_sandboxrc_0_reward_0.000_taskcompleted_0/videos/video_combined.mp4)；[第三方视频](/mnt/data/ljz/opentacbench/capx-runs/independent_memory_v2_seed3300_smoke_rep1_20261028T174439Z/trial_01_sandboxrc_0_reward_0.000_taskcompleted_0/videos/video_combined_third.mp4)
- 数据根 `/mnt/data/ljz/opentacbench/shadow_piano_independent_smoke_v2_3300_20261028T174436Z`；`implementation.json/diff` 固定本次实际执行代码。下一轮若要求冷启动，应指定新的空目录，不导入本次记录。

2026-10-26：按用户要求删除额外的批量运行和套件审计脚本。当前统一使用 t-cap 的 `./run_responses`，多个 seed 直接用 shell 循环串行调用；未经明确允许不新增脚本文件。下文保留历史实现和实验记录，已删除脚本的名称仅用于说明历史来源。

## 2026-10-26：不完整经验归档、动作函数复用和推理耗时

本轮通用 `play_piano_shadow_digit_code_memory.yaml` 新增 `shadow_piano_action_memory`，默认开启，可用 `CAPX_SHADOW_ACTION_MEMORY=false` 关闭。已有 A/B/C 三份配置的控制提示和策略开关不变；它们没有默认启用动作函数库。新模式使用独立兼容版本，旧缓存不会被无提示地当成新模式缓存。

经验保存与整段策略准入现在分开。自适应模式收到的初始、无效及修复提案存入 `candidates/<SHA256>/`，结束时不论公开尝试是否完整，都保存 `episodes/<run>/episode.json` 和运行目录的 `piano_experience_archive.json`。该记录包含公开音序、已结束次数、公开错误、释放检查及参数；不读取真实力、原生得分、reward 或 success。不完整策略为 `experimental_partial`，无效源码为 `invalid`。只有完整公开检查通过的整段源码才作为可直接复用策略；“公开检查通过”不等于原生评分成功。新模式中未执行的结束后修复保持候选，不替换当前有效策略。

`action_skills.json` 保存按 Python 函数提取的索引。每条记录有签名、文档、源码位置、依赖、捕获变量和来源版本。可独立调用的函数会被提取；嵌套函数若捕获 `piano_state`、当前目标等外部局部变量，则标记为需要显式传参，依赖它的函数也不会被错误地独立加载。不根据函数名预先规定钢琴动作阶段；由 LLM 编写和组合通用动作函数，音序仍来自每次 reset 的公开 request。

完整策略缓存未命中时，主机可以提供同一兼容来源中的独立函数声明，并在本地加载实现。LLM 收到签名、适用说明、父程序的公开证据状态和失败提示，不重复上传函数体；新规划可选用这些函数，修复函数则使用新名字，避免无意覆盖。候选函数仍是实验性组件，不能将父程序完成若干次公开提交称为该函数已验证成功。初始规划源码与实际拼装执行的完整源码均保存，便于追溯。整段已检查策略命中时仍然跳过首次生成；修复请求则保留一份完整待修复源码。

新模式通过普通 `./run_responses` 运行也会在结束后归档。结束处理带公开证据指纹，外层已有实验运行器再次调用时直接返回同一结果，不重复发送整理或修复请求。每个 episode 的一次修复预算仍保留。

耗时与动作分开记录：`llm_usage.json` 新增逐请求及分阶段 `timing`，包含失败请求；真正零请求记零，缺失历史计时记未知。`piano_execution_timing.jsonl` 记录每个代码块的执行墙钟时间、动作/物理步起止和源码版本；`piano_timing_summary.json` 汇总两类数据。API请求时间含网络、服务商排队/计算及重试，非流式接口不能进一步分离纯推理时间或首 token 延迟。代码执行时间含仿真、渲染与触觉估计，不冒充纯机械动作时间；preflight独立列出且不假定耗时为零。

改进应分别比较：原生逐音成功数、是否尝试完整、首次生成/修复/整理各自调用数和等待秒数、代码执行墙钟时间及动作数。先确认完整性和成功表现，再判断是否减少推理等待；不能用漏弹、提前退出或减少保持时间获得的少步数称为提升。本次两 seed 只检验功能与统计链路，不作为充分的成功率或推理速度因果实验。

本轮必要CPU验证77项通过，包含部分经验保留、依赖/闭包检查、不同音序复用函数体且不上传源码、候选不直接执行、有效缓存零API、结束处理幂等及耗时未知值处理。历史逐请求时间和本次功能实验保存在数据盘独立目录 `/mnt/data/ljz/opentacbench/shadow_piano_memory_comparison/action_functions_3300_3301_20261026T092020Z/`。首次启动预检返回503“没有可用token”，未启动Kit；保留终端记录，间隔后重试通过预检。GPU结果以该目录最终记录为准。

两组功能试跑已结束：3300为1/6、335动作、两代码块，首次/修复API105.40/106.07秒，代码执行80.79秒；3301为3/6、936动作、单代码块，首次API97.86秒，无修复，代码执行231.37秒。3300的释放检查失败经验与8个候选函数保留，但3301没有引用这些函数，因此不能宣称发生有效跨seed代码复用或推理提速。3301完整公开提交后保存了有效协议版本，未追加下一组验证。原生失败仍单独审计，不输入更新器。

试跑后补充按AST识别函数参数字典字段及返回字段，帮助规划器理解调用约定；这些是源码中的字段提示，不是完整类型证明。还修正了未引用的旧函数也拼入源码的问题：今后只拼装被引用的函数和依赖闭包，检索时过滤仅闲置于旧模块中的函数。损坏索引会保留原文备份并回退重新规划。新增针对性验证通过；GPU既有结果保持原样，不因后续修正重跑或改写。所有提案和实际执行源码均可追溯。

本实现将代码复用与文字建议分开。新配置 `env_configs/opentac/play_piano_shadow_digit_code_memory.yaml` 使用参数化入口 `run_piano(request, piano_state)`；request 由每次原生 reset 后的公开音序生成。缓存命中时跳过初始模型请求，源码通过普通 CaP-X 执行链运行，不使用 oracle、专家策略或旧动作轨迹。

独立实验目录中的 `code_memory/active.json` 保存当前兼容版本，`versions/<源码 SHA256>/policy.py` 与 `history.jsonl` 保存源码及公开来源。兼容性覆盖接口、240 Hz 物理、四物理步动作、DIGIT 配置、力模型和公开控制坐标系；音序和 seed 不进入兼容键。程序可在 `piano_state['policy_parameters']` 中保留四组黑白键×轻重的有限数值参数。每个 episode 重置进度，代码段恢复保留同 episode 的已完成尝试。

初始源码仅允许函数、有限顶层参数和 math import，拒绝硬编码任务音符、顶层执行和非公开访问。主机补上公开 request、调用入口和 END；模型生成的模块自身不调用入口。缓存准入依据公开尝试链及释放检查，状态为 `public_execution_checked` 或待验证的 `pending_public_execution`，不以原生分数或真实力筛选。

新配置要求提交每次尝试前，实际食指弯曲≤0.02 rad，实际腕高在0.04±0.002 m内，连续持续≥0.12模拟秒。未满足时拒绝提交、记录公开错误，不自动代替生成代码走物理步；已有原生终止状态允许合法的最后提交。原生任务、琴键角度和判分没有修改。

仅在代码错误、OOD 检查点或公开控制条件未满足时请求修复。每个 seed 共享一次修复预算：执行中用过就不再结束后评审；未用过时可根据公开失败为下一组提出修复。当前首次生成与代码修复默认均为300秒、8192输出token，不进行无条件文字技能整理。修复提示含当前源码、固定控制要求、公开动作和测量状态；真实力、原生评分和 reward 从不输入。

公开摘要保留尝试结束附近连续八步动作，避免只保留稀疏力查询。阶段动作数按命令轨迹估计：弯指前为接近，弯指后为按压，弯曲目标开始下降后为释放；它是公开控制轨迹分类，不是原生接触事件标签。

新增独立第三方相机在原生首次reset前创建，仅添加渲染传感器。位置[-0.58,0.85,0.66]，朝向[0.12,0,0.095]，不改变HEAD相机与标定。`video_combined.mp4`保留HEAD，`video_combined_third.mp4`保存第三方；两者共用物理时间戳和接触事件合成音轨，第三方画面不发给模型。

当前运行入口为 t-cap 的 `./run_responses`。历史十 seed 实验曾由额外套件脚本编排，该脚本现已删除；已有 `results.md/json`、对照图和私有审计保留。没有模型请求时token明确为0；代码生成和修复开销均计入。

运行前50项相关CPU测试通过，覆盖缓存命中不请求API、音序变化、同episode恢复、兼容失败、提前释放拒绝、公开/私有隔离与第三方视频写入。GPU结果以实验产物为准；释放检查与整理输入也发生了修复，成功率变化不能单独归因于记忆。

实验目录：[code_memory_3300_3309_20261025T094300Z](/mnt/data/ljz/opentacbench/shadow_piano_online_skills/code_memory_3300_3309_20261025T094300Z)。首组3300已完成：5/6、六次尝试、END，1375个生成动作、220个清场动作；首次生成加一次修复共58352 token。生成代码将八次合格采样误当成0.12秒，实际首尾仅0.1167秒，被公开释放护栏拒绝；模型自动修复成九次采样。修复后源码哈希为 `606c9a2943b2501ee505f0c032080da392aafcef654fbd00f53fcf3f9ef98e27`。

首组检查中补齐了缓存框架的两项校验：固定目标的精确只读副本可随参数保存；较早代码块的已恢复异常不应否决最后正常完成的代码。随后仅重新处理公开记录来核对入库，保留修正前结果；没有重跑物理、追加API请求或人工修改模型策略。补充验证后共52项相关测试通过，修正记录保存在实验目录`post_smoke_framework/`。

两份首组视频均为115帧、5fps、23秒，均含AAC音轨，第三方视角已实际查看。剩余3301–3309由tmux后台会话 `shadow_code_mem_20261025T094300Z` 串行继续；[实时十行表](/mnt/data/ljz/opentacbench/shadow_piano_online_skills/code_memory_3300_3309_20261025T094300Z/results.md)、[后台日志](/mnt/data/ljz/opentacbench/shadow_piano_online_skills/code_memory_3300_3309_20261025T094300Z/suite.log)。完成后自动生成对照图和私有审计，未完成的行保留待运行状态。

3301启动已验证实际跨seed命中同一源码版本，初始API请求数与token均为0，且当前请求音序与3300不同。证据保存于实验目录`acceptance_smoke.json`；后续修复与最终判分仍以逐组结果为准。

## 三组独立初始化实验（2026-10-26 修订）

历史比较运行器（现已删除）使用 `shadow_piano_paired_memory.v5`。A、B、C 在 seed 3300 均从空代码和空参数记忆独立请求 LLM，不跨组复制源码或参数。A 每个 seed 重新生成；B 从 seed 3301 起复制 B 自己在 3300 的初始源码及空外部参数，单次执行中的修复不改变冻结版本；C 使用 C 自己公开执行验证或公开反馈修复后的版本。每个 episode 的进度重新开始，不重放上一个 seed 的执行状态。

顺序固定为先完成 A 的全部 seed，再完成 B，最后完成 C。两 seed 实验依次为 A/3300→A/3301→B/3300→B/3301→C/3300→C/3301。manifest 保存 `execution_order=arm_major_A_then_B_then_C` 及完整队列；拒绝恢复旧 v2 交错顺序、v3 单配置协议，或被改成 seed 优先顺序的 manifest。默认模型为用户指定的 `gpt-6.1-sol`。

每组初始响应存于各自运行目录，原始源码版本及来源保存于 `arms/<A|B|C>/initial.json` 和 `initial_memory/`；B 的初始版本在读取私有判分前冻结，不按成绩挑选。三组仍共享公开键位标定与固定控制条件。各组只统计自身实际生成、修复用量，不把 A 的 token 加到 B/C。未返回用量仍为未知。

此前两轮 A 初始源码共享给 B/C 的结果保留为旧协议历史，不能作为本协议的独立初始化结果，也禁止用新运行器恢复旧协议目录。不同初始生成本身带来的随机性需要单独考虑，不能把一次组间差异完全归因于记忆。

本次只运行 seed 3300、3301，六个独立进程串行执行。组间冷却 60 秒；API 错误后保存结果并暂停，不自动补跑。释放保护、控制提示、四物理步动作频率、5D 力估计器及 OpenTacBench 均保持原样。HEAD 和第三方视频及私有审计分别保存。

旧批量运行命令已移除。直接调用 `./run_responses` 时显式设置配置、任务 seed、GPU、模型和数据盘输出目录，并清除大小写代理变量。

本次实际运行结果：首组 A/3300 在首次生成请求阶段超时，未收到源码，动作数、尝试数和代码块均为0；token未知，其余五组未启动。按API故障暂停约定停止。详见[本轮记录](/mnt/data/ljz/opentacbench/shadow_piano_memory_comparison/independent_3300_3301_gpt61_20261025T173235Z/results.md)和[故障分析](/mnt/data/ljz/opentacbench/shadow_piano_memory_comparison/independent_3300_3301_gpt61_20261025T173235Z/analysis.json)。

2026-10-26 超时修正：首次代码生成遵循 `CAPX_LLM_TIMEOUT_SECONDS`（当前启动脚本300秒），修复请求独立限制120秒。此前共用 `_query()` 的120秒限时导致初次请求提前结束。三组控制提示、物理及评分不变；新批次实际开启 `./run_responses` 的preflight。预检请求单列计数，原脚本不报告其token，生成／修复token列不含预检用量。

2026-10-26 用量审计：旧 A 与最新 A/B/C 首次请求的已存 prompt、归档 CONTRACT、转换函数及重建请求 JSON 一致，均无图片、23,606 字符，要求 `low` 和输出上限 8192。旧 A 首次返回 8,189 总 token；新 A/B/C 返回 50,130／57,058／53,890，其中推理分别32,947／39,096／35,909，输出计数均超过请求上限。这些是服务端报告值，客户端没有再加一遍推理 token，也没有累加重复快照；尚不能当作已核实的实际计费用量。修复另发一份当前源码及公开摘要，的确有额外输入成本，但解释不了首次请求的异常。

已补充请求 JSON 的 SHA256、请求 reasoning effort、响应自报 model／reasoning effort／max_output_tokens／status／id，以及超出请求上限或总数不一致标记。保留原始 usage，不将服务端数字裁剪成预算值，不保存凭据或完整响应。旧日志没有这些响应元数据，不能断言服务商实际使用了哪个后端模型。当前未启动新 GPU 或 API 实验，历史批次保持停止；[逐请求审计与复现脚本](/mnt/data/ljz/opentacbench/shadow_piano_memory_comparison/independent_initial300_3300_3301_20261025T184748Z/token_audit/report.md)留存完整证据。

## 三份独立配置与动作优化

| 组 | YAML（位于 env_configs/opentac） | 初次生成／跨 seed 复用 | 结束后行为 |
|---|---|---|---|
| A | play_piano_shadow_digit_abc_a_fresh.yaml | 每个 seed 重新生成；即使误传已有缓存也忽略 | 不调用 API，不晋升代码或参数 |
| B | play_piano_shadow_digit_abc_b_frozen.yaml | 3300独立生成，3301复用B自己的原始源码和初始参数 | 不调用 API，不覆盖冻结版本 |
| C | play_piano_shadow_digit_abc_c_adaptive.yaml | 3300独立生成，后续读取C自己的有效版本 | 公开失败且本轮尚未修复时可修复一次，并保存待验证版本 |

这些是运行时强制执行的 `shadow_piano_memory_policy`，不是仅供 LLM 阅读的文字。策略保存在 session 中，结束处理时核对，避免运行器漏传参数后恢复成默认更新行为。三组均可在执行中修复一次；C 的结束后修复与执行中修复共用同一预算。三组都关闭文字技能库加载和无条件整理。

三份文件的任务物理、提示、传感器、力估计器、释放护栏和一次修复预算相同；运行器启动前比较，只有记忆策略字段允许不同。三组首次生成都增加“满足所有固定条件后，减少多余接近、微调和等待动作”的共同目标。A仍使用相同参数化模块入口，每轮独立生成，并非早期普通脚本提示的原样基线。

C的修复请求额外启用 `optimize_actions_on_repair`：附带公开的逐次尝试阶段动作数、释放检查结果及近期观测，要求优化可复用函数和黑白键×轻重参数。它不接收原生成功数或真实力；不把提前中止、漏弹导致的少动作视为改进。固定悬停高度、最短按压时间、保持／释放时间和控制边界均不得缩短。提案仍须通过源码检查，并在后续运行中验证；公开依据不足时维持原策略。

B只要求首轮高效规划，后续冻结；C只在已有公开失败修复调用中进一步优化。成功后不额外发起“寻找更优策略”请求。因此本实验观察的是失败驱动的策略改进和代码复用，不承诺每个seed持续减少动作。若要测试成功轨迹上的主动优化，应另设预算与实验条件，不能混入这组结果。

现有 `./run_responses` 已识别三份配置，使用 Shadow/DIGIT 启动入口，内置 preflight 开启。旧 A/B/C 编排命令及其专用 CLI 参数已停用；多个 seed 由 shell 循环直接调用 `./run_responses`，GPU由调用者指定。既有结果表、配置路径、源码版本、各类请求/token和产物路径保留在历史实验目录。

本次52项相关CPU测试通过，包括配置传递、A忽略旧缓存、A/B结束后零API及缓存不变、C优化提示与一次预算、完整shell链使用指定模型且无代理；未启动真实API/GPU实验。此前服务端token报告异常尚未证明恢复，配置拆分不会自动修复该问题。


## 本机限时与用量审计修复（v5）

首次生成和代码修复默认均300秒，HTTP与本机总限时一致。启动器参数为`--llm-timeout-seconds 300 --repair-timeout-seconds 300`；直接使用`run_responses`时，`CAPX_SHADOW_REPAIR_TIMEOUT_SECONDS`未设则继承`CAPX_LLM_TIMEOUT_SECONDS`。本地截止标`local_deadline`，接口读取超时标`transport_timeout`，并保存阶段、实际耗时和未知用量。此前章节中120秒代码修复限时是旧批次历史；文字技能整理仍独立限制120秒。

每个usage事件新增`accounting`，保留原始报告与attribution明细，差异标`needs_review`。明细不保证完整，不自动按倍率修正；`comparable_totals`排除有已知矛盾或缺失的请求。ABC表显示“报告token”和“计数状态”，曲线只使用可比较值。这是报告内部一致性检查，不是已核实计费。

协议升级v5并冻结请求预算，旧v4结果保留，重新实验须使用新的输出目录，不能混版本续跑。A/B/C控制提示、原生任务、力模型及一次修复预算未变。81项相关CPU测试通过，本次未调用真实模型或GPU。详见[修复与已存响应重审](/mnt/data/ljz/opentacbench/capx-diagnostics/llm_timeout_accounting_fix_20261026T051709Z/report.md)。

## 2026-10-27：C公开控制反馈修复与实验范围

此前ABC不是文字技能库或局部动作技能复用的消融。三份配置都设置`shadow_piano_public_skill_library: null`、`evolve_skill_library: false`，关闭通用trial/persistent触觉记忆，且未启用`shadow_piano_action_memory`。因此三组都没有读取文字经验或局部动作函数库。共同控制提示、冻结力估计器、共享公开键位缓存仍然存在，不能把这些称作跨episode策略技能检索。

| 组 | 当前跨episode机制 | 技能库 |
|---|---|---|
| A | 每个seed重新生成整段模块 | 关闭 |
| B | 冻结并直接执行B自己的首次模块；临时修复不保留 | 关闭 |
| C | 复用整段模块，保留公开反馈修复 | 关闭 |

C结束后原先只检查异常或未完成尝试链。即使`PIANO_NOTE`明确报告`alignment_lost`、`press_cap`或OOD，安全完成六次释放/提交仍可能不触发API。因此历史C3302–3304的零调用只证明整段缓存可执行，不能证明持续优化已运行。

修复后的反馈版本为`public_control_feedback.v2`。公开摘要单独提取带有效请求index的结构化`PIANO_NOTE`，识别定位丢失、未建立接触、保持超时、OOD、超力等公开控制问题。JSON和Python字典格式均可解析；只保留音序索引、公开音名、固定原因、公开质量/信心及动作步。一般`uncertain`、单独的低信心或`public_hold_provisional`不自动当作失败，更不当作原生判分。真值、原生计分和任意额外字段不进入新反馈结构。

仅C把这些`control_failures`加入结束后补丁触发条件。仍共享每seed一次修复预算：若执行中已用完，记录`repair_budget_exhausted`；否则请求一次补丁，先修复接触/保持等公开控制问题，再考虑减少冗余动作。A/B的更新行为和全部物理、力目标、控制边界及时间约束不变。补丁不会重弹已经提交的音，只保存为后续待执行验证的提案；启用局部动作记忆时仍保留候选，不直接取代已检查策略。

结果新增`feedback_version`及`post_episode_repair_decision`，明确区分请求、预算耗尽、没有公开问题、只读或策略禁用。历史结果不重新处理或覆盖；新的反馈实验须独立保存结果，不能与旧C零请求记录混作同一实现。

若研究目标转为“历史接触经验如何改善规划”，建议另行确定新的消融：A无历史技能；B使用冻结的局部技能与适用条件；C检索同类局部技能并根据公开反馈更新。三组每个新请求都由LLM规划，B/C复用的是局部函数与有来源的策略经验，而非无条件跳过整段规划。该重新定义尚未实施，不追溯改名本轮ABC；固定较好代码减少生成波动也可能提高表现，不能据一次独立初始化认定为技能学习效果。

## C触觉技能冷启动／复用试验

用户随后要求先测试技能复用本身。本试验通过已有`./run_responses`及独立配置`play_piano_shadow_digit_tactile_skill_pilot.yaml`串行跑同seed、同音序的两次执行，不新增编排脚本。试验前保存的本地版本为CaP-X `0ccc026`、t-cap `c85ac4a`。

本次技能范围明确限定为“历史接触匹配的按压保持初始化参数”，绑定LLM生成的`press_and_hold`函数版本。相同控制源码在两次执行中均被冻结：冷启动查询空快照，记录公开保持窗口；复用运行加载这些记录。它暂不检验任意新函数生成或跨任务泛化，也不把省去API调用当作控制收益。

接口`query_tactile_skill`读取当前最初三帧有效接触的5D中位数，在相同黑白键、轻重和传感器/力模型/控制器版本内匹配。距离按冻结标定模型的IQR归一化，RMS不超过1且单维差不超过3时才命中；这是试验设计阈值，尚非经验证的检索阈值。参照只包含公开特征，无私有标签。调用时可获得相对当前探测高度的Z修正；生成代码仍按原有每步最多50微米及总量1毫米限制逐步应用，并持续检查新触觉。公开事件分别记录查询和动作到达所检索参数；同一技能ID的首次实际应用单独确认。

`record_tactile_skill`要求最近九次公开力估计连续、实际食指接近完全弯曲、力高于公开保持门限且低于原上限，并跨越至少0.12物理秒。保存的修正是这些保持窗口中已执行Z命令相对探测高度的中位数；每类最多保留首条。记录函数名/函数哈希/控制器哈希、原始接触特征、动作步和来源。状态仅为`public_hold_observed_not_native_verdict`；4物理步采样不能证明中间每个物理步的官方评分。

冷启动当次新记录不会进入当次检索快照；复用快照只读，不能边测边改。OOD、没有足够有效接触或相似度不符时返回不可用/未命中，控制器走原有安全分支。所有动作仍调用原生`Task.step`，任务、评分、估计器和数值硬边界不变。

首次接线诊断发现配置加载器漏传`shadow_piano_tactile_skill_pilot`，仅停止本次进程并保留退出码137及所有日志。补齐透传并添加配置测试后，原样使用该次首份LLM源码，未查看原生分数挑选代码，再建立新空记忆做有效配对。源码哈希为`5b577958b50d3f238bac286002cf48f666b06ee6d2677cad2693f0f9c139b7a0`。相关56项CPU检查与补充透传测试通过。

有效配对目录：[C_tactile_skill_pair_20261027T014842Z](/mnt/data/ljz/opentacbench/shadow_piano_memory_comparison/C_tactile_skill_pair_20261027T014842Z)。最终结果以该目录报告为准；接线中断不并入控制策略结果。

本次配对已完成：冷启动与复用均6/6、1143动作、1代码块并END；检索技能在F4/A4/A4/E4四次实际应用，功能链成立但未测出成功数或总动作优势。实测力MAE变化小于0.001 N，不作稳定提升结论。详见配对目录results.md；未追加实验。

## 四个新seed的冻结触觉记忆复测

2026-10-27追加3301–3304，共八条串行配对，运行代码和阈值未变。结果依次为3/6、4/6、5/6、4/6，两条件各seed动作数均相同（1164、1281、1275、1238）。前两seed各有一次白键轻音技能命中并实际应用，后两seed无匹配且目标指令逐帧一致。全部失败为重音，当前单条白键轻音记忆未覆盖。两个匹配窗口私有力MAE约小幅下降5.18%/4.48%，没有成功音数或动作数收益。样本少、每seed单对，不能宣称整体优势；未测LLM经验驱动的代码重规划。

结果和16份含音轨视频：[四seed留出复测](/mnt/data/ljz/opentacbench/shadow_piano_memory_comparison/C_tactile_skill_transfer_3301_3304_20261027T043600Z/results.md)。


## 触觉证据闭环与并行时序接口（2026-10-27）

新配置为 `env_configs/opentac/play_piano_shadow_digit_evidence_memory.yaml`，仍由 t-cap 的
`./run_responses` 启动，未增加运行脚本。默认独立数据目录为
`/mnt/data/ljz/opentacbench/shadow_piano_evidence_v1`，可用 `CAPX_SHADOW_EVIDENCE_ROOT` 指定新的实验目录。
这次交付只进行 CPU 模拟与回归验证，没有调用真实模型、启动 GPU 实验或训练编码器；不代表策略已改善。
旧 A/B/C、文字库、代码库和触觉参数试验格式保留，默认配置不会开启此功能。

### 执行入口约定（2026-10-27 修复）

代码记忆返回的是可复用模块：LLM 定义 `run_piano(request, piano_state)` 和辅助函数，宿主
`wrapped()` 注入当前公开音序与本轮状态，并调用入口一次。源码本身不应再调用 `solve()`、
`run_piano(...)`，也不应添加 `if __name__ == ...` 启动逻辑。原生任务仍活动时，宿主包装器仅在
完成尝试计数达到请求长度后请求 END；不以函数正常返回替代逐音完成与释放检查。

所有启用 `shadow_piano_code_memory` 的配置由加载器设为 `generated_code_mode: host_module`。
此模式发送模块生成系统提示，并关闭通用执行器的 `solve()` 自动补调用；普通脚本模式的提示与
自动补调用逻辑保持原行为。此前通用系统提示要求主动调用 `solve()`，与模块提示相冲突；已移除
代码记忆模式中的这项冲突。执行协议版本为 `host_policy_module.v2`，旧版本缓存需重新验证。

允许只含 `pass`／文档字符串、继承 `Exception`、`RuntimeError` 或 `ValueError` 的自定义异常类。
类体执行语句、类方法与装饰器仍不允许；模块赋值、函数默认参数与注解中也不能调用观测或动作函数。
真实的顶层入口调用仍会拒绝，错误指出具体调用及行号，不静默删改模型源码。普通异常类不会再被
笼统误报为“顶层执行动作”。以上是调用契约检查，不能保证生成控制策略本身正确。

### 原生终止后的主机收尾（2026-10-27 修复）

原生任务可在末音琴键完成释放时立即终止，此时手腕未必回到悬停位置。`native_done` 是终止
信号，不能解释成释放超时或成功。API 文档、模块契约和 evidence YAML 要求将它传回
`run_piano` 并正常返回，停止所有动作和等待；活动任务仍要求完整的实测释放检查。

策略正常返回且原生已终止时，执行器调用 Shadow 适配器的主机收尾钩子。钩子只补记原生
已经消费、但公开记录尚未结束的尝试，然后提交一次 END；不重新执行评分、物理动作或原生
`finish_attempt`。未尝试的音不会补齐，原生失败仍返回失败，代码异常不会被此兜底掩盖。
终止状态是否需要收尾只取决于生命周期信号，不根据私有成功标志选择策略或修改执行。

补记标记为 `finalization_source=host_native_termination`、`release_verified=false`，并记录
`native_terminal_end`；不篡改生成代码的 completed 计数或原始停止日志，不把主机收尾冒充
策略自行完成释放检查，也不据此晋升代码缓存。运行兼容签名新增
`terminal_finalization=native_done_host.v1`，避免与旧的收尾协议混用。

对 2026-10-27 21:07 完成的 joint seed3300，以原生成源码、已记录的公开观测与公开端点映射
做离线回放：1186 次目标动作、468 次测力读取均匹配原记录，末次物理步仍为5621；主机补记
最后一次尝试、END仅一次，CaP-X `Task Completed=True`，原生结果未变。该回放没有新物理步、
API 请求或 GPU 实验，不改写原运行的失败结果。

### 双通路与记录契约

现有每次采样的 5D（均值、三个面积比例、面积变化率）以及冻结的力估计器保持不变。
`instantaneous_5d` 与 `temporal` 是两个独立的表示类型，分别保存版本、质量与物理时间，
共同引用同一 `clip_id`。未接入编码器时，时序质量是 `not_configured`，即时表示和控制继续工作。

启用新模式后，适配器在每次原生动作结束自动记录一次公开观测，不依赖 LLM 是否调用测力 API，
不增加物理动作、不插值为 240 Hz。每帧保存 RGB、模拟 depth、参考 depth 和有效像素掩码的压缩
NPZ、SHA256，以及 5D、估计力/质量、动作、公开手部状态、动作步与真实物理步。
缺失通道不造数据；不可用力保留 null。样本去重按物理 step，reset 使用独立 episode ID。
阶段依据命令变化分为 approach/press/hold/release，这是控制阶段推断，不是原生评分阶段。

宿主跟踪实际 Python 函数调用、返回和异常，记录函数 AST 哈希、源码、调用 ID 与首尾时间；
同一函数调用多次产生不同调用 ID。仅定义或检索函数不会被记为实际应用。
完整公开轨迹留在磁盘；更新提示只取有界片段。真值和原生评分仍在已有独立私有审计中。

### 关联与更新

`associations.jsonl` 是只追加的内容寻址索引，关联片段、两类表示、函数版本、参数绑定、文字规则、
调用和应用结果。`TactileEvidenceIndex.related(id)` 可以逐层追溯这些关联。
旧触觉参数 API 的记录/检索/应用事件也接入索引；新配置不强制载入旧试验的 Z 修正权重。
当前检索以相同传感器、力模型、动作频率、坐标系、黑白键、轻重和阶段为条件，对 5D 用标定 IQR
计算距离；时序表示另按编码器与预处理版本隔离。近邻是待验证的经验，不自动改变动作。

以下为旧 `episode_end` 模式；当前配置已改为下文的 `repair_only`，不再在每轮结束调用整理器。
旧模式每轮结束通过现有 Responses 客户端执行一次联合整理，代替旧整段代码修复和独立文字整理。
输入包含当前规则、参数、最多两个实际调用过的函数、当前触觉片段，以及匹配到的历史片段。
源码不重复上传，全部证据 JSON 不超过 24 KiB，完整轨迹不直接发送模型。
输出最多两个完整函数替换和完整的下一版文字规则，每项改变或删除都必须引用片段 ID 与具体采样物理步，
并解释观察依据、修改理由与预期公开效果。允许修改策略的下压节奏、反馈与释放逻辑；策略固定等待数
不当作原生要求。原生持音时间、释放保护、力目标、估计器和 API 动作边界保持固定。

提案检查语法、入口/函数签名、引用、依赖、公共接口和受保护的力/持音/悬停参数绑定。
这是保守的代码检查，不是对任意 Python 控制策略的数学安全证明；动作和释放仍由适配器保护。
文字仍限 16 条、8 KiB。参数或注释修改可以留档，但 `ast_structure_changed` 为 false，不算逻辑更新。
一次请求最多 8192 输出 token、300 秒；清除大小写代理并禁止客户端读取代理环境，结束后恢复进程环境。
没有自动第二次整理调用，网络失败、非法提案和无修改分别记录，前一有效版本保留。

`bundles/<hash>/` 将源码、文字规则、参数和父版本绑定成一份不可变候选。
`pending_bundle.json` 指向待验证版本，下次 episode 优先尝试一次；`active_bundle.json` 保留最后通过
公开执行检查的版本。定义存在或仅通过编译不会晋升。检查要求新函数实际调用、公开尝试链及释放完成，
且无记录的代码/控制失败；异常或未调用保留旧有效版本。公开检查通过不是官方成功。
整轮中断后已保存的证据保留，启动过的整理调用不会被最终处理器重复发送。

### 时序编码器扩展接口

宿主可向 low-level 的 `tactile_temporal_encoder` 传入对象（或通过现有 `_target_` 配置实例化）。
对象提供：

```python
contract = {"version": "my_encoder.v1", "preprocessing": "depth_window.v1",
            "modalities": ["depth_m"]}

def encode(window):
    # window: frames, valid_masks, instantaneous_5d, physics_steps,
    #         timestamps_s, current_physics_step, actions, actual_state,
    #         episode_id, sensor_sha256, sources
    # frames 中可能包含 rgb/depth_m/reference_depth_m；缺失通道保持缺失。
    return {"vector": encoded_vector, "quality": "ok"}
```

未声明固定 window 时，沿用可变长度的已发生阶段窗口；声明 window 时，取同 episode 的
历史窗口，可以跨动作阶段但不能跨 reset，不能引用未来时间。编码器声明预处理/窗口策略版本；
维数不固定为 5，质量异常或编码失败只使时序表示不可用，不覆盖即时表示。
`TactileEvidenceIndex.encode_clip(clip_id, encoder)` 支持对已有公开帧重新编码，验证帧 SHA256，
追加新的表示节点，不改片段 ID、旧 5D、函数或历史关联。不需要重新采集才可换编码器。
在线编码与离线重编码应使用同一 contract；不同版本和维数的向量不直接计算匹配距离。
时序向量用于经验检索；给更新 LLM 的依据仍是可解释的力/接触变化及公开动作。

### 产物和检查入口

- 每次运行：`piano_tactile_evidence.json` 指向本轮公开 manifest；`piano_code_memory_session.json` 记录实际执行源码。
- 数据盘：`episodes/<id>/samples.jsonl`、`manifest.json`、连续 NPZ 帧；`associations.jsonl` 保存跨轮关联。
- 更新：运行目录下 `tactile_evidence_update/` 保存输入、提示、原始响应、提案和校验；
  `piano_evidence_update_result.json` 保存候选/公开检查状态及独立更新 token 与请求耗时。
- 模拟测试覆盖完整更新后再执行、未调用不晋升、失败回退、版本隔离、帧校验、异常追踪、
  缺失编码器、按动作采样而不额外推进物理，以及私有字段隔离。

后续实际运行时设置 `CONFIG_PATH=env_configs/opentac/play_piano_shadow_digit_evidence_memory.yaml`、
`CAPX_SHADOW_EVIDENCE_ROOT`、`CAPX_SHADOW_TASK_SEED` 和显式 `GPU`，沿用原有 `./run_responses`。
本次没有自动启动该命令。


### 四组并行试验协议（2026-10-27）

用户授权四组各 seed3300–3304，在不同 GPU 上并行；各组内部按 seed 串行。
同一配置通过 `CAPX_SHADOW_EVIDENCE_MODE=none|text|tactile|joint` 选择：none 每 seed 从零生成，
不读取代码缓存、不做结束更新；text 保存文字和代码但不检索历史触觉；tactile 保存代码和轨迹但
禁止持久文字规则；joint 两者都保存。三个更新组均能看到相同类型的当前回合公开触觉反馈。
因此更新组之间比较的是历史记忆类型；与 none 的差异也包含代码复用，不能全归因于触觉。

各组首份代码独立生成，不共享其他组的策略。所有组每 seed 使用新的键位缓存文件，清场与定位
动作单独统计；代码复用组可能直接执行已有定位逻辑，图片请求次数仍按真实调用记录。
固定 gpt-6.1-sol、low、8192输出token、四物理步、冻结5D、每次更新300秒，关闭整轮重试。
无记忆组仍保存审计证据，但后续 seed 不读取这些数据。时序编码器不参与本轮。
使用四个后台 shell 循环调用现有 `./run_responses`，没有新增运行脚本。
任务未全对的退出码1继续下一个 seed；缺少试验完成标记的基础设施错误暂停该组，保留现场，
不以补跑替换失败。每 seed 前校验运行源码摘要，发现共享工作区变化则暂停，避免混用实现。

## 16 帧短期轨迹接入（2026-10-28，CPU 验证）

`capx.memory.trajectory.model.EvidenceTrajectoryEncoder` 包装现有
`FrozenTrajectoryEncoder.encode_window()`，实现证据库的 `contract` / `encode(window)` 接口。
现有 evidence YAML 的 low-level 新增如下配置；仍使用 `./run_responses`，没有新增运行脚本。
本次未启动、重启 GPU 实验，也未调用 LLM。

```yaml
tactile_temporal_encoder:
  run_dir: ${oc.env:CAPX_SHADOW_TRAJECTORY_RUN,/mnt/data/ljz/opentacbench/tactile_trajectory_encoder_v1/run_20261027T091422Z}
  representations: [window4, tactile, tactile_action]
```

三种空间并存并独立检索，不合并或替换原 5D 力表示：

| 名称 | 表示 | 输入 |
|---|---|---|
| window4 | 64D，L2 归一化 | 16 帧原始 4D 特征按训练集 feature_mean/std 标准化后按时间展开 |
| tactile | 32D，L2 归一化 | 冻结 tactile/seed_0/best.pt；深度、参考深度、有效掩码 |
| tactile_action | 32D，L2 归一化 | 冻结 tactile_action/seed_0/best.pt；以上输入加每帧 4D 动作及实际状态 |

每个片段结束时，使用真实物理时间查找最近 0.5 秒的 16 个 30 Hz 目标时刻，只选择对应时刻
及之前的最新真实帧，允许年龄上限为模型固定的 1/30 秒。60 Hz 记录通常隔一帧取样；
不直接取末 16 帧、不插值、不补零、不借用上一 episode。样本不足或无效则无向量。
记录 16 个目标时刻、实际采样时刻/物理步、sample/clip ID、帧路径及校验值。
相邻阶段的历史允许进入同一窗口，引用仍指向原始片段。

动作顺序为 `[wrist_x_offset_m, wrist_y_m, lift_z_m, index_flexion_rad]`，
实际状态顺序为 `[wrist_x_offset_m, wrist_y_m, wrist_z_m, index_flexion_rad]`。
记录器已修正原先误用 `wrist_x_m` 的问题。旧记录中实际 X 为 null 时，不能以动作目标
或零代替实测状态；动作版返回 `missing_public_motion`。两种纯触觉表示仍正常运行。
窗口使用 episode 实际 sensor SHA，不能借用模型声明的 SHA 冒充实际传感器。

检索 contract 包括表示名称、checkpoint/归一化 SHA、预处理、采样窗口、传感器、坐标系、
动作频率及原有阶段/黑白键/轻重条件。同为 32D 的两个编码器也不互相匹配。
可选编码器加载失败被记录为不可用，保留即时 5D；模型和归一化未修改、未重新训练。

`TactileEvidenceIndex.encode_history(encoders)` 对本库已有 CaP-X 公开片段幂等建库；
新 episode 仅检索启动前的历史节点。专家评估 index 不导入技能库，其空 skill_id 不作技能。
`resolve_skill_evidence(clip_id)` 沿片段的真实调用 ID 找到函数版本、源码哈希和历史源码。
更新器输入包含有界的历史片段与最多两份历史局部函数；相同源码只存一份引用，向量不发送 LLM。
本轮也修正更新器先按函数名选中纯判断函数的问题：候选必须有实际调用和触觉片段关联。

真实数据验证使用两个已完成的 CaP-X 公开 episode，构建独立数据盘索引；原始档案未修改。
NPZ 使用硬链接复用原始字节并校验 SHA，未复制私有审计。48 个片段生成 144 条质量记录：
window4 48 条可用、tactile 48 条可用、tactile_action 48 条缺失实际 X 因而不可用。
两种可用空间各得到 56 条跨轮 top-3 匹配，均追溯到实际调用的函数与历史源码。
这是检索链路验证，不是技能相关性、控制收益或成功率评估。

真实记录的更新输入选择了 `press` 和 `release`，包含两段历史触觉和历史函数，
紧凑 JSON 为 24,134 bytes，小于 24 KiB；没有发起更新 API。
验证报告及独立索引：
`/mnt/data/ljz/opentacbench/capx_trajectory_retrieval_v1/validation_20261027T171630Z/validation.json`。
公开更新输入示例位于同目录 `offline_public_updater_input.json`。

## 错误触发检索与修复后晋升（2026-10-28）

当前 evidence YAML 使用 `shadow_piano_evidence_trigger: repair_only`、`max_regenerations: 1`。
兼容有效代码直接执行；缓存缺失才生成第一版。正常动作、阶段关闭和 episode 结束均不检索
历史经验，也不调用整理 LLM。公开轨迹和函数调用持续记录。旧 `episode_end` 分支只用于显式
选择旧协议及回归测试，不是当前配置的默认行为。

现有异常或明确 `PIANO_STOP` 进入修复路径后，先检查是否仍允许继续环境，再构造当前窗口。
仅有普通视觉检查点、任务尚未结束、一次 OOD 数值或私有低分不会触发新请求。
原生已终止不再动作，也不为继续动作请求修复。没有历史命中、没有有效窗口、首次代码语法
错误都可以使用当前公开错误进行同一次补救。窗口仍按物理时间取 0.5 秒/16 个 30 Hz 点，
可跨动作阶段但不能跨 reset；缺失状态和帧不补造。

历史候选必须关联一次真实修复及通过的公开检查；普通专家/执行轨迹不能冒充有效修正。
查询只看 episode 开始前的历史快照，排除当前失败自我命中，最多提供两条历史修正。
5D 使用冻结模型的尺度；三个时序空间各自匹配、不比较跨空间距离。保存错误时刻的来源帧，
历史查询也不能使用错误后才出现的帧。输入包括当前错误、局部源码、公开观测、历史修改前后
函数和验证状态，相同函数源码用哈希引用去重，不传嵌入向量或私有真值。

一次修复请求限 300 秒、8192 输出 token，取消代理，并保留 trial 外层 token/时延统计。
局部修复最多替换两个函数；无法定位局部函数或初版语法错误时可重生成完整模块。
响应须引用当前错误事件和可用触觉片段，检查语法、接口绑定及固定 force/hold/hover 约束。
API 失败、非法提案和未执行候选都存档，不重试，不覆盖旧有效版本。

### 修复边界与恢复

记录器新增单调 `event_seq` 和 `execution_id`，每个修复另有 `repair_id`、前后源码哈希。
异常保存生成函数位置，公开停止标记在打印时关联实际调用栈。用事件序号区分修复前后，
即使两者处于同一物理 step；API 等待不计为物理保持时间。

新模块从 `run_piano(request, piano_state)` 入口执行，绝不从原报错行自动续行。
宿主从公开 `attempt_finalized` 记录校正进度，检查其与适配器计数一致。重建执行命名空间，
移除旧函数和瞬态计时，保留已接受键位及合法参数，不信任生成代码的 completed 作为唯一依据。
`piano_state['recovery']` 提供已提交索引、当前尝试、物理 step、公开手部与触觉快照。
初始化只做一次；未完成尝试必须先确认当前状态。恢复后的第一步动作前，必须实际调用
`get_hand_state()` 和 `estimate_key_force(current_note)`；否则宿主拒绝推进物理。
这两个观察调用不推进仿真，策略仍决定如何释放、重新接近或继续。

END 根据宿主公开提交链或既有合法原生终止处理决定，不根据生成代码单独声称的计数。
新增恢复协议参与代码兼容性检查；旧的未验证 pending 不再优先试跑。

### 晋升与存档

旧的“整条轨迹无异常”检查只属于旧模式。新模式永久保留修复前错误，并只对新执行边界之后
检查：修改函数实际执行且返回、原失败检查重新通过、没有新公开错误、剩余提交链合法。
已完成音使用修复前的公开记录保留，不能因原错误被全部否定，也不能再次弹奏。
原生合法终止收尾与实测释放通过分别记录；前者不能冒充后者来验证一次释放修复。
OOD/保持类公开停止需要后续公开 PIANO_NOTE 明确报告当前音 quality=ok、
public_hold_confidence=true 且无 abort_reason，不能仅凭没有抛异常判定已修复。

通过检查后才原子更新 active_bundle；否则候选和失败经历保留，旧有效版本不变。
新函数未调用、原检查没有重做或运行中断时不会晋升。公开通过不是原生评分成功。
每轮结束只做这些本地检查与存档，`episode_end_api_calls` 必须为零。

现有运行目录新增 `tactile_error_repair/` 产物：`retrieval.json`、`public_input.json`、
`prompt.json`、`response.txt`、`proposal.json`、`result.json`、`verification.json`；
未走到某阶段的文件不会伪造。关联索引保存 `repair_case`，连接原错误、查询片段、
修改前后 bundle、实际执行边界及公开验证结果。没有新增独立运行脚本。

本轮只做 CPU 模拟和回归测试；没有新 GPU 实验、真实 API 请求、模型训练或任务修改。

### 固定历史 V0 的最小真实对照（2026-10-28）

现有证据 YAML 新增可选 `CAPX_SHADOW_INITIAL_SOURCE` 与
`CAPX_SHADOW_INITIAL_SOURCE_SHA256`。两者必须同时指定，且仅用于 repair_only 模式。
文件按原始字节验证 SHA256，再经过原有源码校验；它是未验证实验输入，不是缓存命中，
不读取 active 的源码、规则或参数，也不自动成为有效技能。正常运行不设置这两项即可。

本次预注册为 S0=3300、S1=3301、S2=3302，使用同一历史 V0：
`piano_abc_A_seed3300_rerun3300_gpt61sol_20261025T144041Z_smoke_rep1_20261025T144046Z`
的初次生成源码，SHA256 为
`b1fcfccb6832fcd203f2aa3f0ef43a22354c73454dd65968dfac9c3f71ea967c`。
历史公开记录在动作步185/物理步740出现 release_rejected，连续到位时间为
0.1166667秒，小于既有0.12秒要求。保留源码原样，不人为注入语法或任务终止错误。

1. S0 从空经验库运行 V0，最多一次修复，保存修复前窗口、修改和修复后公开验证。
2. S1 空历史对照从同一 V0 开始。
3. 同一 S1 有历史对照仍从 V0 开始，只可读取步骤1结束后冻结的经验；步骤2完全隔离。
4. 仅步骤3候选通过现有公开检查后，S2才直接继承其有效版本，不设置 V0 覆盖项。

步骤2、3固定模型、温度、配置、键位缓存和一次修复预算。没有触发指定可恢复错误时，
不算机制验证；只有私有低分也不触发检索。两组都修复成功只能证明链路可用，
不能单凭这一例声称历史触觉带来额外收益。未获验证候选不强制晋升来完成步骤4。

产物与执行状态：
`/mnt/data/ljz/opentacbench/shadow_piano_memory_comparison/minimal_error_repair_20261028T002124Z/`。
实验只调用现有 `./run_responses`，未新增独立运行脚本。

### 释放错误的两个可修改区域（2026-10-28）

现有证据YAML启用 `shadow_piano_repair_scope: release_regions_v1`。遇到release_rejected时，
完整策略只作为只读上下文；回复必须是带base_source_sha256和两个区域校验值的JSON edits，
不再允许以完整模块替换这类V0。其他错误仍走现有流程；不支持的函数布局明确拒绝，
不自动退回整模块重写。

- `release_body`：提交完整的嵌套release函数，同名、同参数、同返回格式。宿主仍放回原作用域，
  保留command等闭包依赖。只允许局部状态/计时、公开手部读取和既有command，不能写共享绑定。
- `attempt_recovery`：提交恢复分支内部语句。宿主固定guard为本轮resuming=true、error_kind为
  release_rejected且循环索引等于公开current_attempt/error_attempt；插入在measured_arrival之前。
  分支需读当前手部和当前音触觉，委托release完成释放，再走原finalize。不能直接动作、重弹、
  写进度或参数、捕获并压掉异常。continue路径必须先经过finalize，不能落入正常按压流程。

所有区域外源码按原字节复制。原有参数和force/hold/hover表达式保护仍在；原生评分、
实际释放检查与动作边界未变。区域内的静态检查是有限约束，不声称证明任意Python的行为；
公开执行及原错误重检仍决定候选能否晋升。恢复时此前持音信心不可由已脱离接触的窗口
重建，传None表示未知，不编造True或将未知写成原生失败。新seed的resuming=false，
必须完整执行其当前音序。

候选记录保存region_edits。历史检索命中后传这两个已执行的局部修改、来源和公开验证结果，
避免重复传输整段修正版；查询仍只在错误后发生，一轮最多一次API。每个修改函数的AST哈希
与实际调用匹配，旧错误留在修复边界之前。

本次局部修复试验预先固定S0=3300、S1=3306（无历史/有历史两次）、S2=3301。
此前S1=3301首音为白键重音，与S0的白键轻音不满足现有精确检索兼容条件；因此依据公开音序
在运行前换为3306，不放松检索条件，也不依据新运行成绩换seed。产物：
`/mnt/data/ljz/opentacbench/shadow_piano_memory_comparison/minimal_region_repair_20261028T015608Z/`。

### 局部修复经验与整段策略资格分离（2026-10-28）

释放区域补丁从修复执行边界检查到原音的实际 `release_verified=true` 提交：新release函数
必须在该检查前实际返回，新的入口函数必须实际调用，期间不能有新异常、停止或释放拒绝。
这项检查输出 `local_release_repair_check.v1`，仅证明该次释放修复。后续音的公开控制失败
保留在整段策略检查中，不删除，也不反向抹去已经通过的局部检查。

`repair_case.status=local_repair_checked` 可在同类错误后的检索中使用，但不会创建
`active_bundle.json`。LLM收到局部检查范围和整段检查的限制；整段策略通过仍标为
`public_checks_passed`。明确指定候选源码及SHA256，可以做新seed的受控继承验证，
该运行标为显式实验输入，不伪称默认代码缓存命中。不能通过私有6/6成绩取得任一种资格。

策略在原生已经终止后合法提交时，适配器记录 `terminal_before_finalize=true`。
完整提交链承认这条宿主记录，但仍保留 `release_verified=false`，不把终止当作释放
或持音验证。旧记录不补造这一字段。

继续对照在 `continuation_20261028T024645Z/`：3306无历史与有历史都从相同V0开始，
同模型、配置和一次修复预算；有历史只接入此前3300公开经历的冻结快照。此前的失败
状态不覆盖，重新核验以新索引记录追加，来源清单和校验值单独保存。3301仅继承有历史组
通过局部检查的修正版，初始生成与修复API预算均为0。该轮相关CPU测试140项通过。


## 2026-10-28：公开失败触发与宽松模块修复

2026-10-28已暂停的诊断曾使用 public_contract 完整模块修复，max_regenerations=6；旧区域修复和旧实验的一次预算保留在历史配置中。删除正常实验中按两函数范围/保护变量AST写法拒绝补丁的限制，保留语法、run_piano入口、公开接口与数据隔离检查。允许修改入口调度与辅助函数，原生任务、力估计器及动作/释放硬边界不变。

PIANO_NOTE 支持字典和旧 key=value 格式；abort_reason（包括 press_budget）、无有效接触、保持未确认会在下一动作前触发可恢复中断。正常返回但公开尝试链未完成也会触发。报告只保留约定公开字段，原生评分不会触发修复。未知原生失败且没有公开错误仍不能声称会被发现。

每次修复独立保存目录与执行边界。后一个补丁的检查不会读到前一个补丁的异常或后续提交；已完成音依宿主账本保留。补丁未经执行验证不晋升，API失败或非法补丁不覆盖有效版本。每次300秒/8192输出token；原生终止、全部尝试已经提交和预算耗尽是不能继续修复的边界。

154项CPU测试通过，覆盖连续修复、旧报告即时中断、正常返回但未完成、已提交音不重弹、源码修改与私有字段隔离。旧测试中对随机校验哈希搜索数字987的断言改为检查实际字段，避免把哈希巧合误报为真值泄漏。

首条GPU诊断暴露的0/1/文本保持报告已兼容；unknown保留为未确认，不当作合格保持。静态dict(...)参数构造允许，引用观测/动作或被重绑定的构造函数仍禁止在模块加载时调用。此前被拒的完整第三份实际API补丁重新静态校验通过。

用户确认后续每seed最多一次修复，当前仓库YAML和空记忆准备配置均恢复max_regenerations=1；首次生成加修复最多两份策略模块。已暂停的六次修复实验配置和结果原样保留。局部经验验收和触觉失败窗口仍待修改，本次仅调整预算，未启动实验。

### 2026-10-28：经验草稿格式与宿主数值来源绑定

修复代码的可执行检查与经验草稿的来源检查独立。字符串条件自动转为
`applicable_conditions.description`；无法在修正版中找到的可选代码摘录忽略并记警告。
原始 API 提案保持原样，规范化记录保存在 `normalization_warnings`。

宿主可绑定同一函数内唯一的整数动作循环上限修改（`while counter < N` 或
`for ... in range(N)`）：旧函数 AST、旧数值、错误前实际执行的循环行构成
`before_source`，新函数 AST 与循环行构成待验证 `after_source`。修复后仅当
该行实际执行，且对应的宿主公开检查通过，才记录已验证的本次条件。
这证明配置值被执行，不意味着实际执行了上限所示的全部动作数；不自动扩展到
表达式、歧义常数或未执行分支。LLM 不需要填写这些哈希或执行出处。

无法确认的旧值置为 null，并将原声明保留在 `unverified_before_claim`，经验继续
保存为待验证；它不能获得检索推荐资格。私有字段、固定要求覆盖、异常单位等仍拒绝。
`approach_timeout` 关联既有 `xy_arrival` 公开检查；后续其他控制错误独立记录，
不抹去已经通过的接近修正。完整代码的独立 5/6 复用资格不变。

本次按用户要求不执行 CPU 测试，仅启动一条 GPU 0、seed 3300 的空库 smoke，
通过现有 `./run_responses`，每 episode 仍至多一次修复；旧拒绝记录不重新晋升。

### 2026-10-29：简化门槛后的 GPU 验证

仅跑一条空库seed3300；补丁合法并执行，保存 `executed_failed` 且 `skill_searchable=true`。
这验证了失败的实际修改不会因数字溯源或局部验证失败被排除。原生3/6、公开提交2、无END，
未晋升完整程序；本条没有历史命中，不证明修复或跨轮复用收益。代码对第三音保持过于保守，
其原生计分已经完成但公开判断仍停止；LLM修复解释也与原源码不符。详细数值、时序及视频
见[实验账本](opentac-shadow-piano-experiment-ledger.md)和[运行结果](/mnt/data/ljz/opentacbench/capx-runs/simplified_memory_seed3300_smoke_rep1_20261029T023007Z/piano_independent_memory_result.json)。


## 2026-10-29：数值窗口与记录器删减

当前独立代码/技能库流程删除了逐动作重写 manifest、逐帧 NPZ、samples.jsonl、逐行执行位置、函数局部变量和重复的 public_outcome/调用索引。旧的图像窗口读取、整库轨迹重新编码入口也已删除；最终附件不再重复保存函数源码。正常执行中按阶段自动检索和表示建库的旧路径也已删除；技能查询只由可修复错误触发。

触觉 key 改用 window4：16 帧，每帧为 mean_mm、area_gt_0p01mm、area_gt_0p05mm、area_gt_0p2mm。来自现有公开特征提取；有效无接触与缺失特征分别处理，力估计 OOD 不自动使这些特征失效。不加载时序神经网络权重、不缓存或落盘 RGB/depth；5D 即时力估计器保留。按物理时间从错误前 0.5 秒因果选择 16 个 30 Hz 样本，按冻结 normalization.json 的均值/标准差标准化，时间展开为 64D 并 L2 归一化，沿用 RMS 向量距离。预处理版本独立，旧 tactile_action key 不混用。

轻量数值和公开调用记录暂存在本轮内存；修复直接读取，不写入/读回完整 manifest。查询选中的 16 帧数值及校准合同成为技能附件，实验结束一次保存；其他时刻不保存全量触觉历史。已有普通实验日志、视频和私有真实力审计保持原样。完整代码的五音门槛仍使用本轮内存中完整的轻量采样元数据判定，不因最终只保存错误窗口而改变。

宿主写文件时暂停 Python 调用追踪，完成或异常后恢复。代码结束与技能验证结果仍分别保存；没有额外 LLM 整理调用、后台线程或新增运行脚本。强杀会丢失尚未落盘的数值窗口。未启动新的 GPU 实验，端到端提速尚待 smoke 确认。

## 2026-10-29：固定错误的触觉证据对照

`CAPX_SHADOW_EVIDENCE_ABLATION=off|capture|A|B|C` 默认关闭，沿用 evidence_memory YAML 和 `./run_responses`。
通过 `CAPX_SHADOW_INITIAL_SOURCE` 与 SHA256 固定 V0；`CAPX_SHADOW_EVIDENCE_FIXTURE` 指定独立数据盘冻结文件。
capture 不调用 LLM，复现接触后的 OOD 后，保存同 episode、首次异常前 0.5 秒的 16 个 30 Hz 数值观测。
首次异常与策略稍后报错分别记录；不足历史、不复现或原生已终止则停止，不插值、不换案例。

A 只提供完整源码、任务/API、公开错误和动作/实际状态；B 增加最近有效 5D、估计力、质量与年龄；
C 再增加同一窗口的 4D、估计力与质量。B 的 5D 本身含面积变化率；C 对 B 测显式历史窗口的增益。
三组修复正文的共同字段一致，最多 24 KiB，不删中间样本。私有标签不进入请求，历史检索、技能更新和代码晋升均关闭。

三组统一 gpt-6.1-sol/low，单次请求上限 300 秒和 8192 输出 token，无代理、无传输重试。
返回完整模块并保留 FORCE_SETTINGS/HOVER_Z/ACTION_SECONDS；合法补丁从 reset、空进度执行，
不从旧错误状态续接。每组仅一次补丁，无二次修复。语法拒绝、API 错误、公开检查和原生结果分开保存。
单例结果只能说明这一案例的可行性，不能证明普遍收益；减少动作必须结合完整音序和原生判分解释。

### 最多三轮完整源码修复

沿用同一 V0 和已有第一轮 A/B/C 结果，每组最多再生成两次完整程序，仍不检索技能、不读取其他组结果。
`CAPX_SHADOW_ABLATION_ROUND` / `CAPX_SHADOW_ABLATION_MAX_ROUNDS` 标识轮数（最多3）；
`CAPX_SHADOW_ABLATION_NEXT_FIXTURE` 可在本轮结束时冻结下一轮错误窗口，不增加模型调用。
每轮从 reset、空进度执行；请求保留当前完整源码作为参考，允许重写辅助函数和控制流。
固定目标、接口、动作频率和原生规则不变。API错误暂停；终止或无合格公开错误时不硬造下一轮。

首次单轮实验未保存补丁失败的数值窗口，因此为续跑分别重放原补丁补取公共证据，不调用API，也不替换原成绩。
`CAPX_SHADOW_ABLATION_CAPTURE_REASON=press_timeout` 固定此次重放的预期错误。
调用记录只增存已知公开失败的实际函数返回值类别，超时窗口锚定该返回时刻而非释放后的日志时刻；不保存任意函数返回数据。
后续轮次源码与错误已各自演化，这比较整个迭代流程；不能再声称各轮输入完全相同。

### 第四、第五轮：依据公开反馈重新生成完整程序

按后续用户要求，独立实验配置显式设置 `shadow_piano_ablation_regenerate: true`，允许累计最多五轮。
此模式每轮均以本组上一版完整源码、上一版提案说明和公开执行反馈为参考，重新生成完整模块；
正常 END 也允许下一轮生成。默认错误触发模式保持不变，不把正常 END 伪装成错误。
没有跨组记忆、额外技能整理或 episode 内修复。每次新程序从 reset 执行；原生结果仅用于私有结果审计。

公开反馈包含动作数、按动作推断的阶段计数、公开检查通过项、尝试提交及 END。
A 不提供数值触觉；B 增加选定时刻的 5D/估计力；C 再提供相同时间段的 16×4D 与估计力序列。
出现可定位错误时沿用原错误前窗口。没有可定位的非终止错误时，按预先固定的公开规则，
选择首个尝试最后一个 commanded press/hold 样本之前的 0.5 秒，不按私有成绩挑选音符。
窗口按 240 Hz 物理时间每八步取一点，缺失时停止，来源及规则随反馈保存。
上次提案的解释明确标为未验证假设；不能把它视为真实力诊断。

前两阶段没有为第三轮保留后续数值窗口，故本次每组先做一次零 API 原代码回放取证，
不替换历史成绩。第四轮结束即保留下轮反馈，避免额外回放。每轮请求仍限 300 秒/8192 输出 token，
正文 24 KiB、完整源码单份；API 错误暂停，不自动重试。全部沿用 `./run_responses`，不新增运行脚本。
因此第四、第五轮属于“执行反馈驱动的完整代码再生成”，与前三轮“公开错误触发”条件不同，报告须区分。

## 2026-10-30：空记忆 A/B 连续迭代模式

配置为 `env_configs/opentac/play_piano_shadow_digit_continual_a.yaml` 与 `_b.yaml`，
仍通过 t-cap 的 `./run_responses` 启动，不新增运行脚本。
`CAPX_SHADOW_CONTINUAL_ROOT` 指定新数据目录，`CAPX_SHADOW_CONTINUAL_ROUND=0..9`，
`CAPX_SHADOW_TASK_SEED=3300..3309`，实际 reset seed 必须等于 3300 加轮次。

A 的第零轮从空源码、空经验请求一次全新完整程序，B 的第零轮读取同一冻结源码。
以后分别读取本组上一轮的 `result.json.next`，不加载历史 V0、不读另一组目录，也不按原生成绩挑版本。
策略每轮由宿主以新 request 和新进度调用。错误路径不调用修复；第零至第八轮在 episode 结束时各更新一次，
包括正常 END，第九轮仅保存反馈。最多一次首次生成和十八次更新，模型固定 `gpt-6.1-sol/low`，
单次 300 秒、8192 输出 token，清除代理且禁用自动 API 重试。

目录中的 `shared/generation` 保存首次请求与提案，`shared/bootstrap.py` 保存通过检查的共同源码；
`arms/{A,B}/code/<hash>/policy.py` 和 `experience/<hash>.json` 独立保存候选及经验，
`rounds/00..09` 保存实际加载记录、公开反馈、更新请求、响应、差异及拒绝原因。
所有候选显式标记 `candidate_for_experiment`；实验不更新或读取合格 active 指针。
既有代码库的原生至少五音及公开执行门槛保持不变，不能因本实验候选可执行就称其满足直接复用资格。

更新输入的完整源码仅一份。A 的公开材料逐字段构造，仅含任务、分类错误、动作/实际状态、
公开保持与释放报告以及本组经验，不读取任意 stdout 或私有文件。
B 增加每次尝试的末次测力前 0.5 秒、16 点、30 Hz 估计力窗口，以及该尝试全部测力读取的算术摘要。
保留质量、年龄和校准版本；没有原始 4D/5D 特征或时序编码器。若策略在释放时读力，窗口如实包含该阶段。
窗口按真实物理 step 精确匹配，不插值、不跨尝试，缺失值为 null。实际状态单位明确为 mm/mrad/mm/s，
目标仍为 m/rad。原生真值和得分仅在结束后独立生成私有报告，更新器不读取。

经验最多 16 条、8 KiB，记录条件、建议、observed/hypothesis 及可选数值与公开出处。
宿主保存上一建议执行后的公开反馈，不以模型的“已改进”声明证明因果。
24 KiB 请求正文上限不静默截断；超限暂停。源码拒绝时下轮沿用本组上一可执行版本，
经验拒绝独立回退旧经验。首份程序被拒绝时没有可回退版本，整个批次暂停，不自动再生成。
每个请求先独占保存调用标记；重新运行同一目录不会补发首次请求。

本轮启动目录：`/mnt/data/ljz/opentacbench/piano_continual_ab10_20261030T173837Z`。
首次请求 HTTP 200，43.28 秒，供应商报告 10,923 token；源码自行使用 `getattr`/`globals` 动态查找 API，
被既有公开接口校验拒绝，未执行按键。A3300 进程退出 1，后续没有运行，不能算已执行的 0/6 样本。
已补清“API 直接绑定为全局函数、piano_state 只是字典”的提示，未修改已返回程序、未放宽校验或重跑。
四项针对性 CPU 检查通过；完整 GPU 跨轮更新、下一 seed 继承及十轮曲线尚未验证。

用户另行授权一次初始生成后，实验在
`/mnt/data/ljz/opentacbench/piano_continual_ab10_20261030T175939Z` 启动。
3300 的 A/B 共用 `40a7d39f…`，均为原生 4/6、六次尝试、END、591 个生成动作；
A 产出 `e9f31412…` 和三条经验，B 产出 `22b82a37…` 和四条经验。
这些经验尚未在下一 seed 验证，不能称为已有提升。

B 的正文原为 26,264 字节，按 24 KiB 上限在发送前暂停。
仅将 B 的力窗口、校准信息和摘要列名改为列式格式，逐值往返检查通过；
A 的共同字段与原请求一致，B 的力、质量、年龄、版本、时间点和摘要全部保留。
正文降到 24,562 字节后，调用既有更新函数完成那次尚未发出的请求，没有重跑 B 仿真或增加更新次数。
固定系统提示不计入这项正文上限，实际 HTTP 载荷更大。
两组首轮及更新已核对，3301–3309 继续用 `./run_responses` 串行运行；API 错误和后续正文超限仍暂停。

B/3302 已完成仿真，但轮末更新正文为 25,384 字节，在发送前暂停。
`piano_continual_public_feedback.v2` 对两组的动作目标按坐标列保存：
16 点内恒定的坐标只写一个数，变化坐标保留全部 16 个数；缺帧索引独立保留。
实际状态、力窗口和摘要不变。六份已完成反馈都能逐值还原，未删除样本或减少精度。
B/3302 正文因此降到 24,087 字节；系统提示与源码保持原样，本轮不接 SkillOpt。
每次更新在发送前保存 `update/request_size.json`，分别记录正文、系统提示及各部分 UTF-8 大小。
若再次超过正文上限，不创建 API 调用标记、不发请求、不截断数据；保留已完成仿真，
暂停等待调整表示或预算后补做尚未发送的更新。

B/3303 原生 6/6、END，轮末正文 25,070 字节，尚未发出请求。
发送格式 v3 将六音公开报告的重复字段名统一放入 `columns`/`report_columns`，
对应行完整保留，磁盘上的可读反馈不变。正文为 24,310 字节；七份历史反馈
逐值还原验证通过。该格式同时用于 A/B，不删除错误、OOD 或失败报告，不按原生分数裁剪证据。

用户随后指定超限回退门槛：5/6、6/6 为强代码；1/6–3/6 为弱代码。
A/B 配置共同启用 `shadow_piano_continual_overflow_policy: retain5_regenerate1to3.v1`。
仅在无损压缩后正文仍超过 24 KiB 时，宿主检查本次六音原生计数与 release 事件一致性：

- 5/6 或 6/6 且已 END：保留完整源码，合并重复错误文本并保留原顺序；不同错误和触觉样本不删除。
- 1/6–3/6：源码继续存盘，但本次请求不再带旧源码；携带不变的冻结常量、任务/API、
  本组经验及公开反馈，要求重新生成完整程序。更新次数、时限和输出预算不增加。
- 4/6、0/6、审计不一致或证据缺失：保留源码，仍超限则暂停；不猜测强弱。

此规则不回溯应用到已完成更新。从 A/3303 开始启用，不重跑已完成的七次仿真。
它修改了原先“完全不按原生分数决定更新方式”的实验约定；两组采用同一门槛。
分数与宿主决策留在私有 `shadow_continual_source_policy.jsonl`，不进入经验、公开反馈或模型请求。
`update/request_size.json` 及轮次结果记录实际是否携带源码；候选不合格时仍按既有规则回退上一可执行版本。
完整代码库的独立 5/6 晋升规则不变。强代码及必要反馈本身仍放不下时继续暂停，不静默删帧或加大预算。

B/3304 为原生 6/6、END，宿主正确选择保留强代码，但正文仍超限 266 字节。
发送格式 v4 对动作窗口同样使用共享 `columns` 和完整 `rows`，省去重复键名。
正文由 24,842 降至 24,453 字节，十份已有反馈均可完整还原，源码和数据数值不变。
仅补做该轮尚未发送的更新，未重跑仿真；后续 A/B 使用相同表示。

### 2026-10-31：移除初始手工控制策略的小规模试验

两份 continual YAML 采用 `shadow_piano_continual_prior: task_api_only.v1`。
任务说明和 API 契约保留；去掉四组专家 probe 补偿、固定 96 步释放预留、
逐音预算配方及手工下压/反馈/等待流程。冻结常量不再重新注入这些策略。
`FORCE_SETTINGS` 在本配置中只有目标力、容差和原有公开保持门槛三项。
保持门槛是既有 CaP-X 公开检查要求，不能将其全部表述为原生任务阈值。
实际 XY 拦截、释放保护、2000 动作上限、4 物理步动作间隔及冻结测力器不变。

新目录 `/mnt/data/ljz/opentacbench/piano_continual_task_api_pilot_20261031T034400Z`
从空代码和经验开始，只沿用公开键盘标定。设置
`CAPX_SHADOW_CONTINUAL_ROUNDS=2`，顺序为 A3300、B3300、B3301、A3301：
第一对共用一次新生成的程序，各自更新一次，再验证本组下一版。
最后一轮不再生成未经执行的新版本；默认未设置 rounds 时仍为十轮。
本试验最多四次仿真，不导入原十组经验。配置原文、提示词及实现快照保存在数据目录。
必要 CPU 检查三项通过；真实执行结果随后记录，不能由提示词变短推断记忆有效。

四次试验于 04:20:32 UTC 完成，首轮共享源码哈希 `81846f5f`；下一轮 A/B
分别执行自己的更新 `660859dc` / `c933ad49`，没有源码回退或额外生成。
3300 A/B 均为原生 0/6、1487 动作；3301 A 为 0/6、1395 动作，B 为 2/6、1512 动作。
每条均尝试六音并提交 END；共一次初始生成、两次更新，三项 CPU 检查通过。
完整日志、两个视角视频、反馈、私有审计见数据目录 `results.md`、`pilot_audit.json`。

此次 B 的两音为 C#4 / F#4 重音；其修改把原代码自行设置的弯指上限由 0.35
放宽至接口上限 0.50 rad，每次增量保持 0.005 rad。A 在 0.35 以上改用 0.002 rad。
实际两音最大命令分别为 A 0.368、B 0.375 rad，并未运行到上限。
B 的这两次尝试末段公开报告 OOD，但原生已计分；A 公开保持通过仍无原生完整音符。
不能把这一个样例解释为可靠的触觉学习收益。

私有审计发现新动作姿态下明显的估计偏差：3300 白键 quality=ok 样本的 MAE
为 0.392 N（估计范围 0.223–0.495 N，实测 0.014–0.057 N）；3301 该误差
A/B 分别约 0.404 / 0.431 N。现有特征范围检查不能证明这些新姿态下的估计准确。
模型和任务保持不变，真值未发送给更新器；本轮停止于四次试验，不自动扩大。

另记录旧经验校验器的误拒绝：A 草稿提及“estimated_force_history 未提供”，
字符串黑名单将其当作数值触觉泄漏。A 新源码正常执行，经验仍沿用空版本；
B 三条经验保存成功。拒绝记录与原提案均保留，没有人工修写提案。
本试验最后一轮不再更新，故该经验拒绝未改变 A 已生成的执行源码，但未验证完整的多轮经验积累。

### 2026-10-31：轮末最终结果反馈，A 严格隔离触觉数值

用户调整了原先“判分只作私有评估”的约定：整轮结束后，两组可以知道各音是否通过，
但必须自行从本组日志和允许使用的反馈推断原因，并调整下一轮完整程序。
两份 continual YAML 启用 `shadow_piano_continual_final_results: true`；其他配置默认关闭。
兼容签名记录 `piano_final_note_results.v1`，新实验使用新数据目录，旧结果不追溯改写。

宿主在轮末读取原生 release/miss 事件，仅导出以下白名单：

```json
{"schema":"piano_final_note_results.v1","available":true,
 "notes":[{"index":0,"outcome":"failed"},{"index":1,"outcome":"passed"}]}
```

index 对应 request 音序；缺少已结算结果的音为 `unresolved`，不能补成 failed。
执行中读取返回不可用；该方法不是生成代码的公开 API。原始事件、物理时间、真实力、
琴键角度和原生失败原因不进入更新请求。只有完成已结算结果的一致性检查才导出结果。

两组均得到最终结果、当前源码、动作/实际位姿及非数值公开错误；B 额外得到估计力历史、
质量及算术摘要。A 的两个数值触觉通道必须明确 withheld，发送前检查会拒绝这些通道
或公共反馈中新增的触觉数值/图像字段。A 不获得触觉图像、4D/5D 特征、估计力数值或曲线。
任务规定的目标力和控制参数允许保留，它们不代表实测触觉。运行时 A/B 仍调用相同测力 API：
本实验隔离的是更新模型收到的证据，未同时改变在线控制能力。

提示词要求区分最终任务结果、公开保持信心和失败原因假设；失败标签不能证明测力失准，
也不能据此编造真值。探针和控制调整由模型提出，再用下一轮执行验证，不注入人工诊断答案。
五项针对性检查通过；上一轮四条日志离线导出的逐音结果与私有审计一致，未启动 GPU 或 API。
核验材料：`/mnt/data/ljz/opentacbench/validation/terminal_note_feedback_replay_20261031/`。

### 2026-11-01：严格触觉更新证据对照

`shadow_piano_continual_feedback_contract: strict_tactile_ablation.v1` 将过去接触产生的
数值、质量、接触状态和保持判断统一视为触觉证据；它们总结成文字后仍属于触觉派生经验。
即时控制、固定目标与共同专家探针先验保持不变，本对照检验跨轮更新使用历史触觉的作用。

A 更新不接收逐音 `report`（包括 quality、had_valid_contact、public_hold_confidence、
abort_reason），也不接收力历史和摘要。任意程序停止/异常文本在两组的公共反馈中只保留
通用类别，避免错误消息绕过隔离。动作、实测姿态、动作数、姿态释放检查与最终逐音结果
仍提供；语言经验仅作格式和大小检查，不否决模型自行推断的假设。

两组的历史窗口统一由最后一个弯指且低位的动作目标选择，没有此动作则取末次动作；
不再依赖最后测力时间。B 另外接收逐音触觉报告、估计力、质量和算术摘要。
动作与最终结果可能间接体现接触，这是“无显式触觉证据更新”，不声称完全隔绝触觉的因果影响。

本次 seed 3300、3301，共四次串行仿真：一份新生成程序共享，两份空经验库；3300 后各更新
一次，3301 执行各自候选，最后不再更新。旧代码和旧经验不导入。兼容签名包含证据契约。
三项针对性 CPU 检查通过，另以真实旧日志验证 A 的隔离和两组相同的动作窗口。
实验目录：`/mnt/data/ljz/opentacbench/piano_strict_tactile_memory_pair2_20261101T095842Z/`。
启动结果：Responses 预检返回 completed 但 output_items=0、没有可见正文，启动器退出码1，
批次自动暂停；未生成初始程序、未执行仿真、未调用更新器，不计为 A 的任务失败。没有自动重试。

随后用户授权自动重试。`run_responses` 默认设置 `CAPX_LLM_REQUEST_RETRIES=2`、
`CAPX_LLM_RETRY_BACKOFF_SECONDS=30`，预检及代码记忆的生成/更新请求共享退避逻辑。
空 completed 响应、连接/请求超时、408/429/5xx 可重试；401/403/参数错误、拒绝响应、
补丁校验和策略执行失败不重试。每次最多三次请求、间隔30/60秒，各次使用独立请求截止时间。
只在耗尽请求重试后设置批次 API abort；不重新执行仿真，逐次尝试写入 API 事件日志，
成功更新记录 api_calls 与 retry_wait_seconds。存在服务商用量的空响应也保留用量事件。
严格对照已恢复，原预检失败记录保存在实验目录的 maintenance 下。

完成后有效性审计：四次仿真均结束并提交 END；3300 A/B 均0/6、514动作，3301 A为3/6、
1421动作，B为2/6、1972动作。**这轮不满足严格消融条件**：`_load_config` 漏传新证据契约，
实际 A 更新仍携带 quality/contact/hold/abort 报告。该遗漏已修复并增加真实启动配置路径的
回归检查。另，首轮六次均在接近阶段停止，B 没有按压测力历史可学习，因此也没有检验到
有效接触历史的增益。全部原始请求和结果保留，`validity_audit.json` 记录问题；没有追加仿真。
