"""Provider boundary: one HTTP attempt, safe errors, no implicit retries."""

import json
import ssl
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx
import truststore
from google import genai
from google.genai import errors, types


class ProviderError(Exception):
    def __init__(self, message: str, *, retryable: bool, retry_after: float | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.retry_after = retry_after


def _retry_after(exc: errors.APIError) -> float | None:
    response = getattr(exc, 'response', None)
    value = response.headers.get('retry-after') if response is not None and hasattr(response, 'headers') else None
    if value:
        try:
            return max(0.0, float(value))
        except ValueError:
            try:
                return max(0.0, (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())
            except (ValueError, TypeError):
                pass
    details = getattr(exc, 'details', {})
    if isinstance(details, dict):
        for item in details.get('error', details).get('details', []):
            if isinstance(item, dict) and 'retryDelay' in item:
                try:
                    return max(0.0, float(str(item['retryDelay']).removesuffix('s')))
                except ValueError:
                    pass
    return None


class GeminiTransport:
    async def generate(self, *, key: str, model: str, prompt: str, source: str,
                       feedback: str, timeout: float, previous_response: str = '') -> str:
        try:
            async with genai.Client(
                api_key=key,
                http_options=types.HttpOptions(timeout=int(timeout * 1000),
                    retry_options=types.HttpRetryOptions(attempts=1),
                    client_args={'verify': truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)},
                    async_client_args={'verify': truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)}),
            ).aio as client:
                contents = source
                if feedback:
                    contents = [types.Content(role='user', parts=[types.Part(text=source)])]
                    if previous_response:
                        contents.append(types.Content(role='model', parts=[types.Part(text=previous_response)]))
                    contents.append(types.Content(role='user', parts=[types.Part(text=feedback)]))
                result = await client.models.generate_content(
                    model=model,
                    contents=contents,
                    config=types.GenerateContentConfig(system_instruction=prompt, temperature=0.2,
                        max_output_tokens=4096,
                        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)),
                )
            block = getattr(getattr(result, 'prompt_feedback', None), 'block_reason', None)
            if block and str(getattr(block, 'value', block)) not in ('BLOCK_REASON_UNSPECIFIED', '0'):
                raise ProviderError('Gemini가 입력 생성을 차단했습니다. 입력 내용을 확인하세요.', retryable=False)
            for candidate in result.candidates or []:
                reason = str(getattr(candidate.finish_reason, 'value', candidate.finish_reason))
                if reason in {'SAFETY', 'BLOCKLIST', 'PROHIBITED_CONTENT', 'SPII', 'RECITATION', 'IMAGE_SAFETY'}:
                    raise ProviderError('Gemini가 응답 생성을 차단했습니다. 입력 내용을 확인하세요.', retryable=False)
            text = result.text
            if not text or not text.strip():
                raise ProviderError('Gemini가 빈 응답을 반환했습니다.', retryable=True)
            return text
        except errors.APIError as exc:
            code = exc.code
            key_error = 'API_KEY_INVALID' in json.dumps(getattr(exc, 'details', {}), default=str)
            retryable = key_error or code in (401, 403, 408, 429) or (code is not None and 500 <= code < 600)
            if key_error or code in (401, 403):
                message = 'API 키 인증 또는 접근 권한 오류입니다.'
            elif code == 429:
                message = 'API 호출 한도를 초과했습니다. 같은 프로젝트의 키는 한도를 공유합니다.'
            elif code == 404:
                message = f'모델 {model}을 찾을 수 없거나 이 키에서 사용할 수 없습니다.'
            elif code == 400:
                message = 'Gemini 요청 설정이 올바르지 않습니다. 모델과 입력을 확인하세요.'
            else:
                message = f'Gemini 서비스 오류입니다 (HTTP {code}).'
            raise ProviderError(message, retryable=retryable, retry_after=_retry_after(exc)) from None
        except (httpx.TimeoutException, httpx.TransportError, TimeoutError, ConnectionError):
            raise ProviderError('Gemini 연결이 끊겼거나 제한 시간 내 응답하지 않았습니다.', retryable=True) from None
