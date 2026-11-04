"""文件职责：预留 CSV 文件上传入口，当前由陈咏忻负责实现。

在 CSV 模板完成前，保留明确的 501 响应，避免前端把“路由不存在”误判为网络故障。
实现时应完成文件类型、大小、编码和内容校验，然后调用统一任务创建服务。
"""

from fastapi import APIRouter, HTTPException, status


router = APIRouter()


@router.post("/tasks/csv", status_code=status.HTTP_501_NOT_IMPLEMENTED)
def create_csv_task() -> None:
    """占位接口：明确告知调用方 CSV 功能尚未接入。"""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail={
            "code": "CSV_ROUTE_NOT_IMPLEMENTED",
            "message": "CSV upload is owned by 陈咏忻 and is not implemented yet",
        },
    )
