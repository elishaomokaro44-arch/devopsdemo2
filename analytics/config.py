import logging
import os
import sys

from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.engine import URL

db_username = os.environ["DB_USERNAME"]
db_password = os.environ["DB_PASSWORD"]
db_host = os.environ.get("DB_HOST", "127.0.0.1")
db_port = os.environ.get("DB_PORT", "5432")
db_name = os.environ.get("DB_NAME", "postgres")

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = URL.create(
    "postgresql",
    username=db_username,
    password=db_password,
    host=db_host,
    port=int(db_port),
    database=db_name,
)
db = SQLAlchemy(app)

# Log to stdout explicitly. When the CloudWatch agent injects OpenTelemetry it
# puts its own handler on the root logger, so Flask skips its default stream
# handler and application logs never reach the container's stdout.
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s in %(module)s: %(message)s"))
app.logger.addHandler(handler)
logging.getLogger("werkzeug").addHandler(handler)
logging.getLogger("werkzeug").setLevel(logging.INFO)

app.logger.setLevel(logging.DEBUG)
