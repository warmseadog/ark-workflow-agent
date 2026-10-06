# 围巾、手饰兼容增量记录（2026-10-06）

用户要求：在「更多搭配」增加围巾和手饰；兼容衣服参考图已经带有这些配饰的情况；保留原来效果良好的提示词，只做小幅补充，并可定点恢复。

## 本次行为

- 新增 `scarf`（围巾）和 `hand_jewelry`（手饰：手链、手镯、戒指）。手表仍使用原独立类别。
- 复用现有上传、预览、草稿恢复、任务快照、任务复制、素材编号和供应商提交链路。
- 独立参考启用时覆盖衣服图中的同类配饰；未启用时从衣服主图/补充图中取清楚展示的该类。主图冲突优先，补充图只补缺失；没有展示则不凭空添加。
- 围巾补充围法、长度、垂坠及运动要求；手饰补充数量、位置和手部结构要求。
- 原有 yoyo 配饰继承逻辑继续沿用；exclusive / legacy 仅为这两个新类别增加继承规则，其他旧配饰行为不变。人物、动作、镜头等既有正文不重写。
- 现有模型的图片数量限制不变。适配器可接受的素材类别总数校验随可选类别数量增长，避免原 69 张硬编码误拒绝新增类别。
- 复用现有简洁版界面。当前 `compact-home.css` 隐藏全部配饰的「本次使用」开关；本次未改动该样式，停用/恢复的数据能力保留。

## 默认提示词的最小更新

`app/prompt_templates.py` 仅在类别枚举加入围巾、手饰，并定义独立标记段 `【围巾手饰兼容补充 v1】`。

`app/local_preferences.py` 在应用读取配置时执行一次迁移：

1. 只处理共享配置库里现存的内置模板 ID：`default-exclusive-v2`、`default-yoyo-v3`、`default-0` 至 `default-3`。
2. 原正文逐字保留，只在末尾追加标记段。不创建已删除模板，不改个人模板，不改历史草稿或已提交任务快照。
3. 迁移标记为 `preferences.prompts_scarf_hand_v1`；正文前后版本保存在同库 `prompt_scarf_hand_backups` 表。
4. 有标记的正文不会重复追加；追加会超过 10000 字时跳过正文更新，动态素材规则仍生效。
5. 管理员后续保存正文时不会被再次追加或覆盖。

现有草稿保留旧正文，但下一次生成的动态素材规则会识别新类别。新建草稿/重新选择默认模板时使用迁移后的默认正文。本次开发只在隔离测试库验证，没有直接修改实际运行数据库，也未部署或重启线上服务。

## 涉及文件

- `app/reference_roles.py`：类别与佩戴要求。
- `app/production_store.py`：新草稿默认字段。
- `app/static/production.js`、`app/templates/production_panel.html`：新配饰入口和前端素材流程。
- `app/templates/production.html`、`app/templates/studio.html`：脚本缓存版本。
- `app/reference_prompt.py`、`app/editor_assets/legacy-prompt-rules.js`：动态绑定和两类配饰继承。
- `app/prompt_templates.py`、`app/local_preferences.py`：模板补充及可恢复迁移。
- `deploy/rollback_scarf_hand_prompts.py`：仅恢复默认模板正文的工具。
- `tests/test_scarf_hand_accessories.py`、`tests/test_scarf_hand_accessories_browser.py`：本次专项验证；旧模板迁移测试同步允许新增的标记段。

## 恢复方式 A：只恢复已保存默认模板正文

使用**真实共享配置目录**替换下面的 `实际共享配置目录`。不要传某个用户的私有子目录。

```powershell
./.venv/Scripts/python.exe deploy/rollback_scarf_hand_prompts.py --storage-dir "实际共享配置目录"
```

默认只预览：`restore` 列出可恢复模板，`skip_changed_or_deleted` 列出后来改动或删除、需人工比对的模板。确认目标正确后执行：

```powershell
./.venv/Scripts/python.exe deploy/rollback_scarf_hand_prompts.py --storage-dir "实际共享配置目录" --apply
```

工具只把仍等于本次迁移结果的正文恢复到迁移前，不改名字、规则版本、其他偏好、个人模板、素材或任务。不恢复已删除条目，不覆盖后续人工改动。备份和迁移标记保留，避免下次读取又自动加回来。运行中的页面需重新读取/重新选择模板；已经保存的草稿不批量修改。

**此方式只撤销追加到默认正文的标记段，保留新配饰入口及动态兼容规则。** 如需完全恢复原行为，用方式 B。

## 恢复方式 B：撤回整个增量

开发开始时已存在其他未提交工作，不能用 `git reset --hard`、整目录覆盖或直接回退到 HEAD。备份以本次动手前的实际文件为基准，保留当时其他改动。

本机恢复包：`exports/scarf-hand-accessories-20261006/`

- `before/`：本次涉及的既有文件原始副本。
- `change.patch`：仅本次修改的正向差异，可反向应用。
- `manifest.json`：文件清单、修改前后 SHA-256。
- `status-before.txt`：动手前工作区状态。

1. 暂停服务接收新生成任务，保留当前配置数据库和代码副本。
2. **先运行方式 A 的正文恢复工具**；如有后续人工改动的模板，先检查再手动移除本次标记段。
3. 在项目根目录检查反向补丁：

   ```powershell
   git apply --reverse --check exports/scarf-hand-accessories-20261006/change.patch
   ```

4. 检查通过且确认撤回时：

   ```powershell
   git apply --reverse exports/scarf-hand-accessories-20261006/change.patch
   ```

5. 重启服务并刷新页面。原素材文件、任务记录和数据库备份保留。

如果检查失败，说明相关文件后来又有变动；应按补丁逐段撤销，不能强制覆盖。旧版本不识别新类别，因此已有围巾/手饰的新草稿和待执行任务应先处理完或明确取消再整体回退。恢复包存放在被 Git 忽略的 `exports/` 下，只存在本机；部署时应将它单独保留。

## 验证记录

专项测试覆盖图片/人物视频两种编号、衣服图继承、独立覆盖、无衣服图、草稿保存与复制、模板只迁移一次、保留个人正文、回滚预览与后续编辑保护、适配器最多 71 个有效参考角色。

页面测试覆盖桌面 1440px 与手机 390px 的新入口、上传、动态提示词、刷新恢复及 legacy 模板切换。隐藏开关通过事件验证数据行为，不宣称其在现有页面中可点击。未调用付费生成服务，围巾/手饰的实际成片效果仍需用真实素材检查。

最终相关回归：**132 passed**（45.23 秒），含两个浏览器尺寸及 legacy/exclusive/yoyo 三种供应商提交映射；仅有现有 Starlette/AnyIO 弃用警告。报告：`exports/scarf-hand-accessories-20261006/targeted-tests.xml`。

全量检查未全绿，记录如下：

- 从项目根目录直接执行 `pytest` 会同时收集 `exports/`、`video-production-module/` 中已有历史测试副本，产生 1466 个收集错误（重复模块路径等）；详见恢复包里的 `full-suite.log`。应指定 `tests/`。
- 执行 `pytest tests -x` 时，37 项通过后停在既有 `tests/test_account_frontend.py::test_tikhub_settings_and_nested_admin_links_are_hidden_until_admin_identity`：测试期望 `#link-settings` 可见，但未被本次修改的 `app/static/compact-home.css` 第 69–76 行明确隐藏它。日志见 `full-first-failure.log`；没有为了配饰功能改动现有首页样式。
- 原 `test_exclusive_prompts_browser.py` 同样试图点击上述样式隐藏的配饰开关；其旧交互假设不作为本次通过项。本次新浏览器用例已验证真实上传按钮和恢复数据。

本次代码复查发现并修正了可选类别增加后原 `69` 个角色上限未同步的问题。恢复补丁执行 `git apply --reverse --check` 已通过；只验证可恢复性，未实际撤销改动。
