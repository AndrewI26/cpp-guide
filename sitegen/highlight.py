"""Minimal regex-based syntax highlighter for C++ (and plain escaping for others)."""
from __future__ import annotations

import html
import re

KEYWORDS = set("""
alignas alignof and asm auto break case catch class concept const consteval constexpr
constinit const_cast continue co_await co_return co_yield decltype default delete do
dynamic_cast else enum explicit export extern false final for friend goto if inline
mutable namespace new noexcept not nullptr operator or override private protected public
register reinterpret_cast requires return sizeof static static_assert static_cast struct
switch template this thread_local throw true try typedef typeid typename union using
virtual volatile while
""".split())

TYPES = set("""
void bool char char8_t char16_t char32_t wchar_t short int long float double signed unsigned
size_t ssize_t ptrdiff_t intptr_t uintptr_t int8_t int16_t int32_t int64_t uint8_t uint16_t
uint32_t uint64_t byte string string_view vector array map set unordered_map unordered_set
deque list optional variant span atomic unique_ptr shared_ptr weak_ptr pair tuple mutex
thread function
""".split())

CPP_LANGS = {"cpp", "c++", "cxx", "hpp", "c", "h", "cc"}

TOKEN_RE = re.compile(
    r"""
      (?P<comment>//[^\n]*|/\*.*?\*/)
    | (?P<pre>^[ \t]*\#[ \t]*[a-z]+[^\n]*)
    | (?P<rawstr>R"(?P<delim>[^(\s]*)\(.*?\)(?P=delim)")
    | (?P<string>(?:u8|[uUL])?"(?:\\.|[^"\\\n])*")
    | (?P<number>\b(?:0[xX][0-9a-fA-F']+|0[bB][01']+|\d[\d']*(?:\.[\d']*)?(?:[eE][+-]?\d+)?)[uUlLfFzZ]*)
    | (?P<char>(?:u8|[uUL])?'(?:\\.|[^'\\\n])+')
    | (?P<word>[A-Za-z_]\w*)
    """,
    re.X | re.S | re.M,
)

CLASS = {"comment": "c", "pre": "p", "rawstr": "s", "string": "s", "char": "s", "number": "n"}


def _span(cls: str, text: str) -> str:
    return f'<span class="tok-{cls}">{html.escape(text, quote=False)}</span>'


def highlight_cpp(code: str) -> str:
    out: list[str] = []
    pos = 0
    for m in TOKEN_RE.finditer(code):
        out.append(html.escape(code[pos : m.start()], quote=False))
        kind = m.lastgroup if m.lastgroup != "delim" else "rawstr"
        text = m.group(0)
        if m.group("rawstr"):
            kind = "rawstr"
        if kind == "word":
            if text in KEYWORDS:
                out.append(_span("k", text))
            elif text in TYPES:
                out.append(_span("t", text))
            elif code[m.end() : m.end() + 1] == "(":
                out.append(_span("f", text))
            else:
                out.append(html.escape(text, quote=False))
        else:
            out.append(_span(CLASS[kind], text))
        pos = m.end()
    out.append(html.escape(code[pos:], quote=False))
    return "".join(out)


def highlight_shell(code: str) -> str:
    lines = []
    for line in code.split("\n"):
        if line.lstrip().startswith("#"):
            lines.append(_span("c", line))
        elif line.startswith("$ "):
            lines.append(_span("p", "$ ") + html.escape(line[2:], quote=False))
        else:
            lines.append(html.escape(line, quote=False))
    return "\n".join(lines)


def highlight(code: str, lang: str) -> str:
    lang = (lang or "").lower()
    if lang in CPP_LANGS:
        return highlight_cpp(code)
    if lang in {"bash", "sh", "shell", "console"}:
        return highlight_shell(code)
    return html.escape(code, quote=False)
