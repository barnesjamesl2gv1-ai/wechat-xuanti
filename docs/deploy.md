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
git clone https://github.com/<owner>/wechat-xuanti.git; cd wechat-xuanti; .\安装.bat
```

---

## 二、有 Docker 的机器（Windows/macOS/Linux/NAS）：一条命令

```bash
docker run -d --name xuanti -p 127.0.0.1:8767:8767 \
  -e DEEPSEEK_API_KEY=你的key \
  -e SEARCH_PROVIDER=bocha -e BOCHA_API_KEY=你的key \
  -e XUANTI_ACCESS_TOKEN=自己定一个口令 \
  -v xuanti-data:/app/公众号分析输出 \
  ghcr.io/<owner>/wechat-xuanti:latest
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

## 环境变量速查

| 变量 | 默认 | 作用 |
|---|---|---|
| `XUANTI_HOST` | `127.0.0.1` | 监听地址；容器/局域网用 `0.0.0.0` |
| `XUANTI_PORT` | `8765`（占用则向上找） | 端口；容器里固定 `8767` |
| `XUANTI_ACCESS_TOKEN` | 空（不启用） | 访问口令，设了就需要 `/auth?token=` 换 cookie |
| `XUANTI_OPEN_BROWSER` | 未设置 | 设 `1` 时启动后自动开浏览器（默认不抢焦点） |
| `DEEPSEEK_API_KEY` / `SEARCH_PROVIDER` / `BOCHA_API_KEY` / `TAVILY_API_KEY` | 从 `.env` 读 | 设了环境变量会覆盖 `.env`，容器里就不用挂 `.env` |
