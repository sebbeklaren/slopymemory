"""The usage rules the launcher sends as its MCP `instructions` in every session, in store mode and in memory_init
mode. A Python constant, not a data file: a launcher that came up without them would silently lose the reason the
tools get used well (measured: without rules agents never pass query_concepts). Changing this text changes what
every agent is told — measure it first."""

USAGE_RULES = """\
Memory tools: memory_retrieve, memory_save. Use them while planning and building, not only when asked.

1. Before planning or building anything, retrieve what bears on the area you touch: conventions, styles, naming, \
prior choices. Weigh the set, act on what converges, and surface what does not.
2. When the user or your own compaction summary refers to something earlier, retrieve.
3. When a decision is made, save it at once with its why: one memory per decision, with save_concepts and a thread.

When memories disagree, weigh them by how strongly each converges with the question, and state the one you act on \
and why, as your reading. When nothing clearly converges, leave the disagreement open, say so, and ask.

Always pass query_concepts: [space, label] pairs, spaces function or semantic. Retrieve as often as needed; skip only \
for work where nothing could have been decided before. Nothing relevant: say unknown, never guess. A file memory \
index, if present, is a fallback after the store.

If the memory tools fail or report the store unavailable, tell the user, and fall back to your harness's own memory \
(if it has one) until they work again.

If the only memory tool is memory_init, this project has no memory yet: tell the user and offer to set it up.
Never save passwords, keys or tokens; save where they live.
"""
