# function_app.py  (Azure Functions v2 programming model)
import os
from azure.functions import AsgiFunctionApp

# load your app; it already loads .env locally, but Functions will
# inject settings via environment variables (Application Settings)
from scorer.app import app as fastapi_app

# Wrap FastAPI into a Functions app
app = AsgiFunctionApp(app=fastapi_app)
