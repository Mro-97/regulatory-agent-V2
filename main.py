import uvicorn
from src.api import app
from config import cfg

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
