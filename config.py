import os
from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()


class Config(BaseModel):
    # API Keys
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    youtube_api_key: str = os.getenv("YOUTUBE_API_KEY", "")

    # Claude model settings
    claude_model: str = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
    max_tokens: int = int(os.getenv("MAX_TOKENS", "8192"))

    # Scenario settings
    scenario_target_chars: int = 12000
    scenario_min_chars: int = 11000

    # YouTube API settings
    youtube_max_results: int = int(os.getenv("YOUTUBE_MAX_RESULTS", "50"))
    youtube_region_code: str = os.getenv("YOUTUBE_REGION_CODE", "TR")
    youtube_language: str = os.getenv("YOUTUBE_LANGUAGE", "tr")

    # Content principles
    principles: dict = {
        "teknik_dogruluk": "Her teknik bilgi doğrulanmış, güncel ve uygulanabilir olmalıdır.",
        "objektif_analiz": "Veriye dayalı, önyargısız ve çok perspektifli bir değerlendirme sunulmalıdır.",
        "yapici_ton": "İzleyiciyi motive eden, başarıyı kutlayan ve ilham veren bir dil kullanılmalıdır.",
    }

    # Output settings
    output_dir: str = os.getenv("OUTPUT_DIR", "outputs")

    def validate_keys(self) -> bool:
        return bool(self.anthropic_api_key and self.youtube_api_key)


config = Config()
