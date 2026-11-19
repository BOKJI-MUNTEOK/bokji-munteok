"""선택한 테마에서 본문·입력창·안내 배지의 글자를 읽을 수 있는지 검사한다."""
from pathlib import Path
import re
import tomllib
import unittest

ROOT = Path(__file__).resolve().parent.parent


def luminance(color):
    value = color.lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    channels = [int(value[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return sum(c * weight for c, weight in zip(linear, (0.2126, 0.7152, 0.0722)))


def contrast(foreground, background):
    lighter, darker = sorted((luminance(foreground), luminance(background)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


class TestAppearanceContrast(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.theme = tomllib.loads((ROOT / ".streamlit/config.toml").read_text(encoding="utf-8"))["theme"]
        css = (ROOT / "assets/appearance.css").read_text(encoding="utf-8")
        dark = re.search(r':root\[data-bokji-theme="dark"\]\s*\{(.*?)\}', css, re.S).group(1)
        cls.dark = dict(re.findall(r"(--bm-[\w-]+)\s*:\s*(#[0-9a-fA-F]{3,6})\s*;", dark))

    def readable(self, foreground, background, label):
        self.assertGreaterEqual(contrast(foreground, background), 4.5,
                                f"{label}: {foreground} on {background} must meet WCAG AA normal text contrast")

    def test_native_text_on_page_and_inputs(self):
        for mode in ("light", "dark"):
            palette = self.theme[mode]
            for background in ("backgroundColor", "secondaryBackgroundColor"):
                with self.subTest(mode=mode, background=background):
                    self.readable(palette["textColor"], palette[background], f"{mode} {background}")

    def test_native_sidebar_and_primary_buttons(self):
        for mode in ("light", "dark"):
            palette = self.theme[mode]
            sidebar = {**palette, **palette.get("sidebar", {})}
            for background in ("backgroundColor", "secondaryBackgroundColor"):
                with self.subTest(mode=mode, background=background):
                    self.readable(sidebar["textColor"], sidebar[background], f"{mode} sidebar")
        self.readable("#ffffff", self.theme["primaryColor"], "primary button label")

    def test_dark_links_and_custom_text(self):
        palette = self.theme["dark"]
        self.readable(palette["linkColor"], palette["backgroundColor"], "dark native link")
        for text in ("text", "text-strong", "muted", "secondary", "subtle", "caption", "faint",
                     "heading", "accent", "money", "quick-text"):
            for surface in ("surface", "soft", "soft-neutral", "chat-bg"):
                with self.subTest(text=text, surface=surface):
                    self.readable(self.dark[f"--bm-{text}"], self.dark[f"--bm-{surface}"], f"{text}/{surface}")

    def test_dark_status_badges_and_number_labels(self):
        for status in ("easy", "medium", "hard", "number"):
            with self.subTest(status=status):
                self.readable(self.dark[f"--bm-{status}-text"], self.dark[f"--bm-{status}-bg"], status)


if __name__ == "__main__":
    unittest.main()
