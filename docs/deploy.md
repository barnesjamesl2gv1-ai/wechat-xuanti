# 部署指南

三种场景，按「省事程度」从高到低。**都没有云服务依赖，全部跑在使用者自己的机器上**，这点先说清楚：本项目是本地自托管的，没有官方托管站点，每位使用者在自己的电脑或服务器上运行一份。

---

## 一、Windows 用户：双击一次

前提：装好 Python 3.11+（安装时勾选 *Add python.exe to PATH*）。

```
下载仓库（Code → Download ZIP）→ 解压 → 双击 安装.bat
```

`安装.bat` 会自动：建虚拟环境 → 装依赖 → 用 `.env.example` 生成 `.env` → 提示你填两个 Key → 启动。

填 Key：用记事本打开 `.env`，填 `DEEPSEEK_API_KEY`、`SEARCH_PROVIDER=bocha`、`BOCHA_API_KEY`，保存后重新双击 `启动系统-浏览器版.bat`。

第二条命令版（已经装了 git 和 Python）：

```powershell
git clone https://github.com/barnesjamesl2gv1-ai/wechat-xuanti.git; cd wechat-xuanti; .\安装.bat
```

---

## 二、有 Docker 的机器（Windows/macOS/Linux/NAS）：一条命令

```bash
docker run -d --name xuanti -p 127.0.0.1:8767:8767 \
  -e DEEPSEEK_API_KEY=你的key \
  -e SEARCH_PROVIDER=bocha -e BOCHA_API_KEY=你的key \
  -e XUANTI_ACCESS_TOKEN=自己定一个口令 \
  -v xuanti-data:/app/公众号分析输出 \
  ghcr.io/barnesjamesl2gv1-ai/wechat-xuanti:latest
```

打开 http://127.0.0.1:8767 ，第一次会让你访问 `/auth?token=口令` 换 cookie。

或者用 compose（在仓库目录里）：

```bash
cp .env.example .env      # 填两个 Key
docker compose up -d
```

**镜像从哪来**：仓库有 `.github/workflows/docker.yml`，推到 main 或打 `v*` 标签会自动构建并推到 GitHub Container Registry。首次使用时到 *Packages* 页面把镜像可见性改成 public，才能匿名 `docker pull`。

**数据别丢**：所有数据在 `/app/公众号分析输出`，必须挂卷（上面 `-v xuanti-data:...` 或 compose 里的 `./公众号分析输出`）。

---

## 三、部署到服务器供多人访问（需谨慎）

先说结论：**这个工具没有多用户概念，也没有云端账号体系。** 它假设「一个人、一台机器、一份数据」。所以服务器部署只适合「团队内部几个人共用一套，且你信任他们」。

部署前必须做三件事，否则等于把你的 API 额度公开：

1. **设访问口令**（否则任何人打开就能点「开始 AI 分析」花你的钱）：
   ```bash
   -e XUANTI_ACCESS_TOKEN=一个足够长的随机串
   ```
2. **别用 Flask 自带开发服务器**，用 `scripts/serve.py`（waitress）——Docker 镜像默认就是它。
3. **前面挂反向代理**（Nginx/Caddy）做 HTTPS，并把 `XUANTI_HOST` 设成 `0.0.0.0`（镜像已默认）。

局域网内共享（同一台机器、同一个 Wi-Fi）：

```bash
docker run -d -p 8767:8767 -e XUANTI_ACCESS_TOKEN=口令 ... 镜像
# 其他设备访问 http://<本机内网IP>:8767/auth?token=口令
```

### 不要做的事

- 直接把 `8767` 暴露到公网且不设口令；
- 多人同时用同一个实例跑分析——后台任务只有一份锁，会互相阻挡；
- 把 `.env`、`公众号分析输出/` 打进镜像或提交到仓库。

### 想真正「多人 SaaS 化」需要改什么

现在没有：用户体系、额度控制、任务队列隔离、数据多租户。要做得先动数据层（每个账号一份目录 → 每个用户一份命名空间）和鉴权，和当前「单机工具」的定位是两件事。

---

## 四、用 AI agent 部署（可选）

有能操作本机终端的 AI agent（Claude Code、Codex、Cursor 等）时，可以让它把上面这些步骤全做完。**把下面这段话整段发给它**：

```text
帮我把 https://github.com/barnesjamesl2gv1-ai/wechat-xuanti 部署到我这台电脑：
用 uv（没有 uv 就用 Python 3.11+ 的 venv）建环境、装依赖、启动服务，
最后把访问地址告诉我。部署前先检查 Python/uv 是否存在，缺什么先告诉我。
API Key 我自己填 .env，不要让我在对话里贴出来。
```

### agent 会执行的操作

1. `git clone` 仓库（没装 git 的话会下载 ZIP）；
2. 检查 `uv` / `python` / `docker` 有没有；
3. 建 `.venv`、装 `requirements.txt` 里的依赖；
4. 复制 `.env.example` → `.env`，然后**停在填 Key 这一步**；
5. 启动 `web_app.py`，把控制台打印的地址（`127.0.0.1:8765` 起，被占用会自动换）报出来。

### 需要使用者自行解决的三件事

| 事项 | 说明 |
|---|---|
| **API Key** | DeepSeek + 博查各一个，agent 无法代为获取 |
| **装 Python** | 机器上完全没有 Python/uv/Docker 时，装 Python 可能要管理员权限，agent 会被卡住 |
| **网络** | 装依赖需要能连 PyPI；国内网络慢时加 `-i https://pypi.tuna.tsinghua.edu.cn/simple` |

### 关于 Key 的两点提醒

- **推荐**：让 agent 停在「填 Key」那一步（上面那段话就是这么写的），由使用者自己打开 `.env` 填写。
- **如需 agent 一次做完**，可以这样写：「…部署并启动，启动前把 Key 写进 .env：DEEPSEEK_API_KEY=xxx，BOCHA_API_KEY=xxx，SEARCH_PROVIDER=bocha」。此时 Key 会出现在对话记录里，建议用完到控制台**轮换一次密钥**，并确认该 agent 不会把内容发到别处。

### 一句话的准确预期

能「一句话启动部署」，但做不到「零后续」：前置条件（Python/网络/Key）不满足时，agent 一定会回头确认。实测在已装 uv 的机器上，从干净克隆到启动约 **3 秒**装完依赖。

---

## 环境变量速查

| 变量 | 默认 | 作用 |
|---|---|---|
| `XUANTI_HOST` | `127.0.0.1` | 监听地址；容器/局域网用 `0.0.0.0` |
| `XUANTI_PORT` | `8765`（占用则向上找） | 端口；容器里固定 `8767` |
| `XUANTI_ACCESS_TOKEN` | 空（不启用） | 访问口令，设了就需要 `/auth?token=` 换 cookie |
| `XUANTI_OPEN_BROWSER` | 未设置 | 设 `1` 时启动后自动开浏览器（默认不抢焦点） |
| `DEEPSEEK_API_KEY` / `SEARCH_PROVIDER` / `BOCHA_API_KEY` / `TAVILY_API_KEY` | 从 `.env` 读 | 设了环境变量会覆盖 `.env`，容器里就不用挂 `.env` |
