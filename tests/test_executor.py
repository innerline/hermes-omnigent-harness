"""Tests for HermesExecutor message extraction logic."""

from hermes_omnigent_harness.hermes_executor import HermesExecutor


class TestExtractUserMessage:
    """Tests for HermesExecutor._extract_user_message()."""

    def setup_method(self):
        """Create an executor without initializing the Hermes agent."""
        self.executor = HermesExecutor.__new__(HermesExecutor)

    def test_simple_user_message(self):
        """Extract the last user message from a simple conversation."""
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
            {"role": "user", "content": "How are you?"},
        ]
        result = self.executor._extract_user_message(messages, "")
        assert result == "How are you?"

    def test_empty_messages(self):
        """Empty message list returns empty string."""
        assert self.executor._extract_user_message([], "") == ""

    def test_no_user_messages(self):
        """No user messages returns empty string."""
        messages = [
            {"role": "system", "content": "You are helpful"},
            {"role": "assistant", "content": "Hi!"},
        ]
        assert self.executor._extract_user_message(messages, "") == ""

    def test_multimodal_content(self):
        """Extract text from multimodal content blocks."""
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "What's in this image?"},
                    {"type": "image", "url": "data:image/png;base64,..."},
                ],
            },
        ]
        result = self.executor._extract_user_message(messages, "")
        assert result == "What's in this image?"

    def test_string_content_in_list(self):
        """Handle string items in content list."""
        messages = [
            {
                "role": "user",
                "content": ["First part", "Second part"],
            },
        ]
        result = self.executor._extract_user_message(messages, "")
        assert "First part" in result
        assert "Second part" in result

    def test_non_string_content(self):
        """Non-string content is converted to string."""
        messages = [
            {"role": "user", "content": 12345},
        ]
        result = self.executor._extract_user_message(messages, "")
        assert result == "12345"

    def test_multiple_users_takes_last(self):
        """With multiple user messages, the last one is returned."""
        messages = [
            {"role": "user", "content": "First question"},
            {"role": "assistant", "content": "First answer"},
            {"role": "user", "content": "Second question"},
        ]
        result = self.executor._extract_user_message(messages, "")
        assert result == "Second question"
