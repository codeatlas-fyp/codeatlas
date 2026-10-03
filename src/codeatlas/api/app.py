from fastapi import FastAPI

from codeatlas import __version__

app = FastAPI(title="CodeAtlas", version=__version__)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}
