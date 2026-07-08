"""番茄猫 Dashboard API - 提供 Web 仪表板接口"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse

logger = logging.getLogger(__name__)


class DashboardAPI:
    def __init__(
        self,
        workspace: Path,
        memory: Any = None,
        skills_loader: Any = None,
        scheduler: Any = None,
        proactive_engine: Any = None,
        host: str = "127.0.0.1",
        port: int = 8765,
    ) -> None:
        self.workspace = workspace
        self.memory = memory
        self.skills_loader = skills_loader
        self.scheduler = scheduler
        self.proactive_engine = proactive_engine
        self.host = host
        self.port = port
        self._app: FastAPI | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._start_time = datetime.now()
        self._stats = {
            "total_messages": 0,
            "total_tool_calls": 0,
            "channels": {},
        }

    def _build_app(self) -> FastAPI:
        app = FastAPI(title="TomatoCat Dashboard", version="1.0.0")

        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        @app.get("/api/health")
        async def health():
            return {"status": "ok", "timestamp": datetime.now().isoformat()}

        @app.get("/api/status")
        async def get_status():
            uptime = datetime.now() - self._start_time
            hours, remainder = divmod(int(uptime.total_seconds()), 3600)
            minutes, seconds = divmod(remainder, 60)
            uptime_str = f"{hours}h {minutes}m {seconds}s"

            memory_count = 0
            skill_count = 0
            scheduler_count = 0

            if self.memory and hasattr(self.memory, "describe"):
                try:
                    desc = self.memory.describe()
                except Exception:
                    pass

            if self.skills_loader:
                try:
                    skill_count = len(self.skills_loader.list_skills())
                except Exception:
                    pass

            if self.scheduler and hasattr(self.scheduler, "list_jobs"):
                try:
                    scheduler_count = len(self.scheduler.list_jobs())
                except Exception:
                    pass

            return {
                "uptime": uptime_str,
                "start_time": self._start_time.isoformat(),
                "workspace": str(self.workspace),
                "total_messages": self._stats["total_messages"],
                "total_tool_calls": self._stats["total_tool_calls"],
                "memory_count": memory_count,
                "skill_count": skill_count,
                "scheduler_count": scheduler_count,
            }

        @app.get("/api/memory")
        async def get_memory(q: str = "", memory_type: str = "", page: int = 1, page_size: int = 20):
            if not self.memory or not hasattr(self.memory, "list_items_for_dashboard"):
                return {"items": [], "total": 0}

            try:
                items, total = self.memory.list_items_for_dashboard(
                    q=q,
                    memory_type=memory_type,
                    page=page,
                    page_size=page_size,
                )
                return {"items": items, "total": total}
            except Exception as e:
                logger.error(f"[dashboard] 获取记忆列表失败: {e}")
                return {"items": [], "total": 0}

        @app.delete("/api/memory/{item_id}")
        async def delete_memory(item_id: str):
            if not self.memory or not hasattr(self.memory, "delete_item"):
                raise HTTPException(status_code=501, detail="记忆管理功能不可用")

            try:
                result = self.memory.delete_item(item_id)
                if result:
                    return {"success": True}
                raise HTTPException(status_code=404, detail="记忆项不存在")
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"[dashboard] 删除记忆失败: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @app.get("/api/skills")
        async def get_skills():
            if not self.skills_loader:
                return {"skills": [], "total": 0}

            try:
                skills = self.skills_loader.list_skills(filter_unavailable=False)
                result = []
                for s in skills:
                    meta = self.skills_loader.get_skill_metadata(s["name"]) or {}
                    result.append({
                        "name": s["name"],
                        "description": meta.get("description", s["name"]),
                        "source": s["source"],
                        "path": s["path"],
                        "always": meta.get("always", False),
                    })
                return {"skills": result, "total": len(result)}
            except Exception as e:
                logger.error(f"[dashboard] 获取技能列表失败: {e}")
                return {"skills": [], "total": 0}

        @app.get("/api/skills/{skill_name}")
        async def get_skill_detail(skill_name: str):
            if not self.skills_loader:
                raise HTTPException(status_code=501, detail="技能功能不可用")

            try:
                content = self.skills_loader.load_skill(skill_name)
                if not content:
                    raise HTTPException(status_code=404, detail="技能不存在")
                meta = self.skills_loader.get_skill_metadata(skill_name) or {}
                return {
                    "name": skill_name,
                    "content": content,
                    "metadata": meta,
                }
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"[dashboard] 获取技能详情失败: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @app.get("/api/scheduler")
        async def get_scheduler():
            if not self.scheduler or not hasattr(self.scheduler, "list_jobs"):
                return {"jobs": [], "total": 0}

            try:
                jobs = self.scheduler.list_jobs()
                job_list = []
                for job in jobs:
                    job_list.append({
                        "id": job.id if hasattr(job, "id") else str(job),
                        "name": job.name if hasattr(job, "name") else "",
                        "trigger": job.trigger if hasattr(job, "trigger") else "",
                        "mode": job.mode if hasattr(job, "mode") else "",
                        "fire_at": job.fire_at.isoformat() if hasattr(job, "fire_at") and job.fire_at else "",
                        "run_count": job.run_count if hasattr(job, "run_count") else 0,
                    })
                return {"jobs": job_list, "total": len(job_list)}
            except Exception as e:
                logger.error(f"[dashboard] 获取定时任务失败: {e}")
                return {"jobs": [], "total": 0}

        @app.get("/api/files/list")
        async def list_files(path: str = ""):
            try:
                target = self.workspace / path if path else self.workspace
                if not str(target.resolve()).startswith(str(self.workspace.resolve())):
                    raise HTTPException(status_code=403, detail="禁止访问工作区外的路径")

                if not target.exists():
                    raise HTTPException(status_code=404, detail="路径不存在")

                items = []
                for item in sorted(target.iterdir()):
                    items.append({
                        "name": item.name,
                        "is_dir": item.is_dir(),
                        "size": item.stat().st_size if item.is_file() else 0,
                        "modified": datetime.fromtimestamp(item.stat().st_mtime).isoformat(),
                    })
                return {"items": items, "path": str(path)}
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"[dashboard] 文件列表失败: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @app.get("/api/files/read")
        async def read_file(path: str):
            try:
                target = self.workspace / path
                if not str(target.resolve()).startswith(str(self.workspace.resolve())):
                    raise HTTPException(status_code=403, detail="禁止访问工作区外的路径")

                if not target.exists() or not target.is_file():
                    raise HTTPException(status_code=404, detail="文件不存在")

                content = target.read_text(encoding="utf-8", errors="replace")
                return {"content": content, "path": str(path)}
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"[dashboard] 读取文件失败: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @app.get("/api/checkpoints")
        async def get_checkpoints():
            checkpoint_dir = self.workspace / "checkpoints"
            state_file = checkpoint_dir / "checkpoints.json"

            if not state_file.exists():
                return {"checkpoints": [], "total": 0}

            try:
                data = json.loads(state_file.read_text(encoding="utf-8"))
                items = list(data.get("items", {}).values())
                items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
                return {"checkpoints": items, "total": len(items)}
            except Exception as e:
                logger.error(f"[dashboard] 获取检查点失败: {e}")
                return {"checkpoints": [], "total": 0}

        @app.get("/")
        async def index():
            frontend_dir = Path(__file__).parent.parent / "frontend" / "dist"
            index_path = frontend_dir / "index.html"
            if index_path.exists():
                return FileResponse(index_path)
            return JSONResponse({
                "message": "TomatoCat Dashboard API",
                "docs": "/docs",
            })

        return app

    def start(self) -> None:
        if self._running:
            return

        self._app = self._build_app()

        frontend_dir = Path(__file__).parent.parent / "frontend" / "dist"
        if frontend_dir.exists():
            self._app.mount("/assets", StaticFiles(directory=str(frontend_dir / "assets")), name="assets")

        def _run():
            import uvicorn
            self._running = True
            config = uvicorn.Config(
                app=self._app,
                host=self.host,
                port=self.port,
                log_level="warning",
            )
            server = uvicorn.Server(config)
            server.run()

        self._thread = threading.Thread(target=_run, daemon=True, name="dashboard")
        self._thread.start()
        logger.info(f"[dashboard] 仪表板已启动: http://{self.host}:{self.port}")

    def stop(self) -> None:
        self._running = False

    def record_message(self) -> None:
        self._stats["total_messages"] += 1

    def record_tool_call(self) -> None:
        self._stats["total_tool_calls"] += 1