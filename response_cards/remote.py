"""Narrow outbound-only API; never expose the private Faculty listener."""
from dataclasses import dataclass, field
import json
import os
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from werkzeug.exceptions import ServiceUnavailable


@dataclass
class RemoteConfig:
    url: str = ''
    secret: str = field(default='', repr=False)

    @classmethod
    def environment(cls):
        return cls(os.environ.get('FACULTY_RESPONSE_CARDS_URL', '').rstrip('/'),
                   os.environ.get('FACULTY_RESPONSE_CARDS_SECRET', ''))

    @property
    def enabled(self):
        try:
            parts = urlsplit(self.url)
            return bool(parts.scheme == 'https' and parts.hostname and not parts.path
                        and not parts.username and not parts.password and not parts.query
                        and not parts.fragment and len(self.secret) >= 32)
        except ValueError:
            return False


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class RemoteClient:
    def __init__(self, config=None):
        self.config = config or RemoteConfig.environment()

    def call(self, method, path, data=None):
        if not self.config.enabled:
            raise ServiceUnavailable('Configure the Response Cards public service before publishing or syncing.')
        if not path.startswith('/faculty-api/'):
            raise ValueError('Unknown Response Cards service operation.')
        body = None if data is None else json.dumps(data, ensure_ascii=False, allow_nan=False).encode()
        request = urllib.request.Request(self.config.url + path, data=body, method=method,
            headers={'Authorization': 'Bearer ' + self.config.secret, 'Content-Type': 'application/json',
                     'User-Agent': 'Faculty-Response-Cards/1.0'})
        try:
            with urllib.request.build_opener(NoRedirect).open(request, timeout=25) as response:
                content = response.read(4 * 1024 * 1024 + 1)
                if len(content) > 4 * 1024 * 1024:
                    raise ValueError('Response too large')
                result = json.loads(content)
                if not isinstance(result, dict):
                    raise ValueError('Unexpected response')
                return result
        except urllib.error.HTTPError as error:
            # Never echo remote payloads or URLs: both may contain private data.
            raise ServiceUnavailable(f'Response Cards service returned HTTP {error.code}. The operation can be retried safely.') from None
        except (OSError, ValueError):
            raise ServiceUnavailable('Response Cards service could not be reached or returned invalid data. The operation can be retried safely.') from None
