"""Lambda entrypoint: adapt FastAPI ASGI app for AWS Lambda via Mangum."""

from mangum import Mangum

from app.main import app

handler = Mangum(app)
