# 本地开发与组员接入

## 首次运行（Windows x64）

推荐 Python 3.11；当前兼容范围 3.10–3.12。不要使用 3.13+ 安装此版本 MediaPipe。
确认 `python --version`，然后在项目目录执行：

```powershell
.\run.ps1 -Setup
.\run.ps1 -Check
.\run.ps1 -Smoke
.\run.ps1
```

浏览器打开 http://127.0.0.1:8000/__dev，再进入制作台。无需激活虚拟环境。
初次安装需要访问 Python 包索引，可能需要几分钟；本地运行只监听 `127.0.0.1`。
如果机器有多个 Python：` .\setup-dev.ps1 -Python 'C:\Python311\python.exe' `。
如果执行策略禁止脚本，可在当前会话执行 `Set-ExecutionPolicy -Scope Process Bypass`，不修改机器策略。
也可以直接使用 `.\.venv-dev\Scripts\python.exe -m tools.dev run`。

安装器创建 `.venv-dev`，与历史 `.venv` 分开，不继承全局包。不要复制他人的虚拟环境。
`requirements-dev.txt` 是开发安装入口，使用 `requirements-dev-windows.lock.txt` 固定此次在 Windows/Python 3.10.21 验证的依赖版本；服务器仍使用原有部署入口和 Linux 依赖清单。
模型文件由仓库提供，无需从某台开发机复制 `storage`。

## 两种明确的配置

| 项目 | demo（默认） | integration（显式选择） |
| --- | --- | --- |
| 数据、任务、上传素材 | `.dev-data/demo` | `.dev-data/integration` |
| 环境配置 | 内置无凭据默认值 | `.env.integration` + 本模式的后台配置 |
| 视频生成 | 返回打码后的演示视频 | 在后台配置独立测试模型后真实调用 |
| 本地打码、草稿、队列、下载 | 可用 | 可用 |
| AI 灵感、拍法、续写 | 关闭，界面/接口提示需联调配置 | 分别配置测试 LLM 后可用 |
| 人物云端入库、社交链接解析 | 不调用；直接上传本地素材 | 配置测试人物库/TOS/TikHub 后验证 |
| 真人扫码认证 | 不可用 | 还需可访问的公网 HTTPS 回调 |

demo 不复制生产配置，不读取旧根 `.env` 或旧 `storage`，不继承终端里遗留的应用密钥和数据库路径。
开发进程禁止对外 DNS/连接，并拦截云端功能及服务配置写入；这是一项防止误调用的开发约束，不是操作系统级安全沙箱。
mock 提交仍检查本地输入和访问权限，但不要求实际创建云端人物，不生成虚假的授权记录。
**演示结果不是 AI 生成。** 不用它评估模型效果或验证真人授权。

真实服务联调：

```powershell
Copy-Item .env.integration.example .env.integration  # 仅首次；不要覆盖已填写的文件
.\run.ps1 -Profile integration -Check
.\run.ps1 -Profile integration -Port 8001
```

打开该端口的 `/admin/settings`，分别配置视频模型、拍法 LLM、续写 LLM、TOS、人物库与 TikHub。
AI 灵感默认可以继承拍法 LLM；视频模型 Key 不会自动变成所有 LLM 的 Key。
后台保存的 JSON 配置优先于环境变量。缺少某个服务不影响其他已配置模块开发。
只使用负责人分配的测试凭据；不在文档或仓库里填写密钥。配置清单不能覆盖启动器的数据库、数据目录、监听地址或 Python 路径。
`.env.integration`、`.dev-data`、`.venv-dev` 均被 Git 忽略。

真人认证的 `PORTRAIT_PUBLIC_BASE_URL` 必须是专用开发回调的 HTTPS origin，不能是 localhost。
这类公网联调也可以在 test 上验收；启动脚本不会部署、修改或自动连接任何服务器。

## 日常开发

```powershell
.\run.ps1 -Reload                   # 修改源码后重载；正在执行的任务可能中断
.\run.ps1 -Port 8002                # 端口被占用时换端口，不强制结束他人进程
.\run.ps1 -Check                    # 解释器、依赖、FFmpeg、模型、功能配置状态
.\run.ps1 -Test                     # 本地开发相关回归，使用临时数据库
.\run.ps1 -Smoke                    # 实际上传→草稿→队列→打码→演示输出→下载与解码
```

单项测试：`.\.venv-dev\Scripts\python.exe -m tools.dev test tests/test_local_development.py -q`。
`-Test` 默认是与本地入口相关的回归集，不宣称运行整个仓库测试套件。
`-Smoke` 无论从哪种 profile 发起，都使用新的演示数据根，阻止外网调用；报告保留在 `.dev-data/smoke-*/report.json`。
多人各有自己的 clone 和数据目录；不要同时从两个进程启动同一 profile 的任务 worker。
停止服务使用运行终端中的 Ctrl+C。长任务期间不要开启 Reload。

## 常见错误

| 现象 | 处理 |
| --- | --- |
| 提示没有本地环境 | 运行 `run.ps1 -Setup`，不要回退到全局 `python -m uvicorn` |
| demo 配置被改成真实服务 | 文件会保留且启动检查拒绝；切换 integration，或自行备份该 demo 配置后重新初始化 |
| 缺少 LLM Key | 确认使用 integration，并在对应后台模块配置，不要只改旧 `.env` |
| 中文路径下打码模型加载失败 | 使用新入口和当前代码；默认 deface 已固定解释器和字节加载，MediaPipe 在独立子进程使用临时资源 |
| Windows TEMP 路径含中文且短路径不可用 | 将当前进程的 TEMP/TMP 指向自己可写的英文路径后重启，例如 `C:\dev-temp`；不改系统全局变量 |
| 浏览器 Cookie 读取失败 | 新入口不读取浏览器 Cookie；优先直接上传，社交解析在 integration 配置 TikHub |
| 端口占用 | 使用 `-Port` 或正常停止自己的旧服务 |
| 真实接口返回权限、额度、素材拒绝 | 在 test/integration 核验对应服务；本地演示通过不代表云端权限和额度已就绪 |

## 合并交付

组员必须拉取**包含本地开发修复及其所依赖应用源码**的提交。服务器上正在运行的文件、某台电脑未提交的代码和 Git 仓库不是自动同步的。
提交只包含源码、测试、脚本和配置模板；不要提交 `.env*` 实际配置、数据库、上传素材或诊断输出。
本地统一入口不会代替 PR 合并、代码推送或 test/prod 发布。
