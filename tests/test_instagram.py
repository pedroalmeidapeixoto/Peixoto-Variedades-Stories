from urllib.parse import parse_qs

import pytest
import requests
import responses

from stories_bot.instagram import GRAPH_URL, InstagramClient, InstagramError

TOKEN = "SECRET-TOKEN-123"
MEDIA = f"{GRAPH_URL}/17841/media"
PUBLISH = f"{GRAPH_URL}/17841/media_publish"
CONTAINER = f"{GRAPH_URL}/c1"


def make_client(**kwargs) -> InstagramClient:
    return InstagramClient("17841", TOKEN, sleep=lambda _: None, **kwargs)


def body_of(call) -> dict[str, list[str]]:
    return parse_qs(call.request.body)


@responses.activate
def test_publishes_an_image_story():
    responses.post(MEDIA, json={"id": "c1"})
    responses.get(CONTAINER, json={"status_code": "IN_PROGRESS"})
    responses.get(CONTAINER, json={"status_code": "FINISHED"})
    responses.post(PUBLISH, json={"id": "m1"})

    media_id = make_client().publish_story("https://x/a.jpg", is_video=False)

    assert media_id == "m1"
    create = body_of(responses.calls[0])
    assert create["media_type"] == ["STORIES"]
    assert create["image_url"] == ["https://x/a.jpg"]
    assert "video_url" not in create
    assert body_of(responses.calls[-1])["creation_id"] == ["c1"]


@responses.activate
def test_video_uses_video_url():
    responses.post(MEDIA, json={"id": "c1"})
    responses.get(CONTAINER, json={"status_code": "FINISHED"})
    responses.post(PUBLISH, json={"id": "m1"})

    make_client().publish_story("https://x/a.mp4", is_video=True)

    assert body_of(responses.calls[0])["video_url"] == ["https://x/a.mp4"]


@responses.activate
def test_api_error_surfaces_message_without_leaking_token():
    responses.post(MEDIA, status=400, json={"error": {"message": "Invalid image"}})

    with pytest.raises(InstagramError) as exc:
        make_client().publish_story("https://x/a.jpg", is_video=False)

    assert "HTTP 400: Invalid image" in str(exc.value)
    assert TOKEN not in str(exc.value)


@responses.activate
def test_network_error_does_not_leak_token():
    responses.post(MEDIA, body=requests.ConnectionError(f"failed for token={TOKEN}"))

    with pytest.raises(InstagramError) as exc:
        make_client().publish_story("https://x/a.jpg", is_video=False)

    assert TOKEN not in str(exc.value)


@responses.activate
@pytest.mark.parametrize("status", ["ERROR", "EXPIRED"])
def test_failed_container_raises(status):
    responses.post(MEDIA, json={"id": "c1"})
    responses.get(CONTAINER, json={"status_code": status})

    with pytest.raises(InstagramError, match=status):
        make_client().publish_story("https://x/a.jpg", is_video=False)

    assert all(call.request.url != PUBLISH for call in responses.calls)


@responses.activate
def test_container_that_never_finishes_times_out():
    responses.post(MEDIA, json={"id": "c1"})
    responses.get(CONTAINER, json={"status_code": "IN_PROGRESS"})

    with pytest.raises(InstagramError, match="not ready"):
        make_client(max_polls=3).publish_story("https://x/a.jpg", is_video=False)


@pytest.mark.parametrize(
    ("message", "code", "expected"),
    [
        ("HTTP 400: User is performing too many actions", None, True),
        ("HTTP 400: Application request limit reached", 4, True),
        ("HTTP 400: x", 613, True),
        ("HTTP 400: The requested resource does not exist", 100, False),
        ("HTTP 400: Invalid OAuth access token", 190, False),
    ],
)
def test_rate_limit_is_told_apart_from_other_errors(message, code, expected):
    assert InstagramError(message, code).is_rate_limit is expected


@responses.activate
def test_publishing_quota_is_read_from_the_api():
    responses.get(
        f"{GRAPH_URL}/17841/content_publishing_limit",
        json={
            "data": [{"quota_usage": 12, "config": {"quota_total": 50, "quota_duration": 86400}}]
        },
    )
    assert make_client().publishing_quota() == (12, 50)


@responses.activate
def test_publishing_quota_is_none_when_unavailable_or_oddly_shaped():
    url = f"{GRAPH_URL}/17841/content_publishing_limit"
    responses.get(url, status=400, json={"error": {}})
    assert make_client().publishing_quota() is None
    responses.replace(responses.GET, url, json={"data": []})
    assert make_client().publishing_quota() is None
