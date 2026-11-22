"""Wayfarer: an AirplaneMode app. Travel trivia from Wikivoyage, single player.

The questions are built offline from a Wikivoyage ZIM (bank/build.py) and ship in the image as
bank/questions.json.gz. Rounds live in memory; the server holds the answers and does the
scoring, so the leaderboard only takes scores it worked out itself.

Pages (relative URLs only: the app runs behind a path prefix, /wayfarer/ on the box):
  /                              the game
API:
  GET  /health                   {"ok": true, "questions": n}
  GET  /api/meta                 categories, regions, source ZIM, today's daily date
  POST /api/rounds               {mode, cat?, region?, timed, seen: [ids]} -> a new round
  POST /api/rounds/{id}/next     the current question (no answer in it); starts its clock
  POST /api/rounds/{id}/answer   {n, choice: 0-3 | null} -> right or wrong, points, the answer
  GET  /api/rounds/{id}          end-of-round summary (score, accuracy, per category, share text)
  POST /api/rounds/{id}/submit   {name} -> put a finished round on its leaderboard (once)
  GET  /api/leaderboard?board=   top 10 of one board (quick, endless, daily:<date>, cat:<id>, region:<c>)
  GET  /api/leaderboards         the boards for the home screen (quick, endless, today's daily)
"""
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import quiz
from store import Store, clean_name

log = logging.getLogger("wayfarer")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

HERE = Path(__file__).parent
BANK_PATH = Path(os.getenv("BANK_PATH", HERE / "bank" / "questions.json.gz"))
DATA_DIR = os.getenv("DATA_DIR", "/data")

bank = quiz.Bank.load(BANK_PATH)
store = Store(DATA_DIR)
rounds = quiz.Rounds(ttl=float(os.getenv("ROUND_TTL", 3 * 3600)), cap=int(os.getenv("MAX_ROUNDS", 500)))
clock = time.monotonic     # tests replace this to move time along

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
log.info("bank: %d questions from %s (%s); leaderboard %s", len(bank.questions), bank.source.get("zim"),
         bank.source.get("date"), "in " + str(store.path) if store.persistent else "in memory only")


def today():
    """The box's local date (set TZ on the container to move midnight)."""
    return datetime.now().astimezone().strftime("%Y-%m-%d")


@app.exception_handler(quiz.RoundError)
async def round_error(_request: Request, exc: quiz.RoundError):
    return JSONResponse({"detail": exc.message}, status_code=exc.status)


class NewRound(BaseModel):
    mode: str = "quick"
    cat: Optional[str] = Field(None, max_length=40)
    region: Optional[str] = Field(None, max_length=40)
    timed: bool = True
    seen: list[str] = Field(default_factory=list, max_length=quiz.SEEN_MAX * 2)


class Answer(BaseModel):
    n: int
    choice: Optional[int] = None


class Submit(BaseModel):
    name: str = Field(..., max_length=64)


@app.get("/health")
async def health():
    return {"ok": True, "questions": len(bank.questions)}


@app.get("/api/meta")
async def meta():
    return {**bank.meta(), "daily": today(), "round_length": quiz.ROUND_LENGTH, "time_limit": quiz.TIME_LIMIT,
            "strikes": quiz.ENDLESS_STRIKES, "leaderboard_saved": store.persistent}


@app.post("/api/rounds")
async def new_round(body: NewRound):
    seen = [s[:16] for s in body.seen if isinstance(s, str)]
    rnd = quiz.Round(bank, body.mode, cat=body.cat, region=body.region, timed=body.timed, seen=seen,
                     date=today(), now=clock())
    rounds.add(rnd, clock())
    return {"id": rnd.id, "mode": rnd.mode, "board": rnd.board, "label": rnd.label, "timed": rnd.timed,
            "total": rnd.total, "limit": quiz.TIME_LIMIT, "strikes_max": quiz.ENDLESS_STRIKES}


@app.post("/api/rounds/{rid}/next")
async def next_question(rid: str):
    return rounds.get(rid, clock()).current(clock())


@app.post("/api/rounds/{rid}/answer")
async def answer(rid: str, body: Answer):
    return rounds.get(rid, clock()).answer(body.n, body.choice, clock())


@app.get("/api/rounds/{rid}")
async def summary(rid: str):
    return rounds.get(rid, clock()).summary()


@app.post("/api/rounds/{rid}/submit")
async def submit(rid: str, body: Submit):
    rnd = rounds.get(rid, clock())
    s = rnd.summary()
    if not rnd.done:
        raise HTTPException(409, "Finish the round first.")
    if rnd.submitted:
        raise HTTPException(409, "This round is already on the board.")
    if not s["answered"]:
        raise HTTPException(409, "Nothing to put on the board.")
    name = clean_name(body.name)
    if not name:
        raise HTTPException(400, "Type a name for the board.")
    rank = store.add(rnd.board, name, rnd.score, s["right"], s["answered"], rnd.best_streak)
    rnd.submitted = True
    return {"board": rnd.board, "label": rnd.label, "rank": rank, "name": name, "rows": store.top(rnd.board)}


def valid_board(board):
    kind, _, arg = board.partition(":")
    return (board in ("quick", "endless") or (kind == "daily" and len(arg) == 10)
            or (kind == "cat" and arg in bank.cat_names) or (kind == "region" and arg in bank.continents))


@app.get("/api/leaderboard")
async def leaderboard(board: str = "quick"):
    if not valid_board(board):
        raise HTTPException(404, "No such board.")
    return {"board": board, "label": quiz.board_label(board, bank), "rows": store.top(board)}


@app.get("/api/leaderboards")
async def leaderboards():
    boards = ["quick", f"daily:{today()}", "endless"]
    return {"boards": [{"board": b, "label": quiz.board_label(b, bank), "rows": store.top(b, 5)} for b in boards]}


app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")


@app.get("/")
async def index():
    return FileResponse(HERE / "static" / "index.html", headers={"Cache-Control": "no-cache"})
