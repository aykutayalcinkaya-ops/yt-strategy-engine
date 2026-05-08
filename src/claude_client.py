from __future__ import annotations

import anthropic
from tenacity import retry, stop_after_attempt, wait_exponential

from config import config

# System prompt encoding the three core content principles
SYSTEM_PROMPT = """\
Sen, YouTube içerik stratejisi ve teknik senaryo üretimi konusunda uzmanlaşmış bir asistansın.

Çalışmalarında üç temel prensibe bağlısın:

## 1. Teknik Doğruluk
Her bilgi, istatistik ve öneri doğrulanmış, güncel ve pratik olarak uygulanabilir olmalıdır.
Kaynak göster, somut verilerle destekle ve asla spekülasyon yapma.

## 2. Objektif Analiz
Verileri önyargısız, çok perspektiften değerlendir. Hem güçlü yönleri hem de gelişim
alanlarını dengeli biçimde ortaya koy. Kişisel görüşleri veri destekli çıkarımlardan ayır.

## 3. Yapıcı Ton (Praising)
İzleyicileri motive eden, başarıları kutlayan ve ilham veren bir dil kullan.
Eleştirileri yapıcı çerçevele; her zorluğu bir büyüme fırsatı olarak sun.
"""


class ClaudeClient:
    def __init__(self):
        self._client = anthropic.Anthropic(api_key=config.anthropic_api_key)
        self._model = config.claude_model

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=30))
    def complete(
        self,
        prompt: str,
        system: str = SYSTEM_PROMPT,
        max_tokens: int = None,
        temperature: float = 1.0,
    ) -> str:
        response = self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens or config.max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=30))
    def complete_with_cache(
        self,
        prompt: str,
        cached_context: str,
        system: str = SYSTEM_PROMPT,
        max_tokens: int = None,
    ) -> str:
        """Uses prompt caching for large, repeated context blocks."""
        response = self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens or config.max_tokens,
            system=[
                {"type": "text", "text": system},
                {
                    "type": "text",
                    "text": cached_context,
                    "cache_control": {"type": "ephemeral"},
                },
            ],
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text

    def stream_complete(
        self,
        prompt: str,
        system: str = SYSTEM_PROMPT,
        max_tokens: int = None,
    ):
        """Yields text chunks for streaming output."""
        with self._client.messages.stream(
            model=self._model,
            max_tokens=max_tokens or config.max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            for text in stream.text_stream:
                yield text
