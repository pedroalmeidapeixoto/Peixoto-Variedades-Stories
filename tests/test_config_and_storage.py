from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from stories_bot.config import ConfigError, InstagramSettings, R2Settings
from stories_bot.state import load_offset, load_posted, record_post, save_offset
from stories_bot.storage import R2Storage, StorageError

R2_ENV = {
    "R2_ACCOUNT_ID": "acc",
    "R2_ACCESS_KEY_ID": "key",
    "R2_SECRET_ACCESS_KEY": "secret",
    "R2_BUCKET": "bucket",
    "R2_PUBLIC_URL": "https://pub.example/",
}


def test_instagram_settings_need_both_values():
    assert InstagramSettings.from_env({"IG_USER_ID": "1", "IG_ACCESS_TOKEN": "t"}).user_id == "1"
    with pytest.raises(ConfigError, match="IG_ACCESS_TOKEN"):
        InstagramSettings.from_env({"IG_USER_ID": "1"})


def test_r2_settings_strip_trailing_slash_and_report_all_missing():
    assert R2Settings.from_env(R2_ENV).public_url == "https://pub.example"
    with pytest.raises(ConfigError) as err:
        R2Settings.from_env({})
    assert "R2_BUCKET" in str(err.value) and "R2_PUBLIC_URL" in str(err.value)


def test_r2_storage_calls_the_bucket():
    client = MagicMock()
    storage = R2Storage(R2Settings.from_env(R2_ENV), client=client)

    storage.upload("stories/a b.jpg", b"x", "image/jpeg")
    client.put_object.assert_called_once_with(
        Bucket="bucket", Key="stories/a b.jpg", Body=b"x", ContentType="image/jpeg"
    )
    storage.delete("stories/a b.jpg")
    client.delete_object.assert_called_once_with(Bucket="bucket", Key="stories/a b.jpg")
    assert storage.public_url("stories/a b.jpg") == "https://pub.example/stories/a%20b.jpg"


def test_r2_errors_never_leak_details():
    client = MagicMock()
    client.put_object.side_effect = ClientError(
        {"Error": {"Code": "403", "Message": "secret"}}, "x"
    )
    storage = R2Storage(R2Settings.from_env(R2_ENV), client=client)
    with pytest.raises(StorageError) as err:
        storage.upload("k.jpg", b"x", "image/jpeg")
    assert "secret" not in str(err.value)


def test_state_roundtrip(tmp_path):
    from datetime import datetime

    posted = tmp_path / "state" / "posted.json"
    assert load_posted(posted) == {}
    record_post(posted, "id1", "a.jpg", "m1", datetime(2026, 10, 9, 12, 0))
    assert load_posted(posted)["id1"]["media_id"] == "m1"

    offset = tmp_path / "telegram.json"
    assert load_offset(offset) is None
    save_offset(offset, 7)
    assert load_offset(offset) == 7
