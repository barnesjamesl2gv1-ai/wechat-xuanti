# 项目规则（2026-11-15 ~ 11-18 复盘沉淀）

这份文件不是愿望清单，是**用返工换来的**。每条规则后面都写着它对应的真实事故。

## 规则 1：新功能必须在「陌生环境」验收一次

**做法**：新目录 + 干净克隆 + 空 `.venv`，按 README 走一遍，之后才算完成。

**事故**：安装脚本连改四次（乱码 → TEMP 不可写 → pip 卡死 → uv 优先）。根因是只在"我的机器、我的账号、已有环境"下验证过，问题全留给了第一次真实部署。

## 规则 2：先冻结数据模型与接口契约，再写实现

**做法**：动数据结构前，先在 `docs/design.md` 写清字段与含义；接口参数改动先改文档。

**事故**：手绘曲线一晚连改三次（撤销失效、两张图互相干扰、改成"一条曲线两个视角"）。根因不是手误，是"7 天 / 30 天存两份还是一份"这个模型没先定。

## 规则 3：判断「有没有用」必须过入口矩阵，禁止单点代理指标

**做法**：判定文件/功能是否可删，按 `docs/architecture.md` 的六项矩阵逐条查（import、subprocess、.bat、文档指令、计划任务、外部调用）。任一项为「是」就是在用。

**事故**：把采集端（`src/`、`main.py`、`vue-project/`、`desktop_launcher.py`）判成死代码，因为"没人 import 它"。它实际由入口脚本启动，是整条数据链的上游。

同理：事件去重阈值原来拍 0.72，实测只召回 3/12，等于没去重。**量化过再定阈值**。

## 规则 4：真实账号、数据、私人口吻永不进公开仓库

**做法**：
- 账号名只放本地 `git config --local leakguard.words`，不写进任何跟踪文件（包括 `.gitignore`）
- 文章数据、Excel、Key 只留在硬盘，绝不 `git add`
- 提交信息也用中性表述，不写"给别人用"这类私人交付口吻
- 公开前跑 `scripts/preflight.py`

**事故**：`.gitignore` 里写了真实账号名 → 已公开 11 小时 → 三轮历史重写 + 两次删库重建。这是三天里最贵的一次返工，而且完全可预防。

## 规则 5：发布前跑 preflight，不靠肉眼

**做法**：`python scripts/preflight.py`。它检查：语法、全部测试、泄露、`.bat` 编码（ASCII+CRLF+无 BOM）、文档图片是否存在、占位符是否残留、数据目录是否被跟踪、超大文件。

**事故**：镜像地址占位符、测试说明漏一个文件、截图裂图、README 文案反复改——全是"发布后才逐个发现"。

## 规则 6：禁止用一次性脚本改多个文件

**做法**：多文件改造先写成 `scripts/` 下的可复用工具，跑完保留；单文件改动用编辑器。

**事故**：临时 Python/PowerShell 片段反复踩坑（`.Replace` 重载不存在、heredoc 不支持、引号转义、GBK 控制台把 `✔` 打崩）。每次故障都要额外一轮修复。

## 规则 7：编码与换行按平台常量处理

- `.bat`：**纯 ASCII + CRLF + 无 BOM**（中文提示交给 Python 打印），并用 `.gitattributes` 固定 `*.bat text eol=crlf`
- 文本文件：UTF-8 无 BOM
- 读文件显式 `encoding="utf-8"`，不要依赖系统默认（中文 Windows 默认 GBK，会把 UTF-8 读成乱码，进而导致误判）

**事故**：`.bat` 中文导致 cmd 解析失败；安装脚本提示语被 ASCII 编码吞成问号；PowerShell 读 UTF-8 显示乱码，一度让我误判文件内容；只靠 Windows 的 `core.autocrlf` 保 CRLF，结果在 Linux CI 上 .bat 变成 LF 直接失败——必须用 `.gitattributes` 固定，不能依赖本机设置。

## 规则 8：删改前先写「反证条件」

**做法**：下"可删除"结论前，先写下"什么证据会推翻它"，并实际去查。

**事故**：同一类错误出现两次——一次判采集端为死代码，一次把 `desktop_launcher.py` 判成 0 引用死文件（它 `Popen` 启动 `main.py`）。

---

## 自动化落点

| 检查 | 触发时机 |
|---|---|
| `scripts/check_leaks.py` | 每次 commit（`.githooks/pre-commit`） |
| `scripts/preflight.py` | 每次 push（`.githooks/pre-push`）+ CI |
| `tests` + `docker` 工作流 | 每次 push |

启用钩子（每份克隆一次）：

```powershell
git config core.hooksPath .githooks
```
