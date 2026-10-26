"""啟動 Web 程序：python -m backend"""

import uvicorn

from backend.config import http_host, http_port

if __name__ == "__main__":
    uvicorn.run(
        "backend.app:create_app",
        factory=True,
        host=http_host(),
        port=http_port(),
        log_level="warning",
    )
