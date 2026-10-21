import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from discord_forum_slack.progress import CommandProgress, emit


class ProgressTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_private_message_with_budget_reserved_for_final(self):
        respond = AsyncMock(return_value=SimpleNamespace(status_code=200))
        async with CommandProgress(respond, "동기화") as status:
            emit("fetch", "글 460개 조회 완료")
            emit("status", "상태 처리 중")
            emit("status", "Solved 422 · Handling 25 · New 13")

            def write_rows():
                emit("delete", "기존 38개 삭제 완료")
                for index in range(1, 39):
                    emit("write", f"새 행 작성 {index}/38")
                    if index == 19:
                        emit("write_mid", "테이블 작성 중")
                emit("table_done", "테이블 갱신 완료")

            await asyncio.to_thread(write_rows)
            emit("report", "보고 준비 완료")
            await status.finish("보고 발송 완료")
        calls = respond.await_args_list
        self.assertEqual(len(calls), 5)
        self.assertFalse(calls[0].kwargs["replace_original"])
        self.assertTrue(all(call.kwargs["replace_original"] for call in calls[1:]))
        self.assertTrue(all(call.kwargs["response_type"] == "ephemeral" for call in calls))
        self.assertIn("19/38", calls[2].args[0])
        self.assertIn("38/38", calls[-1].args[0])
        self.assertIn("보고 발송 완료", calls[-1].args[0])
        self.assertNotIn("상태 처리 중", calls[-1].args[0])

    async def test_concurrent_commands_do_not_mix(self):
        first, second = AsyncMock(), AsyncMock()

        async def run(respond, ident):
            async with CommandProgress(respond, ident) as status:
                await asyncio.to_thread(emit, "write", ident)
                await asyncio.sleep(0)
                await status.finish("완료")

        await asyncio.gather(run(first, "ONLY_A"), run(second, "ONLY_B"))
        emit("write", "NO_COMMAND")
        self.assertNotIn("ONLY_B", first.await_args.args[0])
        self.assertNotIn("ONLY_A", second.await_args.args[0])
        self.assertNotIn("NO_COMMAND", first.await_args.args[0])

    async def test_failed_notification_does_not_abort_or_expose_response_url(self):
        respond = AsyncMock(side_effect=[RuntimeError("secret response url"), None])
        with self.assertLogs("discord_forum_slack.progress", level="WARNING") as logs:
            async with CommandProgress(respond, "동기화") as status:
                emit("write", "작성 2/38")
                await status.finish("실제 작업 실패")
        self.assertNotIn("secret response url", "".join(logs.output))
        self.assertFalse(respond.await_args.kwargs["replace_original"])
        self.assertIn("작성 2/38", respond.await_args.args[0])
        self.assertIn("실제 작업 실패", respond.await_args.args[0])

    async def test_waiting_does_not_exhaust_final_response_slot(self):
        respond = AsyncMock()
        async with CommandProgress(respond, "동기화") as status:
            emit("queue", "⏳ 다른 동기화 대기")
            for key in ("status", "write_mid", "table_done"):
                emit(key, key)
            await status.finish("최종 완료")
        self.assertEqual(respond.await_count, 5)
        self.assertIn("최종 완료", respond.await_args.args[0])
