from pydantic import BaseModel


class StartDebateResponse(BaseModel):
    session_id: str
    ws_url: str
    controller_token: str | None = None
    reconnect_token: str | None = None
    observer_join_code: str | None = None


class JoinSessionResponse(BaseModel):
    session_id: str
    ws_url: str
    observer_token: str


class ReconnectSessionResponse(BaseModel):
    session_id: str
    ws_url: str
    controller_token: str
    reconnect_token: str
