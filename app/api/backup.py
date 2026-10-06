"""备份与恢复接口：导出 zip、导入恢复、清空数据（PD.md 第 7 节）。"""
import os
import tempfile
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from ..services import backup

router = APIRouter()


@router.get("/backup/list")
def list_backups():
    return {"backups": backup.list_backups(), "meta": backup.read_export_meta()}


@router.post("/backup/export")
def export_backup():
    _fd, tmp_name = tempfile.mkstemp(suffix=".zip")
    os.close(_fd)  # 立即关闭句柄，否则 Windows 上 unlink 报 WinError 32（审查F8）
    tmp = Path(tmp_name)
    backup.export_backup(tmp)
    return FileResponse(
        tmp,
        filename=f"workbench-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip",
        background=BackgroundTask(_cleanup, tmp),
    )


def _cleanup(path: Path):
    try:
        path.unlink()
    except OSError:
        pass


@router.post("/backup/import")
def import_backup(file: UploadFile):
    if not file.filename or not file.filename.endswith(".zip"):
        raise HTTPException(status_code=400, detail="请选择 .zip 备份文件")
    _fd, tmp_name = tempfile.mkstemp(suffix=".zip")
    os.close(_fd)  # 立即关闭句柄，否则 Windows 上 unlink 报 WinError 32（审查F8）
    tmp = Path(tmp_name)
    try:
        tmp.write_bytes(file.file.read())
        result = backup.import_backup(tmp)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
    result["restart_required"] = True
    return result


@router.post("/data/clear")
def clear_data(confirm: dict):
    if not confirm.get("confirm"):
        raise HTTPException(status_code=400, detail="缺少二次确认")
    return backup.clear_all_data()
