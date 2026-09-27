# 真人素材本地接入实施计划

用户已批准真人认证方案并要求执行；当前无公网返回页，选择先完成本地功能。

范围：官方控制台发起邀约，本地仅将官方 HTTPS 邀约链接编码为二维码；服务端使用火山 AK/SK 查询真人素材，仅官方 Active 且属于当前账号项目的 Image 可绑定本地人物照片；生成使用 asset:// 引用。主页面仅两个小入口，认证与设置放弹窗。没有公网回调时明确提示，不虚构本地认证或自动成功。

接口合同：/api/portrait/config GET PUT（project default、凭据空输入保留/clear、可显式使用现有 TOS 凭据）；/test POST只读查询；/assets GET返回可用真人图片元数据；/bind POST {local_asset_id,remote_asset_id}验证官方素材；/binding/{local_asset_id} DELETE解除本地绑定；/qr POST {url}返回官方链接二维码图片。绑定必须验证本地文件与官方授权素材一致，不能把同一个授权asset标记应用到任意人照片。优先采用从官方素材导入为本地人物asset方式 /import POST {remote_asset_id}，下载安全检查，保存私有文件并关联远程官方元数据。绑定手动接口可省略，避免错误人脸映射。

公开素材增添 portrait 字段 {remote_asset_id,group_id,project,status}。Private credential account binding fingerprint to prevent cross-account reuse. Generation private snapshot includes validated remote ref map by local asset ID. Worker converts selected local face asset paths to verified remote IDs; VideoProvider additional optional image_asset_uris dict maps Path string to asset://, Ark only.

任务：
- [x] 官方素材服务客户端/私有配置/签名/安全下载/测试
- [x] 本地API、绑定存储、生成前校验、worker/provider引用、测试
- [x] 精简前端弹窗、二维码/配置/素材列表导入、持久恢复、浏览器测试
- [x] 集成复核、完整回归、无付费生成、更新本地服务

安全：不读取/打印现有密钥，不自动上传用户脸图；导入由用户显式选择，未经验证不得声称认证通过；H5二维码属于临时敏感链接，不写日志；本地入口同源限定。测试隔离storage，真实调用仅用户授权范围内的只读权限验证。

## 交付记录
- 357 项自动化测试通过（1 个原有依赖弃用警告）；真人弹窗与原制作流程桌面/手机浏览器测试通过；隔离真实服务的模拟生成流程通过。
- 只读验证现有火山凭据可访问 default 项目真人素材接口，可用图片数为0；本地配置已复用该现有凭据，不复制共享密钥。
- 并发重复导入和弹窗关闭后的异步响应问题已修复；后端只读复核通过。
- 本地服务已更新，真实浏览器验证官方空素材列表、实际SVG二维码返回、关闭清理及手机布局，无JS错误。
- 没有发起真人认证，没有上传用户照片，没有调用付费视频生成。本人仍需在官方控制台完成认证和素材入库。
