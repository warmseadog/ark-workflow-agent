"""Remove the editor-generated block before reusing a template or composing a request."""
import re


def strip_reference_rules(prompt: str) -> str:
    return re.sub(r"\n*【素材联动】[\s\S]*?【联动结束】", "", prompt).strip()
