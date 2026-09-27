# 轻量多人认证与照片自动校验实施计划

> 执行方式：superpowers:executing-plans；用户已明确要求按对话方案改造并部署，连续执行，不重复请求方案授权。

**Goal:** 人物选择一行展示，认证记录独立可见，新照片自动官方校验，提交视频不阻塞编辑。
**Architecture:** 复用 production.db、现有认证会话、TOS 和后台队列。新增人物和照片校验记录；人物按账号/项目隔离；照片按人物+内容指纹去重。一个专用后台线程处理图片入库，视频任务等待时让出工作线程。
**Tech Stack:** FastAPI / SQLite / 原生 JavaScript / Pillow / TOS SDK。
**Spec:** 本对话已批准设计：一个人物自动选、多个人物记住选择、后台校验、不可用不得调用模型、任务冻结所选人物。

## Constraints
- Git 基线 c057498。只部署 /opt/ark-video-workflow，不修改原 director-prompt-h5 或 Nginx。
- 不上传密钥到 Git；不生成额外付费视频来测试。
- 官方校验结果是唯一可用依据；CreateAsset 超时不得盲目重发，按确定的名称查询恢复。
- 现有原图导入、普通参考图和其他服务商兼容保留。

## Tasks
- [x] 人物列表和改名：新增 app/portrait_library.py，人物从已验证 sessions 迁移；GET /people 仅本地读取，POST /people/sync 才拉官方组；PUT /people/{id} 保存备注。测试空组可见、配置隔离、名称验证。
- [x] 图片校验：POST /photos {person_id,asset_id} 返回后台 job；GET /photos?ids= 批量查询。校验实际图片格式尺寸；去重和限流；队列处理上传、CreateAsset、GetAsset。测试同照片同人复用、不同人隔离、FaceMismatch、重启、超时不重复创建、敏感字段不出响应。
- [x] 视频衔接：draft.person_id 保存，prepare 保存不可变照片 job；后台未就绪任务让出队列，Active 后重新核对官方素材，只有 asset URI 入模型；测试切人不改变历史任务、失败不调用模型。
- [x] 轻量 UI：人物行、下拉与搜索、改名、添加认证；上传本地预览、单一批量状态轮询、认证完成更新人物、保存和恢复选择；桌面/手机浏览器验证。
- [ ] 全套测试、独立审查、Git 完成提交；发布独立 release、SQLite 备份、切换 current；线上只读验收、新旧站点与服务比对。

## Review focus
1. 人物切换途中旧上传响应不能覆盖新选择。
2. 服务重启/网络异常不得重复提交创建素材或视频。
3. 已撤销/跨账号或项目的人物和素材不能作为有效授权。
4. 等待校验不占住两个视频 worker，不循环忙等。
5. 没有图片的人物仍可见，失败理由与恢复操作可理解。

## Ledger
- Baseline committed: c057498, excludes runtime data and temporary exported packages.
- Ruling: retain current workspace after baseline rather than copy ignored runtime dependencies; no concurrent implementers.

- Backend integration58passed; full suite298passed before extended regressions.
- Desktop/mobile browser tests: multi-person selection, rename, restored selection, immutable task input, removed slow upload discarded, explicit ordinary-mode persists.
- Independent review: fixed uncertain account-switch reset, stale file upload enqueue, missing ordinary mode.
- Official references: https://docs.byteplus.com/en/docs/ModelArk/2318271 and https://docs.volcengine.com/docs/ark/get-asset-api?lang=zh . Actual likeness verification remains provider-controlled.

- Focused reviewer recheck: all three findings resolved.
- Concurrent legacy import test briefly hit its 5-second barrier on Windows; diagnostic now collects both future exceptions, with no relaxed assertions. Five isolated repetitions passed; full release suite rerun required before publish.
