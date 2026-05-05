"""Tests for the Moonraker [server_announcements] client wrapper."""
from __future__ import annotations

import httpx
import pytest
import respx

from multiace_web.announcements import AnnouncementsClient


@pytest.mark.asyncio
async def test_post_announcement_returns_entry_id():
    async with respx.mock(base_url="http://moon.test", assert_all_called=False) as rx:
        rx.post("/server/announcements/post").respond(
            json={"result": {"entry_id": "abc-123"}}
        )
        async with httpx.AsyncClient() as http:
            client = AnnouncementsClient(http, "http://moon.test")
            entry_id = await client.post(
                title="Auto-dry triggered: ACE 0",
                description="Humidity 22%, drying to 15%",
                entry_type="info",
                priority="normal",
            )
            assert entry_id == "abc-123"


@pytest.mark.asyncio
async def test_post_announcement_returns_none_on_http_error():
    async with respx.mock(base_url="http://moon.test", assert_all_called=False) as rx:
        rx.post("/server/announcements/post").respond(status_code=500)
        async with httpx.AsyncClient() as http:
            client = AnnouncementsClient(http, "http://moon.test")
            entry_id = await client.post(title="x", description="y")
            assert entry_id is None  # logged + swallowed; FSM keeps running


@pytest.mark.asyncio
async def test_post_announcement_returns_none_on_network_error():
    async with respx.mock(base_url="http://moon.test", assert_all_called=False) as rx:
        rx.post("/server/announcements/post").mock(side_effect=httpx.ConnectError("boom"))
        async with httpx.AsyncClient() as http:
            client = AnnouncementsClient(http, "http://moon.test")
            entry_id = await client.post(title="x", description="y")
            assert entry_id is None


@pytest.mark.asyncio
async def test_dismiss_returns_true_on_success():
    async with respx.mock(base_url="http://moon.test", assert_all_called=False) as rx:
        rx.post("/server/announcements/dismiss").respond(json={"result": {}})
        async with httpx.AsyncClient() as http:
            client = AnnouncementsClient(http, "http://moon.test")
            ok = await client.dismiss("abc-123")
            assert ok is True


@pytest.mark.asyncio
async def test_dismiss_returns_false_on_error():
    async with respx.mock(base_url="http://moon.test", assert_all_called=False) as rx:
        rx.post("/server/announcements/dismiss").respond(status_code=500)
        async with httpx.AsyncClient() as http:
            client = AnnouncementsClient(http, "http://moon.test")
            ok = await client.dismiss("abc-123")
            assert ok is False
