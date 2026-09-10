from typing import List, Literal, Optional

from pydantic import BaseModel

ChatMode = Literal["discovery", "research", "teachers", "students"]


class ChatRequest(BaseModel):
    message: str
    chat_id: Optional[str] = None
    user_id: Optional[str] = None
    mode: Optional[ChatMode] = None


class ChatResponseBase(BaseModel):
    chat_id: str
    user_id: str


class ChatProgressResponse(ChatResponseBase):
    type: Literal["progress"]
    progress: str


class ChatDeltaResponse(ChatResponseBase):
    type: Literal["delta"]
    delta: str


class ChatMessageItem(BaseModel):
    role: Literal["user", "assistant"]
    text: str
    timestamp: float
    citations: Optional[List[dict]] = None
    # Backend-only debug fields (not exposed via REST — see ChatMessageItemPublic).
    search_queries: Optional[dict] = None
    search_results: Optional[List[dict]] = None
    fact_check: Optional[dict] = None
    follow_up_questions: Optional[List[str]] = None


class ChatMessageItemPublic(BaseModel):
    """Public projection of ChatMessageItem returned by the chat-history REST endpoint.

    Excludes backend-only debug fields (search_queries, search_results, fact_check,
    follow_up_questions) intentionally.
    """

    role: Literal["user", "assistant"]
    text: str
    timestamp: float
    citations: Optional[List[dict]] = None


class ChatFinalResponse(ChatResponseBase):
    type: Literal["final"]
    text: str
    citations: List[dict]


class ChatFollowUpResponse(ChatResponseBase):
    type: Literal["extra"]
    follow_up_questions: List[str]


class ChatErrorResponse(ChatResponseBase):
    type: Literal["error"]
    error: str


class ChatFactCheckResponse(ChatResponseBase):
    type: Literal["fact_check"]
    flagged: bool
    issues: List[dict]


class ChatCostResponse(ChatResponseBase):
    type: Literal["cost"]
    total_usd: float
    prompt_tokens: int
    cached_tokens: int
    completion_tokens: int
    embedding_tokens: int = 0
    embedding_cost_usd: float = 0.0


class ArtifactRequest(BaseModel):
    index: Literal["letter", "book"]
    id: str


class ChatSummary(BaseModel):
    chat_id: str
    mode: ChatMode
