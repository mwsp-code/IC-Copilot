from scripts.capture_demo_research import _redact


def test_demo_capture_redacts_nested_managed_and_profile_secrets() -> None:
    managed_secret = "managed-test-secret"
    profile_secret = "profile-test-secret"
    payload = {
        "message": f"provider echoed {managed_secret}",
        "nested": [
            {"error": f"LLM request rejected {profile_secret}"},
            "safe value",
        ],
    }

    redacted = _redact(payload, (managed_secret, profile_secret))

    serialized = str(redacted)
    assert managed_secret not in serialized
    assert profile_secret not in serialized
    assert serialized.count("[REDACTED]") == 2
