"""명령별 개인용 진행 표시. 진행 알림 오류는 실제 작업을 중단하지 않는다."""

import asyncio
import logging
import threading
from contextvars import ContextVar
from time import monotonic

logger = logging.getLogger(__name__)
_current = ContextVar("command_progress", default=None)


def emit(key: str, text: str) -> None:
    progress = _current.get()
    if progress is not None:
        progress.emit(key, text)


class CommandProgress:
    # response_url: 30분 내 최대 5회. 시작 + 주요 단계 3회 + 최종 결과 1회.
    milestones = {"status", "write_mid", "table_done"}

    def __init__(self, respond, title):
        self.respond, self.title = respond, title
        self.loop = asyncio.get_running_loop()
        self.thread_id = threading.get_ident()
        self.queue = asyncio.Queue()
        self.lines = {}
        self.published = set()
        self.attempts = 0
        self.created = False
        self.started = monotonic()
        self.closed = False

    async def __aenter__(self):
        self.token = _current.set(self)
        await self._send("⏳ 작업을 시작합니다.")
        self.worker = asyncio.create_task(self._run())
        return self

    async def __aexit__(self, exc_type, exc, tb):
        try:
            if not self.closed:
                await self.finish("⚠️ 작업이 중단되었습니다. 봇 로그를 확인해주세요.")
        finally:
            _current.reset(self.token)

    def emit(self, key, text):
        if self.closed:
            return
        if threading.get_ident() == self.thread_id:
            self.queue.put_nowait((key, text))
        else:
            self.loop.call_soon_threadsafe(self.queue.put_nowait, (key, text))

    async def finish(self, text):
        if self.closed:
            return
        self.closed = True
        await self.queue.put((None, text))
        await self.worker

    async def _run(self):
        while True:
            key, text = await self.queue.get()
            if key is None:
                await self._send(text)
                return
            self.lines[key] = text
            notify = key in self.milestones or (key == "queue" and text.startswith("⏳"))
            if notify and key not in self.published:
                self.published.add(key)
                if self.attempts < 4:
                    await self._send("⏳ 처리 중입니다.")

    async def _send(self, status):
        if self.attempts >= 5 or monotonic() - self.started >= 29 * 60:
            return
        self.attempts += 1
        elapsed = int(monotonic() - self.started)
        text = "\n".join([f"*{self.title}* · {elapsed}초", *self.lines.values(), status])
        try:
            response = await asyncio.wait_for(
                self.respond(text, response_type="ephemeral", replace_original=self.created),
                timeout=8,
            )
            code = getattr(response, "status_code", 200)
            if isinstance(code, int) and code != 200:
                logger.warning("개인 진행 메시지 갱신 실패: HTTP %s", code)
                return
            self.created = True
        except Exception as exc:
            # response_url과 토큰이 예외 문자열에 포함될 수 있어 종류만 기록한다.
            logger.warning("개인 진행 메시지 갱신 실패: %s", type(exc).__name__)
