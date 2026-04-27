from pydantic import BaseModel


class DecayResult(BaseModel):
    scanned: int = 0
    decayed: int = 0
    triggered_push_candidates: int = 0


class PushL1Result(BaseModel):
    pushed: int = 0
    push_cards: list[dict] = []
