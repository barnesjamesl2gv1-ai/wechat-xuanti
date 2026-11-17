# 容器镜像：一条命令跑起来
#
#   docker run -d --name xuanti -p 8767:8767 \
#     -e DEEPSEEK_API_KEY=xxx -e SEARCH_PROVIDER=bocha -e BOCHA_API_KEY=xxx \
#     -e XUANTI_ACCESS_TOKEN=你的口令 \
#     -v xuanti-data:/app/公众号分析输出 \
#     ghcr.io/<owner>/wechat-xuanti:latest
#
# 数据（Excel 去重库、报告、手绘曲线）都在 /app/公众号分析输出，务必挂卷，否则删容器即丢。
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    XUANTI_HOST=0.0.0.0 \
    XUANTI_PORT=8767 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./
RUN python -m pip install --upgrade pip && python -m pip install -r requirements.txt

COPY . .

# 运行期产生的数据目录（挂卷点）
RUN mkdir -p "/app/公众号分析输出"
VOLUME ["/app/公众号分析输出"]

EXPOSE 8767

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8767/api/status', timeout=4).status==200 else 1)"

CMD ["python", "scripts/serve.py"]
