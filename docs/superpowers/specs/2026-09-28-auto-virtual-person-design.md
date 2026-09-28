# 默认上传虚拟人自动入库并生成：现有代码兼容方案

日期：2026-09-28
状态：用户已批准并于 2026-09-28 执行，后续授权合并主目录并部署；实现及验证情况见 `../../auto-virtual-person.md`。
范围：主应用 `app/`、对应 `tests/`。未跟踪的独立导出模块 `video-production-module/` 本次不改。

## 1. 目标与交互约定

普通用户使用虚拟人物时：上传人物参考素材，补齐动作视频、衣服和提示词，点击一次“生成视频”；后台自动创建或复用虚拟人物、上传官方素材、等待可用，再进入现有生成流程。无需先去后台新增人物、选照片或扫码。

本方案将“直接上传就能生成”解释为去掉人物准备阶段的额外点击，保留现有的一次生成提交。选择文件、替换参考图、恢复草稿本身不自动提交付费视频生成。

保留两个辅助入口：“从人物库选择”和“真人授权”。已认证真人的照片继续复用原有一致性校验；新真人仍通过官方认证。新草稿默认的虚拟人模式不等于所有传入照片都已被认定为虚拟人。

## 2. 当前实现与实际缺口

| 位置 | 已有能力 | 本次接缝 |
| --- | --- | --- |
| app/main.py:281 | 首页、/v1、/studio 使用 production.html | 修改主制作页面，不改旧工作流来实现默认入口 |
| app/production_router.py:110 | POST /api/production/assets 保存本地素材 | 继续复用，不在文件上传请求内调用远程建组 |
| app/production_router.py:210 | POST /api/production/runs；244–248 行调用 prepare 后才 create_run | 自动模式应先冻结任务，再执行远程副作用 |
| app/portrait_generation.py:14 | 有 person_id 时自动 enqueue；无 person_id 时查素材绑定 | 无人物、无绑定时返回 None，缺自动创建人物 |
| app/portrait_library.py:97 | create_virtual 创建 AIGC 组，request_id 幂等 | 可复用；补组创建结果的恢复与关联 |
| app/portrait_library.py:297 | enqueue 按 account/person/hash 复用照片任务 | 增加自动模式的跨人物精确内容查找 |
| app/portrait_library.py:330 | 后台上传、CreateAsset、查询 Active | 复用图片/人物视频分支与官方校验 |
| app/production_worker.py:49 | authorizing 阶段捕获 PortraitPending 并放回队列 | 前面增加自动人物解析与准备记录 |
| app/production_store.py:190 | 同事务检查版本、幂等并冻结快照 | 同事务保存自动模式的准备意图 |
| app/static/portrait-people.js:102 | 无明确 person_id 且只有一人时自动选中 | 自动模式禁用该默认选择 |
| app/static/production.js:625 | 选择人物后上传自动排照片检查 | 自动模式无选中人物也应可提交主任务 |
| app/static/production.js:61 | 人物视频目前要求先选人物，再进素材面板 | 如支持视频自动入口，需增加直传控件和相应校验 |

已存在的真人认证、照片检查与视频生成是不同环节。无需合并成一个“是否验证”布尔值。

## 3. 路由策略：新默认与旧输入并存

新增草稿字段 `person_input_policy`，与 `person_reference_mode=image|video` 独立：

| 值 | 用途 | 行为 |
| --- | --- | --- |
| auto_virtual | 新版制作页的新草稿默认 | 未选人物时自动解析/建立 AIGC 人物、入库后生成 |
| existing_person | 用户明确选用已有真人或虚拟人物 | 从服务端人物记录读取 LivenessFace/AIGC，沿用原链路 |
| legacy_raw | 旧普通图片或显式兼容接口模式 | 保留旧普通图片提交语义；已有官方绑定仍重新核实 |

规则：

1. 新版前端创建空白草稿时显式提交 auto_virtual；创建接口应接受并验证该字段。旧客户端不传字段时保留旧语义，避免悄然改变 API。
2. 旧草稿缺字段：有 person_id 或有效官方绑定按已有素材处理；其余按旧普通图片处理。不要迁移已有 run 的不可变快照。
3. 旧客户端仅更新旧字段时不强行覆盖当前策略；旧客户端明确选择 person_id 时，按已有人物语义规范化。新客户端显式发送的策略与人物类型冲突时返回明确校验错误。
4. 选用库中人物进入 existing_person；库中真人绝不自动改为 AIGC。person_type 由服务端记录决定，不能信任客户端传值。
5. auto_virtual 不得接收已明确绑定到真人的素材后重新归类为 AIGC。检测到这种冲突时保留素材，要求切换已有真人入口；不弹新认证，已有认证继续复用。
6. “人物参考来源策略”和“图片/视频格式模式”分别保存、恢复和冻结。图片保持现有数量规则；视频保持当前官方接口、模型、时长、尺寸等校验。
7. 官方人物素材目前只适用于代码允许的火山官方接口。自动模式选了不兼容的服务商时，应在建组前提示模型配置问题；不能静默退回原图。
8. 本次不改变 /api/jobs 与旧 workflow API 的协议，也不将相应历史任务迁成新模式。

前端新草稿不默认选中库内唯一真人；旧草稿的明确人物选择必须恢复。用户实际完成真人认证后的关联仍保留。

## 4. 后台执行顺序与持久化

推荐复用现有 production run，增加人物准备子状态；不新增一整套对用户可见的生成任务系统。

```text
本地上传并保存素材
  → 保存草稿
  → 一次提交生成
  → 本地预校验 + 同事务冻结输入/准备意图
  → 人物解析或创建 AIGC 组
  → 复用 enqueue 上传人物素材
  → 官方素材 Active
  → verify 再查账号、项目、类型、素材有效性
  → 现有打码 / 上传动作视频 / 模型生成 / 下载
```

默认先把整条人物准备链放在现有 authorizing 阶段，不额外并行打码；这样保留原阶段顺序和资源控制。

### 4.1 创建任务前

校验策略、素材存在和类型、图片/视频有效性、参考图上限、配置就绪及不兼容绑定。只做读取/本地校验，不调用 CreateAssetGroup 或 CreateAsset。

新自动路径从已保存草稿读取输入，在 create_run 的事务中复查 revision 与幂等键，并保存准备意图。同一幂等提交只返回同一个 run。版本冲突不应产生远程人物或照片。

现有 selected-person 路径可逐步迁入相同准备服务；第一阶段保持其外部行为，并用旧回归测试锁定。不要为了全量重构延迟自动路径交付。

### 4.2 运行时记录

建议新增轻量表 `production_person_preparations`，以 `run_id` 为主键，存放：

- state、当前账号/项目指纹、输入内容摘要；
- 稳定的 group_request_id、解析后的 person_id；
- 本任务本地素材 ID 到照片入库任务 ID 的映射；
- next_check_at、started_at、deadline_at；
- error_kind、message、updated_at。

生成配置与凭据继续放现有 private 快照，不放公开准备状态。准备服务读取冻结快照，而不是重新读取用户正在编辑的草稿。

表中的 person_id 是运行时解析结果，不回写旧 run.snapshot。任务查询按白名单公开准备进度和解析后的人物摘要，不暴露凭据、签名链接、私有路径。

队列沿用 queued / running / failed / needs_attention 等顶层状态及 authorizing 阶段。准备中的任务设置下一次检查时间，释放工作线程；claim_next 同时考虑 next_check_at，保持现有约 10 秒的等待退避，不能紧密抢占队列。

### 4.3 最小职责划分

新增 `app/person_preparation.py`，集中处理策略解析、自动人物匹配、组准备、照片映射及结果检查。它调用现有 PortraitLibrary，不重复实现 TOS 或官方客户端。

现有 portrait_generation.verify 继续负责生成前的官方复核与 asset:// 映射。对新准备结果构造它能消费的 portrait snapshot；旧 private.portrait 继续可读。

## 5. 自动创建与复用规则

自动复用限定在当前项目与账号指纹范围内，并用文件 sha256 + 素材 kind 精确匹配；不做相似人脸识别，也不因名字相同合并人物。

查询需联接 portrait_photos、production_assets、portrait_people，并考虑 hidden 状态。不能只查 production_portraits：普通照片队列达到 active 时未必会写该绑定表。

- 找到唯一、可见、类型为 AIGC 的人物关联：复用人物，现有待处理/已可用照片复用原任务，执行前仍做官方复核。
- 找到冲突绑定、多个不同人物归属，或仅找到用户已移除人物：返回可解释的待选择状态，保留输入；不静默合并、恢复或另建重复人物。
- 没有匹配：创建新的 AIGC 人物。同一批“人物参考图”作为同一角色的一组参考；以主参考内容形成创建键，补充图顺序不改变主角色。如果素材已有不同人物的明确绑定，则阻止混用。
- 稳定创建键由账号范围、素材格式和主参考完整 hash 计算，不使用每次重试的新 UUID，也不只用文件名。不同 run 提交相同主参考可复用同一个建组请求。
- 自动云端组名包含稳定创建键，长度遵守现有 60 字符限制；本地显示名可用“虚拟人物 + 简短编号”，后续后台可改名。本地改名不得改变重试请求身份。

当前 create_virtual 把大多数异常保留为 uncertain，且没有自动组对账。为减少用户干预，应小幅补齐：

1. 创建响应取得 group ID 后先持久化，再调用 GetAssetGroup 核实；避免“创建已成功、验证失败”丢失 ID。
2. 重启或超时后，有 ID 时查询原组；无 ID 时仅对自动生成的稳定云端组名做精确查询，核实类型/项目、唯一结果及本地请求对应关系。
3. 唯一可证明归属的结果回填 person_id；零个或多个结果保持 uncertain，绝不重新调用 CreateAssetGroup。
4. 保留旧手工建组的名称去重规则，不把任意同名人工组当成自动创建成功的证据。

## 6. 失败、重试、取消与恢复

| 情况 | 行为 |
| --- | --- |
| 人物素材仍在排队/上传/处理中 | 主任务显示“正在准备虚拟人物”，自动等待后续步骤 |
| 正常进程重启 | 依据准备记录和现有照片恢复逻辑继续，不重建人物/已提交素材 |
| 组或素材提交结果不确定 | 查询原请求；无法确认时进入 needs_attention，显示“入库结果待确认” |
| 官方明确拒绝素材 | 停止本次生成并展示原因；不改类型、不退回原图、不重复付费提交 |
| 临时查询/上传错误 | 对安全可重试步骤有限退避；带远程 ID 的只继续查原记录 |
| 配置账号/项目变更 | 阻止旧输入继续，提示重新选择/核实，不跨账号复用 |
| 超过准备等待期限 | 标记明确的准备超时并保留记录；重新检查恢复原准备任务 |
| 排队时取消 | 停止该视频任务后续调度；不删除已有云端人物和素材 |
| 视频模型已有 task_id | 沿用现有继续查询/下载，不重新入库或重新生成 |

第一版可沿用现有 30 分钟照片等待上限，但新主任务应以自己开始等待的时间计时，而非复用照片记录几天前的 created 时间。

增加专用的准备重试接口，例如 `POST /api/production/runs/{id}/person-preparation/retry`。它只允许无 provider_task_id 的可恢复准备错误；复用原 group_request_id、照片任务和冻结输入。旧 /resume 继续只负责云端查询/下载，不能借它直接重发生成。

准备状态与 run 状态的恢复/取消采用事务检查。等待期间 cancel 后，迟到回调不能重新排入生成。已发出的官方素材请求允许完成并保留结果。

## 7. 前端与历史兼容

制作页主入口显示“上传虚拟人物”，旁边提供“从人物库选择”；“真人授权”放人物来源的辅助选项。

上传立即预览并自动保存。首次创建官方组建议在点击生成后开始，避免用户试图换图时不断产生云端人物。

用户看到的阶段为：素材已保存 → 正在准备虚拟人物 → 正在生成视频 → 已完成。不要求等待人物库照片面板中的“使用这张”。

要同步改动：

- production.js：保存/恢复策略，自动模式下生成条件不要求 person_id；与 existing_person 上传检查逻辑分流；新增人物视频直传时保持所有现有限制。
- portrait-people.js：新草稿默认虚拟入口；仅 auto_virtual 禁止唯一人物自动选择；切换策略不应让旧异步上传回调把素材绑定到错误人物。
- portrait-photos.js、portrait.js：保留人物照片选择、真人认证及官方导入流程，补齐策略切换事件即可。
- production_panel.html：更新主提示，避免没有人物时默认显示“请先真人认证”。
- production-runs.js：展示准备中/待处理原因，以及专用的准备重试操作。
- virtual-library.js：自动产生的人物直接通过现有人物库查询显示，无需再点“同步”。

准备过程中改图、换草稿、切人物都不能影响已提交任务。前端沿用现有 draft/person 捕获检查，并加入策略/当前素材检查，丢弃迟到结果。

复制规则：复制草稿保留原策略；复制已生成或已成功解析人物的任务时，可将解析后人物写入“新草稿”的 existing_person 字段。旧任务快照保持不变。重新使用素材时继续官方核实。

人物“成功生成次数”和“最近成功记录”当前读取 snapshot.person_id。需要同时读取 preparation.person_id，仍排除 mock 成功；否则自动生成的视频不会计入人物库统计。

## 8. 文件改造清单

| 文件 | 计划修改 |
| --- | --- |
| app/person_preparation.py（新增） | 人物来源策略、自动解析、持久化准备推进、重试判断 |
| app/production_store.py | 准备表及原子创建/状态更新、排队退避、恢复和安全取消 |
| app/production_router.py | 策略字段验证、新前端创建参数、自动 run 入队、准备状态/重试、复制解析结果 |
| app/portrait_library.py | 精确 hash 复用、自动组对账、照片复用、人物生成统计兼容 |
| app/portrait_service.py | 建组响应 ID 及时持久化所需的客户端拆分/回调；受限的组查询 |
| app/portrait_generation.py | 复用新准备结果；保留旧 private.portrait 验证和绑定路径 |
| app/production_worker.py | authorizing 前置准备、释放线程、分类错误及取消检查 |
| app/static/production.js | 默认上传、策略保存恢复、图片/视频上传入口与异步隔离 |
| app/static/portrait-people.js | 新默认入口、选择策略事件、禁止自动选错真人 |
| app/static/production-runs.js | 准备阶段提示及专用重试 |
| app/templates/production_panel.html | 主入口与提示文案 |
| app/static/portrait-photos.js、portrait.js | 必要的已有入口策略联动；认证协议保留 |
| app/templates/production.html、studio.html | 按实际静态依赖更新资源版本 |

不新增通用工作流引擎，不引入新队列产品，不重写图片/视频供应商适配，不做真人相似度识别。

## 9. 实施顺序与验收

### 阶段 A：锁定旧语义与新数据模型

增加字段兼容解析及准备状态存储，验证旧草稿/已有绑定/真人授权保持原行为。先解决“不同策略怎样恢复”，再改默认 UI。

### 阶段 B：自动图片主链路

实现生成任务冻结后的后台解析/创建、enqueue、Active 等待及 verify 接续。补全幂等、组不确定恢复和准备重试。联通图片默认上传入口。

### 阶段 C：人物视频与完整交互

复用同一个准备服务接入人物视频直传，保持既有模型和媒体约束。补复制、统计、任务列表、改图竞态和桌面/手机交互。

### 阶段 D：回归与真实账号验收

核心自动化验收：
- 空人物库上传虚拟人，生成一次即可自动建组、入库并提交模型。
- 库中仅一个真人，新草稿仍不选中、不走真人校验。
- 已有真人及新真人认证流程均不受影响；虚拟人物不会显示 verified=true。
- 旧无 person_id 的普通图、旧官方绑定素材、旧 /api/jobs 和旧 workflow API 保持兼容。
- 同图重复/并发任务不重复建组、上传；有歧义和隐藏记录不会被自动猜测。
- revision 冲突无远程创建；提交、重启、超时后保持原请求身份。
- Active 之前不会调用视频模型；官方拒绝不会偷偷走原图。
- 任务创建后改图/换草稿不影响冻结输入；取消期间完成入库不会恢复已取消的视频任务。
- 图片/视频切换只消费当前模式；时长、格式、9 图上限等现有限制继续生效。
- 解析人物正确出现在任务详情、复制新草稿和非 mock 成功统计中。
- 1440/390 宽度下默认流程无额外人物库选择；已有照片与认证面板仍正常。

新增 tests/test_person_preparation.py；扩展 test_production_api/store/worker、test_virtual_library、test_portrait_library、test_person_video。浏览器增加默认自动上传用例，并回归 browser_portrait_people/photos/production_session。

应用测试明确使用 `.venv/Scripts/python.exe -m pytest tests`；独立导出副本具有自己的 conftest，不从仓库根目录无范围收集其测试。本轮没有运行测试，不引用历史测试数量作为本方案已通过的证据。

## 10. 已知真实接入限制与发布条件

docs/ecs-deployment.md:178–183 记录：2026-09-27 某 AIGC 组和图片已确认为 Active，但两次模型提交均报 asset not found；当时未完成真实出片验收。后续 2026-09-28 的人物库导入记录也明确没有执行新生成验收。

这证明自动化链路接通并不能单独证明当前模型配置能消费任意新建 AIGC 素材。本方案不推断根因，也不把旧失败当成当前必然失败。实施前核对已知账号/项目、模型配置与素材元数据；上线默认入口前，以当前配置完成一次明确授权的真实 AIGC 出片验收。mock 通过与 GetAsset=Active 均不能替代它。

当前检查为代码/文档只读审查与官方文档查阅；未读取私密配置、未查询密钥列表、未访问生产数据库、未创建官方素材或提交生成。

官方资料（2026-09-28 查阅）：
- 素材组区分 AIGC / LivenessFace：https://docs.volcengine.com/docs/ark/list-asset-groups-api?lang=zh
- 素材 Active / Processing / Failed 及项目字段：https://docs.volcengine.com/docs/ark/get-asset-api?lang=zh
- 真人认证和后续素材一致性检查：https://docs.volcengine.com/docs/ark/upload-real-person-portrait-assets?lang=zh
- API Key 项目权限范围：https://docs.volcengine.com/docs/ark/api-key?lang=zh

推荐采用“默认自动虚拟人 + 已有人物复用 + 真人认证兼容 + 旧普通图保留”的增量方案。比前端串行点接口更能覆盖刷新/重启与幂等，也比另建一套完整任务系统更贴合当前项目。
