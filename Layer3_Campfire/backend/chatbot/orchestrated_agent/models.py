from pydantic import BaseModel


class ScopeDetectionResult(BaseModel):
    in_scope: bool
    reason: str


class SearchQueries(BaseModel):
    historical_query: str | None
    book_query: str | None


class RAGAnswer(BaseModel):
    text: str
    selected_sources: list[int]


class FollowUpQuestions(BaseModel):
    questions: list[str]


class FactCheckIssue(BaseModel):
    claim: str
    explanation: str


class FactCheckResult(BaseModel):
    flagged: bool
    issues: list[FactCheckIssue]
