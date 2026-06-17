from dataclasses import dataclass


@dataclass
class AICommentary:
    review: str          # 오늘 매매에 대한 평가
    strategy: str        # 내일 전략 제안
    provider: str = ""   # 'gemini' | 'ollama'
    model: str = ""
