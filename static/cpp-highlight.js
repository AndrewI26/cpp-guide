// C++ syntax highlighter for the practice editor. Mirrors sitegen/highlight.py so the editor and the
// rendered code blocks use the same token classes (and colours, from style.css).
(function () {
  const words = (s) => new Set(s.trim().split(/\s+/));
  const KEYWORDS = words(`
    alignas alignof and asm auto break case catch class concept const consteval constexpr
    constinit const_cast continue co_await co_return co_yield decltype default delete do
    dynamic_cast else enum explicit export extern false final for friend goto if inline
    mutable namespace new noexcept not nullptr operator or override private protected public
    register reinterpret_cast requires return sizeof static static_assert static_cast struct
    switch template this thread_local throw true try typedef typeid typename union using
    virtual volatile while`);
  const TYPES = words(`
    void bool char char8_t char16_t char32_t wchar_t short int long float double signed unsigned
    size_t ssize_t ptrdiff_t intptr_t uintptr_t int8_t int16_t int32_t int64_t uint8_t uint16_t
    uint32_t uint64_t byte string string_view vector array map set unordered_map unordered_set
    deque list optional variant span atomic unique_ptr shared_ptr weak_ptr pair tuple mutex
    thread function`);

  // Unterminated comments / strings run to the end so highlighting doesn't flicker while typing.
  const TOKEN_RE = new RegExp([
    String.raw`(?<comment>//[^\n]*|/\*[\s\S]*?(?:\*/|(?![\s\S])))`,
    String.raw`(?<pre>^[ \t]*#[ \t]*[a-z]+[^\n]*)`,
    String.raw`(?<rawstr>R"(?<delim>[^(\s]*)\([\s\S]*?(?:\)\k<delim>"|(?![\s\S])))`,
    String.raw`(?<string>(?:u8|[uUL])?"(?:\\.|[^"\\\n])*"?)`,
    String.raw`(?<number>\b(?:0[xX][0-9a-fA-F']+|0[bB][01']+|\d[\d']*(?:\.[\d']*)?(?:[eE][+-]?\d+)?)[uUlLfFzZ]*)`,
    String.raw`(?<char>(?:u8|[uUL])?'(?:\\.|[^'\\\n])+')`,
    String.raw`(?<word>[A-Za-z_]\w*)`,
  ].join("|"), "gm");

  const CLASS = { comment: "c", pre: "p", rawstr: "s", string: "s", char: "s", number: "n" };
  const esc = (s) => s.replace(/[&<>]/g, (c) => (c === "&" ? "&amp;" : c === "<" ? "&lt;" : "&gt;"));
  const span = (cls, text) => `<span class="tok-${cls}">${esc(text)}</span>`;

  function highlightCpp(code) {
    let out = "";
    let pos = 0;
    for (const m of code.matchAll(TOKEN_RE)) {
      out += esc(code.slice(pos, m.index));
      const text = m[0];
      const g = m.groups;
      if (g.word !== undefined) {
        if (KEYWORDS.has(text)) out += span("k", text);
        else if (TYPES.has(text)) out += span("t", text);
        else if (code[m.index + text.length] === "(") out += span("f", text);
        else out += esc(text);
      } else {
        const kind = Object.keys(CLASS).find((k) => g[k] !== undefined);
        out += span(CLASS[kind], text);
      }
      pos = m.index + text.length;
    }
    return out + esc(code.slice(pos));
  }

  // Compiler / test output: colour diagnostics and pass/fail lines.
  function highlightOutput(text) {
    return text.split("\n").map((line) => {
      const e = esc(line);
      if (/\berror:|^FAIL\b|^Runtime error|^Time limit/.test(line)) return `<span class="out-bad">${e}</span>`;
      if (/\bwarning:/.test(line)) return `<span class="out-warn">${e}</span>`;
      if (/\bnote:/.test(line)) return `<span class="out-note">${e}</span>`;
      if (/^\d+ passed, 0 failed$/.test(line)) return `<span class="out-good">${e}</span>`;
      return e;
    }).join("\n");
  }

  window.cppHighlight = { cpp: highlightCpp, output: highlightOutput, escape: esc };
})();
