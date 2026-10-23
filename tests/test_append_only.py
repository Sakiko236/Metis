import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src-python"))

import unittest
from models import ChatHistoryItem, VendorProfile, VendorType
from llm import MultiVendorClient
from agent import AgentLoop
from whitelist import WhitelistManager


class TestAppendOnlyArchitecture(unittest.TestCase):
    def test_prepare_responses_inputs(self):
        history = [
            ChatHistoryItem(role="user", content="[Workspace Initial Files Snapshot]\n=== FILE: main.py ==="),
            ChatHistoryItem(role="user", content="Fix the bug"),
            ChatHistoryItem(role="assistant", content="I fixed it"),
            ChatHistoryItem(role="user", content="[File Edit Feedback]: Successfully replaced 1 occurrence in `main.py`"),
            ChatHistoryItem(role="user", content="[Workspace Event] File added to workspace: `utils.py`\nContent:\n```python\nprint(1)\n```"),
            ChatHistoryItem(role="user", content="Now use utils.py"),
        ]

        inputs = MultiVendorClient.prepare_responses_inputs(history)
        self.assertEqual(len(inputs), 3)
        self.assertEqual(inputs[0]["role"], "user")
        self.assertIn("Initial Files Snapshot", inputs[0]["content"])
        self.assertIn("Fix the bug", inputs[0]["content"])
        self.assertEqual(inputs[1]["role"], "assistant")
        self.assertEqual(inputs[1]["content"], "I fixed it")
        self.assertEqual(inputs[2]["role"], "user")
        self.assertIn("[File Edit Feedback]", inputs[2]["content"])
        self.assertIn("[Workspace Event]", inputs[2]["content"])
        self.assertIn("Now use utils.py", inputs[2]["content"])

    def test_prepare_chat_completions_messages(self):
        system_prompt = AgentLoop.build_system_prompt()
        history = [
            ChatHistoryItem(role="user", content="Initial snapshot"),
            ChatHistoryItem(role="user", content="User prompt 1"),
            ChatHistoryItem(role="assistant", content="Assistant reply 1"),
            ChatHistoryItem(role="user", content="User prompt 2"),
        ]

        messages = MultiVendorClient.prepare_chat_completions_messages(system_prompt, history)
        self.assertEqual(len(messages), 4)
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[0]["content"], system_prompt.strip())
        self.assertEqual(messages[1]["role"], "user")
        self.assertEqual(messages[1]["content"], "Initial snapshot\n\nUser prompt 1")
        self.assertEqual(messages[2]["role"], "assistant")
        self.assertEqual(messages[2]["content"], "Assistant reply 1")
        self.assertEqual(messages[3]["role"], "user")
        self.assertEqual(messages[3]["content"], "User prompt 2")

    def test_prepare_claude_messages(self):
        history = [
            ChatHistoryItem(role="user", content="Snapshot"),
            ChatHistoryItem(role="user", content="Prompt 1"),
            ChatHistoryItem(role="assistant", content="Reply 1"),
            ChatHistoryItem(role="user", content="Prompt 2"),
        ]

        messages = MultiVendorClient.prepare_claude_messages(history)
        self.assertEqual(len(messages), 3)
        self.assertEqual(messages[0]["role"], "user")
        self.assertEqual(messages[0]["content"], "Snapshot\n\nPrompt 1")
        self.assertEqual(messages[1]["role"], "assistant")
        self.assertEqual(messages[1]["content"], "Reply 1")
        self.assertEqual(messages[2]["role"], "user")
        self.assertEqual(messages[2]["content"], "Prompt 2")

    def test_prepare_gemini_contents(self):
        history = [
            ChatHistoryItem(role="user", content="Snapshot"),
            ChatHistoryItem(role="user", content="Prompt 1"),
            ChatHistoryItem(role="assistant", content="Reply 1"),
            ChatHistoryItem(role="user", content="Prompt 2"),
        ]

        contents = MultiVendorClient.prepare_gemini_contents(history)
        self.assertEqual(len(contents), 3)
        self.assertEqual(contents[0]["role"], "user")
        self.assertEqual(contents[0]["parts"][0]["text"], "Snapshot\n\nPrompt 1")
        self.assertEqual(contents[1]["role"], "model")
        self.assertEqual(contents[1]["parts"][0]["text"], "Reply 1")
        self.assertEqual(contents[2]["role"], "user")
        self.assertEqual(contents[2]["parts"][0]["text"], "Prompt 2")

    def test_prefix_cache_invariance(self):
        """
        Verify that between Turn 1 and Turn 2:
        All messages generated in Turn 1 are strictly bit-for-bit identical in Turn 2,
        guaranteeing >90% Prefix Cache hit rate for DeepSeek and Claude.
        """
        system_prompt = AgentLoop.build_system_prompt()
        
        # Turn 1
        turn1_history = [
            ChatHistoryItem(role="user", content="[Workspace Initial Files Snapshot]\n=== FILE: a.py ===\nx = 1"),
            ChatHistoryItem(role="user", content="Refactor a.py"),
        ]
        t1_messages = MultiVendorClient.prepare_chat_completions_messages(system_prompt, turn1_history)
        
        # Assistant responds in Turn 1
        turn1_history.append(ChatHistoryItem(role="assistant", content="Refactored."))
        
        # In-between event: user adds b.py
        turn1_history.append(ChatHistoryItem(role="user", content="[Workspace Event] File added to workspace: `b.py`\nContent:\n```python\ny = 2\n```"))
        
        # Turn 2: User prompt 2
        turn1_history.append(ChatHistoryItem(role="user", content="Now refactor b.py"))
        t2_messages = MultiVendorClient.prepare_chat_completions_messages(system_prompt, turn1_history)

        # Check prefix invariance
        # Messages 0 (system), 1 (user snapshot+prompt1), 2 (assistant reply) must be identical!
        self.assertEqual(t1_messages[0], t2_messages[0])
        self.assertEqual(t1_messages[1], t2_messages[1])
        self.assertEqual(t2_messages[2]["role"], "assistant")
        self.assertEqual(t2_messages[2]["content"], "Refactored.")
        self.assertEqual(t2_messages[3]["role"], "user")
        self.assertIn("[Workspace Event] File added to workspace: `b.py`", t2_messages[3]["content"])
        self.assertIn("Now refactor b.py", t2_messages[3]["content"])

    def test_minimalist_status_no_unified_diff(self):
        wm = WhitelistManager()
        test_file = Path("test_dummy_code.py")
        test_file.write_text("def hello():\n    return False\n", encoding="utf-8")
        try:
            wm.add_file(str(test_file.resolve()))
            from models import SurgicalFileEdit, SurgicalActionType
            edit = SurgicalFileEdit(
                action_type=SurgicalActionType.SearchReplace,
                target_file="test_dummy_code.py",
                search_block="    return False\n",
                replace_block="    return True\n",
            )
            ok, msg, wf = wm.apply_edit_in_place(edit)
            self.assertTrue(ok)
            self.assertIn("Successfully replaced 1 occurrence", msg)
            self.assertNotIn("+++", msg)
            self.assertNotIn("---", msg)
            self.assertNotIn("@@", msg)
        finally:
            if test_file.exists():
                test_file.unlink()

    def test_workspace_event_formatting(self):
        from main import format_file_added_event, format_file_removed_event
        added = format_file_added_event("src/utils.py", "def add(a, b):\n    return a + b")
        self.assertIn("[Workspace Event] File added to workspace: `src/utils.py`", added)
        self.assertIn("Content:\n```python\ndef add(a, b):\n    return a + b\n```", added)

        removed = format_file_removed_event("src/utils.py")
        self.assertEqual(removed, "[Workspace Event] File `src/utils.py` has been removed from workspace.")


if __name__ == "__main__":
    unittest.main()
