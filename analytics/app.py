import logging
import os
import json

from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timedelta
from flask import jsonify, request, Response
from sqlalchemy import and_, text
from random import randint

from config import app, db


port_number = int(os.environ.get("APP_PORT", 5153))


@app.route("/health_check")
def health_check():
    return "ok"

@app.route("/readiness_check")
def readiness_check():
    try:
        # Run a simple raw SQL query to confirm DB connectivity
        db.session.execute("SELECT 1 FROM tokens LIMIT 1;")
    except Exception as e:
        app.logger.error(e)
        return "failed", 500
    else:
        return "ok", 200


def get_daily_visits():
    with app.app_context():
        try:
            result = db.session.execute(text("""
            SELECT Date(created_at) AS date,
                   Count(*)         AS visits
            FROM   tokens
            WHERE  used_at IS NOT NULL
            GROUP  BY Date(created_at)
            """))

            response = {}
            for row in result:
                response[str(row[0])] = int(row[1])  # force int

            app.logger.info(f"get_daily_visits response: {response}")
            return response
        except Exception as e:
            app.logger.error(f"Error in get_daily_visits: {e}")
            return {"error": str(e)}


@app.route("/api/reports/daily_usage")
def daily_visits():
    data = get_daily_visits()
    # use Response/json.dumps to force serialization
    return Response(json.dumps(data), mimetype="application/json")


@app.route("/api/reports/user_visits", methods=["GET"])
def all_user_visits():
    result = db.session.execute(text("""
    SELECT t.user_id,
           t.visits,
           users.joined_at
    FROM   (SELECT tokens.user_id,
                   Count(*) AS visits
            FROM   tokens
            GROUP  BY user_id) AS t
           LEFT JOIN users
                  ON t.user_id = users.id;
    """))

    response = {}
    for row in result:
        response[row[0]] = {
            "visits": int(row[1]),
            "joined_at": str(row[2])
        }
    
    return jsonify(response)


scheduler = BackgroundScheduler()
job = scheduler.add_job(get_daily_visits, 'interval', seconds=30)
scheduler.start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=port_number)