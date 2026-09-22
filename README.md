# WrongBook

个人数学一与 408 错题整理系统基线实现。运行环境要求 Windows、Python 3.11+；前端构建后由 FastAPI 提供静态文件。

## 启动

日常使用桌面上的 **启动错题本** 和 **关闭错题本**：开机后双击启动，会自动打开网页；关机前双击关闭，看到完成提示后即可关机。重复点击有保护，关闭不会删除数据库、原图或备份。操作失败时查看 `logs/desktop.log`。

重新创建桌面快捷方式：`powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install-shortcuts.ps1`。

复制 `.env.example` 为 `.env`，按需填写 `AI_BASE_URL/AI_API_KEY/AI_MODEL`。未配置 Key 仍可上传并保留原图，任务会返回明确的不确定信息。执行 `powershell -ExecutionPolicy Bypass -File scripts/start.ps1`，关闭执行 `scripts/stop.ps1`，状态执行 `scripts/status.ps1`。

开发前端：`cd frontend; npm install; npm run build`。生产访问 `http://127.0.0.1:8000`；Tailscale Serve 示例：`tailscale serve --https=443 http://127.0.0.1:8000`。iOS/iPadOS Web Push 需要 HTTPS、添加到主屏幕并授权；电脑关机期间本地提醒服务不会运行，重新启动时会扫描逾期任务。

数据位于 `data/`、`uploads/`、`backups/`，关闭脚本不会删除它们。系统接口提供手动备份、导出和完整性检查。AI 任务状态可恢复：`uploaded/pending_recognition/recognizing/analyzing` 会在服务启动后继续，失败任务可重试。

### 保持在线

- `start.ps1` 用 `pythonw.exe`（无控制台）通过 WMI 启动服务，进程不挂在任何窗口下，关掉 PowerShell 也不会连带杀掉它；stdout/stderr 写入 `logs/uvicorn.out.log` 与 `logs/uvicorn.err.log`，意外退出有据可查。
- `start.ps1` 会自动把 `tailscale serve --bg --https=443` 配成后台常驻，手机地址不会因为窗口关闭而失效。
- 已注册计划任务 `WrongBook Watchdog`：每 5 分钟检查一次，服务不在就自动拉起（`run/watchdog.vbs`，无窗口闪烁）。删除命令 `schtasks /delete /tn "WrongBook Watchdog" /f`。
- 队列在服务启动时会自动把中断在 `recognizing/analyzing` 的任务重新排队，不会永久卡死。
- 脚本文件保持**纯 ASCII**（`install-shortcuts.ps1` 例外，它是带 BOM 的 UTF-8）：Windows PowerShell 5.1 会把无 BOM 的 UTF-8 当 GBK 解析，中文注释字节数为奇数时会吞掉换行，把下一行代码变成注释。

## AI 图像识别

识别走服务器端的 OpenAI 兼容 HTTP 接口，前端不接触 API Key。每张原图先落盘，再按标准 `image_url` 视觉消息格式发送给模型，提示词要求先识别题目与解题过程，再定位第一处实质性错误，最后自检并给出置信度。

`.env` 最少需要三项：

```text
AI_BASE_URL=https://你的中转站或官方地址/v1
AI_API_KEY=你的密钥
AI_MODEL=支持图像输入的模型名
```

当前这份配置已经指向中转站 `https://modelflare.dev/v1`，模型 `gpt-5.6-sol`，实测可以直连并正确读图。换供应商时只改这三行，业务代码不用动。

### 推荐服务入口

- AI 中转站注册：[ModelFlare](https://modelflare.dev/sign-up?partner=LSVXXRTTA3YJ)
- VPN 注册：[VPN 服务入口](https://43.132.225.172:4901/register?code=V7BXCG2R)

以上链接也会显示在应用的“状态”页面，方便从手机直接打开。服务由第三方提供，价格、协议和可用性以服务商页面为准。

如果供应商在境外需要走代理，把 `AI_PROXY` 设为 `auto`（读取 Windows 系统代理）或直接写地址，例如 `http://127.0.0.1:7890`；不需要代理就设为 `none`。

其它可调项：`AI_TIMEOUT`（秒）、`AI_MAX_RETRIES`（失败重试次数）、`AI_IMAGE_COMPRESS`（发送前压缩）、`AI_IMAGE_MAX_EDGE`（长边像素）、`AI_IMAGE_JPEG_QUALITY`、`AI_CONFIDENCE_THRESHOLD`（低于该值会在确认页标记为不确定）、`AI_SAVE_RAW_RESPONSE`（是否保存模型原始响应，默认关闭）。部分兼容接口不支持 JSON 模式，系统会自动去掉 `response_format` 重试。

未配置 Key 时，任务会直接进入 `failed` 并给出明确原因，原图保留，配置后可重试；不会生成看似成功的结果。日志只记录任务ID、模型名、耗时、状态码和错误类型，不记录密钥。

## 识别正确率

正确率有两个来源，都在状态页可见：

- **视觉自检**：点击“测试AI识别能力”，系统现场渲染已知答案的算式图片交给模型识别并自动比对，直接给出该模型在这台电脑上的读图正确率。
- **人工评价**：确认 AI 结果时必须选择识别与纠错是否准确（正确 / 部分正确 / 错误），部分正确按 50% 计入，累计正确率显示在首页与状态页。

## 测试

```powershell
python -m pip install -r requirements.txt
python -m pytest -q
# 一键执行后端测试、编译检查、前端构建和运行中服务健康检查
powershell -ExecutionPolicy Bypass -File scripts/test.ps1
```

覆盖解析器置信度、接口配置化调用、图片压缩与视觉消息格式、日志不泄露密钥、上传后 AI 不可用时原图保留、确认后入库与正确率统计，以及“上传→队列→视觉接口→待确认→入库”的完整链路。

## 功能一览

界面为 iOS 风格的深色设计：纯黑背景、大号粗体标题、下划线标签页、粗环甜甜圈图与彩色进度条图例、带彩色圆环的列表行、白色悬浮加号按钮；右上角可切换深色 / 浅色 / 跟随系统，选择会记在浏览器里。手机端为底部标签栏，桌面端自动变成顶部标签栏。

- 上传：手机拍照多图上传，每张图可标记题目 / 解题过程 / 参考答案，并可裁剪、旋转，只识别选中的区域。裁剪参数单独保存，原图不覆盖；导出同时包含原图和裁剪参数。上传时显示传输进度，原图先落盘再进 AI 队列。
- 任务中心：首页、上传页、复习页均有入口，按状态查看普通上传和重做任务，每 3 秒刷新；失败可重试，待确认任务可重新打开，刷新页面也能找回已保存的任务。
- 图片核对：确认页提供原图 / 裁剪图与识别结果对照，支持图片放大、修改 LaTeX 并实时预览；点击“保存修改草稿”后可下次继续，只有最终确认才入错题集。
- 内容显示：默认展示排版结果，点击“修改”才展开源码。408 优先展示完整题干，文字自动换行，局部公式由 KaTeX 渲染；兼容旧记录中误用的文字型 LaTeX，公式语法异常时明确提示并保留原文。前端内容回归测试：`cd frontend; npm test`。
- 确认：AI 结果全部可改（题目、LaTeX、参考答案、第一处错误、正确思路、知识点、错误标签、408 代码块、难度），确认后才进入错题集。
- 错题集：关键词搜索，按科目（含“408”四科合并）、章节、知识点、错误类型、日期区间筛选，支持按最该复习 / 最不熟 / 最难排序。
- 复习：今日与逾期分开查看，四档评价后自动计算下次复习时间；首页显示反复出错的题目。
- 统计：知识点分布、错误类型分布、科目分布、近 30 天新增、难度分布。
- 状态：磁盘空间、Tailscale 连接、自动备份间隔、备份文件列表、AI 视觉自检正确率。
- 数据：手动备份、每 24 小时自动备份（默认保留 7 份）、导出含原图的 zip、完整性检查。关闭脚本只停服务，不删除数据库、图片和备份。
