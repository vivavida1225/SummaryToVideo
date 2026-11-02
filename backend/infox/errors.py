class InfoXError(Exception):
    """Safe diagnostics: never retain the response, request, URL, or remote text."""

    def __init__(self, code: str, status_code: int | None = None):
        self.code = code
        self.status_code = status_code
        super().__init__(code + (f' (HTTP {status_code})' if status_code is not None else ''))


def check_status(status: int) -> None:
    if 200 <= status < 300:
        return
    code = ('auth_expired' if status == 401 else 'permission_denied' if status == 403
            else 'rate_limited' if status == 429 else 'server_error' if status >= 500 else 'http_error')
    raise InfoXError(code, status)
