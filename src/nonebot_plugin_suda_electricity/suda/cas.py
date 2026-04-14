"""封装苏大统一身份认证（CAS）登录流程。"""

import base64
import binascii
from html.parser import HTMLParser
from typing import Any, Self
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
import orjson
from pydantic import BaseModel, computed_field

from .http_pool import build_http_client

DEFAULT_LOGIN_URL = (
    r"https://auth.suda.edu.cn/cas/login?"
    r"service=https%3A%2F%2Fauth.suda.edu.cn%2Fsso%2Flogin%3Fredirect_uri%3D"
    r"https%253A%252F%252Fauth.suda.edu.cn%252Fsso%252Foauth2%252Fauthorize%253F"
    r"response_type%253Dcode%2526redirect_uri%253Dhttp%253A%252F%252F"
    r"sder.hqglc.suda.edu.cn%252Fsdzndb%252FwebResources%252Fwww%252Findex.html"
    r"%2526client_id%253DMsUWEQGjjKzsgu5NSGrS%2526scope%253Dopenid%26x_client%3Dcas#/"
)
CAPTCHA_API_URL = "https://auth.suda.edu.cn/sso/apis/v2/open/captcha?imageWidth=100"
DEFAULT_TIMEOUT = 15


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def extract_code_from_url(url: str) -> str:
    """从 CAS 跳转地址中提取授权码。

    Args:
        url: 登录完成后返回的最终 URL。

    Returns:
        URL 查询参数中的 ``code``。若不存在则返回空字符串。
    """

    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    return _text(query.get("code", [""])[0])


class LoginPageParser(HTMLParser):
    """解析 CAS 登录页中的隐藏字段和错误提示。"""

    def __init__(self) -> None:
        super().__init__()
        self.inputs: list[dict[str, str]] = []
        self._current_span_id: str | None = None
        self._span_chunks: dict[str, list[str]] = {
            "errorcode": [],
            "errormes": [],
        }

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {key: _text(value) for key, value in attrs}
        if tag == "input":
            self.inputs.append(attr_map)
            return
        if tag == "span" and attr_map.get("id") in self._span_chunks:
            self._current_span_id = attr_map["id"]

    def handle_data(self, data: str) -> None:
        if self._current_span_id is not None:
            self._span_chunks[self._current_span_id].append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "span":
            self._current_span_id = None

    @property
    def spans(self) -> dict[str, str]:
        return {
            key: "".join(chunks).strip()
            for key, chunks in self._span_chunks.items()
            if chunks
        }


class LoginBootstrap(BaseModel):
    """保存登录页解析得到的初始字段。"""

    hidden_inputs: dict[str, str]
    x_random: str = ""
    error_code: str = ""
    error_message: str = ""


def parse_login_bootstrap(html: str) -> LoginBootstrap:
    """解析 CAS 登录页 HTML。

    Args:
        html: 登录页原始 HTML。

    Returns:
        页面中的隐藏表单字段、验证码随机参数和错误提示。
    """

    parser = LoginPageParser()
    parser.feed(html)

    hidden_inputs: dict[str, str] = {}
    x_random = ""
    for input_attrs in parser.inputs:
        name = _text(input_attrs.get("name"))
        input_type = _text(input_attrs.get("type")).lower()
        if name and input_type == "hidden":
            hidden_inputs[name] = _text(input_attrs.get("value"))
        if _text(input_attrs.get("id")) == "x_random":
            x_random = _text(input_attrs.get("value"))

    return LoginBootstrap(
        hidden_inputs=hidden_inputs,
        x_random=x_random,
        error_code=parser.spans.get("errorcode", ""),
        error_message=parser.spans.get("errormes", ""),
    )


class CasLoginState(BaseModel):
    """描述当前登录会话所需的上下文。"""

    login_url: str
    hidden_inputs: dict[str, str]
    captcha_required: bool = False
    captcha_token: str = ""
    captcha_image_bytes: bytes = b""
    x_random: str = ""


class CasCodeResult(BaseModel):
    """表示一次 CAS 登录提交后的结果。"""

    code: str
    final_url: str
    status: int
    error_code: str = ""
    error_message: str = ""
    raw_html_excerpt: str = ""

    @computed_field(return_type=bool)
    @property
    def ok(self) -> bool:
        return bool(self.code)


class CaptchaRequiredError(RuntimeError):
    """表示当前登录流程要求补充验证码。"""

    def __init__(self, state: CasLoginState) -> None:
        super().__init__("captcha required")
        self.state = state


def _build_http_client(timeout: float) -> httpx.AsyncClient:
    """为独立使用的 CAS 客户端创建默认 HTTP Client。"""

    return build_http_client(timeout)


class CasClient:
    """处理 CAS 登录页、验证码和授权码获取。"""

    def __init__(
        self,
        login_url: str = DEFAULT_LOGIN_URL,
        timeout: int = DEFAULT_TIMEOUT,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.login_url = login_url
        self.timeout = timeout
        self._client = client or _build_http_client(timeout)
        self._owns_client = client is None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """关闭当前持有的 HTTP Client。"""

        if self._owns_client:
            await self._client.aclose()

    def reset(self) -> None:
        """清理当前会话中的 Cookie。"""

        self._client.cookies.clear()

    async def get_text(self, url: str) -> tuple[int, str, str]:
        """以文本方式请求一个页面。"""

        response = await self._client.get(
            url,
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        return response.status_code, str(response.url), response.text

    async def get_json(self, url: str) -> tuple[int, str, dict[str, Any]]:
        """请求 JSON 接口并确保返回对象结构。"""

        response = await self._client.get(
            url,
            headers={
                "Accept": "application/json, text/plain, */*",
                "Referer": self.login_url,
            },
        )
        payload = orjson.loads(response.content)
        if not isinstance(payload, dict):
            raise RuntimeError("captcha api returned a non-object payload")
        return response.status_code, str(response.url), payload

    async def post_form(self, url: str, fields: dict[str, str]) -> tuple[int, str, str]:
        """提交表单并返回最终落地页面。"""

        response = await self._client.post(
            url,
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": "https://auth.suda.edu.cn",
                "Referer": url,
            },
            content=urlencode(fields),
        )
        return response.status_code, str(response.url), response.text

    def has_cookie(self, cookie_name: str) -> bool:
        """判断当前会话中是否存在指定 Cookie。"""

        return cookie_name in self._client.cookies

    async def prepare_login(self) -> CasLoginState:
        """预热登录流程并在需要时拉取验证码。"""

        _, final_url, html = await self.get_text(self.login_url)
        bootstrap = parse_login_bootstrap(html)

        captcha_required = self.has_cookie("X_CAPTCHA")
        captcha_token = ""
        captcha_image_bytes = b""
        if captcha_required:
            _, _, captcha_data = await self.get_json(CAPTCHA_API_URL)
            captcha_token = _text(captcha_data.get("token"))
            image_base64 = _text(captcha_data.get("img"))
            if image_base64:
                try:
                    captcha_image_bytes = base64.b64decode(image_base64)
                except binascii.Error as exc:
                    raise RuntimeError("failed to decode captcha image") from exc

        return CasLoginState(
            login_url=final_url,
            hidden_inputs=bootstrap.hidden_inputs,
            captcha_required=captcha_required,
            captcha_token=captcha_token,
            captcha_image_bytes=captcha_image_bytes,
            x_random=bootstrap.x_random,
        )

    async def fetch_code(
        self,
        username: str,
        password: str,
        captcha: str | None = None,
        state: CasLoginState | None = None,
    ) -> CasCodeResult:
        """提交登录表单并提取授权码。

        Args:
            username: 学号或统一身份认证账号。
            password: 统一身份认证密码。
            captcha: 用户输入的验证码。若当前流程无需验证码可省略。
            state: 预热阶段返回的登录状态。为空时会自动重新准备登录页。

        Returns:
            登录后的授权码提取结果。

        Raises:
            CaptchaRequiredError: 当前流程需要验证码但未提供。
        """

        login_state = state or await self.prepare_login()
        if login_state.captcha_required and not captcha:
            raise CaptchaRequiredError(login_state)

        payload = self._build_login_payload(login_state, username, password, captcha)
        status, final_url, html = await self.post_form(login_state.login_url, payload)
        bootstrap = parse_login_bootstrap(html)
        return CasCodeResult(
            code=extract_code_from_url(final_url),
            final_url=final_url,
            status=status,
            error_code=bootstrap.error_code,
            error_message=bootstrap.error_message,
            raw_html_excerpt=html[:1200],
        )

    @staticmethod
    def _build_login_payload(
        state: CasLoginState,
        username: str,
        password: str,
        captcha: str | None,
    ) -> dict[str, str]:
        """根据登录页上下文构造提交表单。"""

        source = _text(state.hidden_inputs.get("source")) or "cas"
        payload = {
            "username": _text(username),
            "password": _text(password),
            "source": source,
        }
        if "cas" in source.lower():
            for key in ("lt", "execution", "_eventId"):
                value = _text(state.hidden_inputs.get(key))
                if value:
                    payload[key] = value
        elif state.hidden_inputs.get("pid"):
            payload["pid"] = _text(state.hidden_inputs["pid"])
        if state.captcha_required:
            payload["captcha"] = _text(captcha)
            if state.captcha_token:
                payload["token"] = state.captcha_token
        return payload
