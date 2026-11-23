import sys

import uvicorn
from app.main import app

if __name__ == "__main__":
    loop = "asyncio:SelectorEventLoop" if sys.platform == "win32" else "auto"
    uvicorn.run(app, host="127.0.0.1", port=8000, loop=loop)