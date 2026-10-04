from app.schemas import ChannelCreate, ChannelPatch


def test_chat_setting_is_writable_on_create_and_patch():
    created = ChannelCreate(url="@example", chat_enabled=False)
    updated = ChannelPatch(chat_enabled=False)

    assert created.chat_enabled is False
    assert updated.model_dump(exclude_unset=True) == {"chat_enabled": False}


def test_live_from_start_defaults_on_and_is_writable():
    created = ChannelCreate(url="@example")
    updated = ChannelPatch(live_from_start=False)

    assert created.live_from_start is True
    assert updated.model_dump(exclude_unset=True) == {"live_from_start": False}
