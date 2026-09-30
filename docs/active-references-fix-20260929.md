# 可选素材检查修复

两条任务 `9b8c5dcaa60940c7bb9ef12993f98414`、`f5e09e01f9884de3bce258f56e4d4731` 的人物视频均已在 AIGC 库 Active，失败由启用的 `yoyo发型1.jpg` 触发：同内容真人照片被移除，提交前授权检查拒绝。旧代码还检查了未启用的可选素材及未采用的人物输入模式，且没有标明出错文件。

修复 `production_worker.authorize_run_inputs`：只检查实际参与本次请求的输入，遵循人物图片/视频模式与可选项开关；错误包含素材用途、文件名及处理方法。在预处理之前检查，并在模型提交之前再次检查，保留授权撤销保护与已有云端任务的续查逻辑。

本地、服务器各 24 项回归测试通过。复制生产数据库后验证两条真实失败快照：原快照准确指出发型图；关闭发型参考后素材授权检查通过。未发起付费模型调用。

发布版本 `/opt/ark-video-workflow/releases/20260929T121142Z-active-references`，发布备份 `/opt/ark-video-workflow/data/backups/before-active-references-20260929T121142Z`。服务健康，历史数据未变。

根据用户授权，从共用草稿 `c0448b37472e4a068c48bf7dea925eeb` 移除该发型图并关闭空的发型参考，草稿版本升至 12，其他输入保持不变。操作前数据库备份 `/opt/ark-video-workflow/data/backups/before-draft-hairstyle-fix-20260929T121231Z`。两条历史失败任务记录保持不变，没有自动重跑。
