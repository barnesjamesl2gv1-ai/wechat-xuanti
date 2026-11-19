# 用采集端把文章抓下来（可选，但完整流程需要）

本项目只做「拿到文章之后」的选题分析。文章本身要从微信公众号采集，采集器不在本仓库里。

## 为什么要分开

采集器是上游项目 [yeximm/Access_wechat_article](https://github.com/yeximm/Access_wechat_article) 的，协议是 **CC BY-NC-SA 4.0**（可免费用、不可商用）。本项目是 MIT。两者不能混在一个仓库里，否则整个项目就不能商用了。

所以做法是：**代码不复制，用脚本帮你装。**

## 一键安装

双击项目根目录的 `安装采集端.bat`，它会：

1. 把采集器下载到 `collector/`（本项目的子目录，已被 `.gitignore` 排除，不会提交）
2. 用 uv 装好依赖和抓取用的浏览器
3. 在 `.env` 里写入 `XUANTI_STORAGE_ROOT=collector/storages`，让本项目知道去哪读数据

需要先有 **git** 和 **uv**（脚本会自动检查并给出安装提示）。

## 手动装（等价的命令）

```powershell
git clone --depth 1 https://github.com/yeximm/Access_wechat_article.git collector
cd collector
uv sync
uv run playwright install chromium
```

然后在项目根目录的 `.env` 里加一行：

```ini
XUANTI_STORAGE_ROOT=collector/storages
```

## 采集

```powershell
cd collector
uv run python main.py
```

按采集器自己的界面操作：选公众号、设采集数量、启动任务。它需要 **微信 PC 客户端** 和 **代理证书**，第一次用要按上游文档装一下证书。

采集完成后，文章会落在 `collector/storages/<账号>/<文章>/`。

### 桌面窗口起不来时（Python 3.13 常见）

采集器的桌面窗口用 pywebview，Windows 上它依赖 pythonnet + .NET。本机实测（Python 3.13 + pythonnet 3.1.0）会直接弹：

```text
程序启动失败：Failed to resolve Python.Runtime.Loader.Initialize from
...\pythonnet\runtime\Python.Runtime.dll
```

这不是证书、代理或微信的问题，是**桌面外壳加载 .NET 失败**。上游自带一个开发后端，
跑的是同一个后端和同一份前端，只是外壳换成浏览器：

```powershell
cd collector
uv run python dev_server.py
```

然后浏览器打开 <http://127.0.0.1:8766/index.html>。界面和功能完全一样：选公众号、开始运行、看进度。

- 8766 被占用时 `dev_server.py` 会直接报错退出，先关掉占用该端口的程序
- 浏览器模式下关标签页**不会**停服务，要停就在终端里 Ctrl+C 或关掉那个窗口
- 这是上游的「开发模式」入口，日常够用；想要常驻稳定，等上游修好 pythonnet 或用 Python 3.12 建环境

## 回到本项目

双击 `启动系统-浏览器版.bat`，先点「更新本地数据」。

- 本项目默认读 `./storages`
- 如果你按上面配了 `XUANTI_STORAGE_ROOT`，它就读 `collector/storages`
- 两个都没有数据时，点「更新本地数据」会直接提示「还没有文章数据」，不会静默失败

## 常见问题

| 现象 | 原因 |
|---|---|
| 提示「还没有文章数据」 | 采集端还没抓过，或 `.env` 里的路径写错 |
| `uv` 找不到 | 先装 uv：`powershell -c "irm https://astral.sh/uv/install.ps1 \| iex"` |
| 采集器启动报证书/代理错误 | 按上游 README 装 mitmproxy 证书；这是采集器自己的前置条件 |
| 采集器弹 `Failed to resolve Python.Runtime.Loader.Initialize` | 桌面外壳依赖 pythonnet/.NET，Python 3.13 上加载失败 → 改用 `uv run python dev_server.py`，浏览器打开 8766（见上一节） |
| 依赖装不上 | 试试国内镜像：`set UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple` |

## 合规提醒

- 采集公开文章仍受微信服务条款约束，请控制频率、自用为主。
- 采集器协议是 CC BY-NC-SA 4.0：**不能用于商业用途**。如果你的使用场景涉及商业变现，请先获得上游作者授权，或自行实现采集端。
