"""The texts the embedder's reference vectors were taken from (tests/substrate/embedder_reference.json)."""
_SENTENCE = "The nightly export job writes every order changed since the last run to a compressed file. "
TEXTS = [
    "Destructive actions use an outlined button and a confirm dialog.",
    "button styling",
    "how long does a user stay logged in?",
    _SENTENCE * 180,                          # past 2,048 tokens: the long-sequence path
    _SENTENCE * 700,                          # past 8,192 tokens: truncation
    "Ünïcödé — 日本語のテキスト, emoji 🚀✅, code: `def f(x): return {k: v for k, v in x.items()}`\t\t  spaces",
]
