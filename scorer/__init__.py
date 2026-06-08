import azure.functions as func
from azure.functions._http_asgi import AsgiMiddleware

# Import the FastAPI ASGI app from the local module
# Use a relative import so this works when deployed as a package
from .app import app as fastapi_app


# Wrap FastAPI app with ASGI middleware so the Functions runtime can serve it
asgi_middleware = AsgiMiddleware(fastapi_app)


async def main(req: func.HttpRequest, context: func.Context) -> func.HttpResponse:
    """Azure Functions entry point that forwards HTTP requests to FastAPI via ASGI middleware."""
    return await asgi_middleware.handle_async(req, context)
