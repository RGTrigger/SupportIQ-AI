from __future__ import annotations

import unittest
from html import unescape

from streamlit.testing.v1 import AppTest


class NavigationTests(unittest.TestCase):
    def test_each_page_renders_without_streamlit_exception(self):
        at=AppTest.from_file("app.py",default_timeout=10).run()
        pages=["Dashboard","Inbox","Tickets","Customers","Conversations","AI Analysis","Knowledge Base","AI Quality","Bulk Analysis","Daily Brief","Analytics","Settings","About / Creator","Feedback & Review"]
        for name in pages:
            next(button for button in at.button if button.label.endswith(name)).click().run()
            rendered=[unescape(element.value) for element in at.title]+[unescape(element.value) for element in at.markdown]+[unescape(element.value) for element in at.caption]
            self.assertFalse(list(at.exception),f"{name}: {[error.message for error in at.exception]}")
            self.assertTrue(any(name in text for text in rendered),f"Expected {name} in {rendered}")
        next(button for button in at.button if button.label.endswith("Tickets")).click().run()
        next(button for button in at.button if button.label=="Open Ticket Workspace").click().run()
        self.assertEqual(at.title[0].value,"Ticket Workspace")
        next(button for button in at.button if button.label.endswith("Settings")).click().run()
        next(button for button in at.button if button.label=="Open MCP Tools").click().run()
        self.assertEqual(at.title[0].value,"MCP Tools")


if __name__=="__main__": unittest.main()
