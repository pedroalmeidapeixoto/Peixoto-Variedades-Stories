import pytest


class FakeStorage:
    """In-memory stand-in for the R2 bucket."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.deleted: list[str] = []

    def upload(self, key: str, data: bytes, content_type: str) -> None:
        self.objects[key] = data

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)
        self.deleted.append(key)

    def public_url(self, key: str) -> str:
        return f"https://cdn.example/{key}"


@pytest.fixture
def storage() -> FakeStorage:
    return FakeStorage()
