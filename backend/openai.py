"""One OpenAI Responses API attempt; retries belong to the application."""

import ssl

import httpx
import truststore
from openai import (APIConnectionError, APIResponseValidationError, APIStatusError,
                    AsyncOpenAI)

from .providers import ProviderError


class OpenAITransport:
    async def generate(self, *, key: str, model: str, prompt: str, source: str,
                       feedback: str, timeout: float, previous_response: str = '') -> str:
        messages = [{'role': 'user', 'content': source}]
        if feedback:
            if previous_response:
                messages.append({'role': 'assistant', 'content': previous_response})
            messages.append({'role': 'user', 'content': feedback})

        async def authorize(request: httpx.Request):
            # SDK environment headers must not replace the snapshotted job key.
            request.headers['Authorization'] = f'Bearer {key}'

        try:
            async with AsyncOpenAI(
                api_key=key, base_url='https://api.openai.com/v1', max_retries=0, timeout=timeout,
                http_client=httpx.AsyncClient(
                    verify=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT), timeout=timeout,
                    event_hooks={'request': [authorize]}),
            ) as client:
                result = await client.responses.create(
                    model=model, instructions=prompt, input=messages,
                    reasoning={'effort': 'low'}, max_output_tokens=8192, store=False,
                )
            for item in result.output or []:
                if item.type == 'message' and any(part.type == 'refusal' for part in item.content):
                    raise ProviderError('OpenAI가 응답 생성을 거부했습니다. 입력 내용을 확인하세요.')
            if result.error is not None:
                retryable = result.error.code in {'server_error', 'rate_limit_exceeded'}
                raise ProviderError('OpenAI 응답 생성 중 오류가 발생했습니다.', retryable=retryable)
            if result.status == 'incomplete':
                reason = getattr(result.incomplete_details, 'reason', None)
                if reason == 'max_output_tokens':
                    raise ProviderError('OpenAI 출력 토큰 한도에 도달해 응답이 완료되지 않았습니다.', retryable=True)
                raise ProviderError('OpenAI가 응답 생성을 완료하지 못했습니다. 입력 내용을 확인하세요.')
            if result.status != 'completed':
                raise ProviderError('OpenAI가 응답 생성을 완료하지 못했습니다.')
            text = result.output_text
            if not text or not text.strip():
                raise ProviderError('OpenAI가 빈 응답을 반환했습니다.', retryable=True)
            return text
        except APIStatusError as exc:
            code = exc.status_code
            action = 'stop'
            if code in (401, 403):
                message = 'OpenAI API 키 인증 또는 접근 권한 오류입니다.'
            elif code == 404:
                action = 'next_model'
                message = f'모델 {model}을 찾을 수 없거나 이 키에서 사용할 수 없습니다.'
            elif code == 429:
                action = 'next_model'
                message = 'OpenAI API 호출 한도 또는 사용량 한도를 초과했습니다.'
            elif code == 408 or 500 <= code < 600:
                action = 'next_model'
                message = f'OpenAI 서비스 오류입니다 (HTTP {code}).'
            else:
                message = f'OpenAI 요청 설정을 확인하세요 (HTTP {code}).'
            raise ProviderError(message, action=action) from None
        except (APIConnectionError, httpx.TransportError, TimeoutError, ConnectionError):
            raise ProviderError('OpenAI 연결이 끊겼거나 제한 시간 내 응답하지 않았습니다.', retryable=True) from None
        except APIResponseValidationError:
            raise ProviderError('OpenAI 응답 형식을 읽지 못했습니다.', retryable=True) from None
