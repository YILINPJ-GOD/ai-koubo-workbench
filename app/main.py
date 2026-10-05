"""FastAPI 入口：API 路由 + 前端静态托管 + 启动初始化（含自动备份）。"""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from starlette.middleware.trustedhost import TrustedHostMiddleware

from .config import db_path, ensure_dirs
from .db import init_db
from .api import backup, feed, hotspots, packs, settings, styles, system, today


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_dirs()
    init_db()
    # PD.md 7.2：每次启动自动备份，滚动保留 7 份；失败不阻塞启动
    try:
        if db_path().exists():
            from .services.backup import backup_db

            backup_db(tag="startup")
    except Exception:  # noqa: BLE001 —— 备份失败只记录，不阻塞
        import traceback

        traceback.print_exc()
    yield


app = FastAPI(title="AI口播工作台", lifespan=lifespan)

# 防 DNS rebinding：只接受本机 Host 头（审查F2）
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["127.0.0.1", "localhost", "testserver"],  # starlette 匹配的是不含端口的主机名
)

app.include_router(system.router, prefix="/api")
app.include_router(today.router, prefix="/api")
app.include_router(settings.router, prefix="/api")
app.include_router(feed.router, prefix="/api")
app.include_router(hotspots.router, prefix="/api")
app.include_router(packs.router, prefix="/api")
app.include_router(styles.router, prefix="/api")
app.include_router(backup.router, prefix="/api")

_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"


class SPAStaticFiles(StaticFiles):
    """SPA 路由回退：非 API 路径 404 时回退到 index.html（支持 /hotspots/3 直达）。"""

    async def get_response(self, path: str, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as e:
            if e.status_code != 404:
                raise
            return await super().get_response("index.html", scope)


_PLACEHOLDER = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>AI口播工作台</title></head>
<body style="font-family:system-ui;padding:40px;line-height:1.8">
<h2>前端尚未构建</h2>
<p>后端 API 已在运行。请在项目目录执行 <code>build.bat</code>（需要 Node.js）构建前端后刷新本页。</p>
<p>也可直接访问 <a href="/docs">/docs</a> 查看 API。</p>
</body></html>"""

if _DIST.exists():
    app.mount("/", SPAStaticFiles(directory=_DIST, html=True), name="web")
else:

    @app.get("/", include_in_schema=False)
    async def _index():
        return HTMLResponse(_PLACEHOLDER)
