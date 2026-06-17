"""Tests for the HermesStreamBridge event translator."""

import asyncio

import pytest

# Skip all tests in this file if omnigent isn't installed —
# the bridge creates ExecutorEvent instances at runtime.
pytest.importorskip("omnigent")

from hermes_omnigent_harness._event_bridge import HermesStreamBridge, _DONE  # noqa: E402


@pytest.mark.asyncio
async def test_stream_callback_enqueues_text_chunks():
    """Text deltas from the callback become TextChunk events."""
    bridge = HermesStreamBridge()
    bridge.stream_callback("Hello ")
    bridge.stream_callback("world!")

    queue = await bridge.events()

    # Should have two items without finishing
    item1 = await asyncio.wait_for(queue.get(), timeout=1.0)
    item2 = await asyncio.wait_for(queue.get(), timeout=1.0)

    assert item1.text == "Hello "  # type: ignore[attr-defined]
    assert item2.text == "world!"  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_stream_callback_ignores_empty():
    """Empty string deltas are silently dropped."""
    bridge = HermesStreamBridge()
    bridge.stream_callback("")

    queue = await bridge.events()
    assert queue.empty()


@pytest.mark.asyncio
async def test_finish_enqueues_turn_complete():
    """finish() translates result dict into TurnComplete + done sentinel."""
    bridge = HermesStreamBridge()
    bridge.finish({"final_response": "Done!"})

    queue = await bridge.events()
    event_item = await asyncio.wait_for(queue.get(), timeout=1.0)
    done_item = await asyncio.wait_for(queue.get(), timeout=1.0)

    assert event_item.response == "Done!"  # type: ignore[attr-defined]
    assert done_item is _DONE
    assert bridge.is_done(done_item)


@pytest.mark.asyncio
async def test_finish_with_usage():
    """finish() extracts usage info from the result dict."""
    bridge = HermesStreamBridge()
    bridge.finish(
        {
            "final_response": "Response",
            "usage": {
                "input_tokens": 100,
                "output_tokens": 50,
                "total_tokens": 150,
                "irrelevant_key": "ignored",
            },
        }
    )

    queue = await bridge.events()
    event_item = await asyncio.wait_for(queue.get(), timeout=1.0)

    assert event_item.response == "Response"  # type: ignore[attr-defined]
    assert event_item.usage == {  # type: ignore[attr-defined]
        "input_tokens": 100,
        "output_tokens": 50,
        "total_tokens": 150,
    }


@pytest.mark.asyncio
async def test_finish_with_error():
    """finish_with_error() enqueues an ExecutorError."""
    bridge = HermesStreamBridge()
    bridge.finish_with_error("Something broke", retryable=True)

    queue = await bridge.events()
    event_item = await asyncio.wait_for(queue.get(), timeout=1.0)
    done_item = await asyncio.wait_for(queue.get(), timeout=1.0)

    assert event_item.message == "Something broke"  # type: ignore[attr-defined]
    assert event_item.retryable is True  # type: ignore[attr-defined]
    assert bridge.is_done(done_item)


@pytest.mark.asyncio
async def test_finish_is_idempotent():
    """Calling finish() twice only enqueues one TurnComplete."""
    bridge = HermesStreamBridge()
    bridge.finish({"final_response": "First"})
    bridge.finish({"final_response": "Second"})

    queue = await bridge.events()
    event_item = await asyncio.wait_for(queue.get(), timeout=1.0)
    done_item = await asyncio.wait_for(queue.get(), timeout=1.0)

    assert event_item.response == "First"  # type: ignore[attr-defined]
    assert bridge.is_done(done_item)
    assert queue.empty()


@pytest.mark.asyncio
async def test_full_stream_lifecycle():
    """Simulate a full conversation: stream deltas, then finish."""
    bridge = HermesStreamBridge()

    # Simulate streaming
    for chunk in ["Hello", ", ", "how", " are", " you?"]:
        bridge.stream_callback(chunk)

    # Simulate completion
    bridge.finish({"final_response": "Hello, how are you?"})

    queue = await bridge.events()
    collected = []
    while True:
        item = await asyncio.wait_for(queue.get(), timeout=1.0)
        if bridge.is_done(item):
            break
        collected.append(item)

    # Should have 5 text chunks + 1 TurnComplete
    assert len(collected) == 6
    text_chunks = [e for e in collected if hasattr(e, "text")]
    turn_complete = [e for e in collected if hasattr(e, "response")]
    assert len(text_chunks) == 5
    assert len(turn_complete) == 1
    assert turn_complete[0].response == "Hello, how are you?"
