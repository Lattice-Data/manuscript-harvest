"""List prices, and the ratios that mean only two numbers per model are stored.

A run on a Claude subscription is not billed per token, so every dollar figure
this package prints is a LIST-PRICE ESTIMATE of what the same work would have
cost through the API -- the same thing Claude Code's own run summary reports,
and the same caveat applies. It is a size, not an invoice.

Two design notes.

**Only input and output rates are stored.** Anthropic's cache prices are fixed
multiples of a model's base input rate: a cache read is 0.1x, a 5-minute cache
write 1.25x, a 1-hour cache write 2.0x. Storing four numbers per model invites
three of them to drift; storing two and deriving the rest cannot. The ratios
were confirmed against the CLI's own `total_cost_usd` on this machine -- see
`tests/test_usage.py`, which reproduces two real envelopes to the cent.

**Cache writes are priced by TTL, and the difference is not small.** The
perturbation runs use 1-hour caching exclusively (`ephemeral_1h_input_tokens`
nonzero, `ephemeral_5m` zero throughout). Pricing their 37.4M cache-write
tokens at the 5-minute rate would understate the v0.0.21 corpus run by about
$140. `cost()` therefore reads the `cache_creation` sub-object and refuses to
collapse it to the flat `cache_creation_input_tokens` total.

An unknown model returns None rather than guessing. A wrong price is worse than
a missing one: a missing one is visible in the report as `price unknown`, and a
wrong one is a number somebody will quote.
"""

from __future__ import annotations

from typing import Mapping

#: Dollars per million tokens, (input, output). Canonical model names -- the
#: envelope's `modelUsage` keys carry a date suffix, so `canonical()` strips it
#: before lookup. Add a model here and every report picks it up.
RATES: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-opus-4-1": (15.00, 75.00),
    "claude-sonnet-4-5": (3.00, 15.00),
}

#: Multiples of the model's base INPUT rate. Not per-model, because these
#: ratios are a property of the caching product rather than of any model.
CACHE_READ = 0.10
CACHE_WRITE_5M = 1.25
CACHE_WRITE_1H = 2.00

_PER_MTOK = 1_000_000.0


def canonical(model: str | None) -> str | None:
    """`claude-haiku-4-5-20251001` -> `claude-haiku-4-5`.

    The envelope's `modelUsage` block already supplies a `canonicalModel` field,
    but the transcript records this package backfills from carry only the dated
    `message.model`, so the two paths need one shared way to agree on a name.
    """
    if not model:
        return None
    if model in RATES:
        return model
    # Strip a trailing -YYYYMMDD, which is the only suffix form in use.
    head, _, tail = model.rpartition("-")
    if head and len(tail) == 8 and tail.isdigit():
        return head
    return model


def cost(model: str | None, usage: Mapping[str, object]) -> float | None:
    """List-price dollars for one usage block, or None if the model is unknown.

    `usage` is either a transcript record's `message.usage` or an envelope's
    `usage` -- the field names are the same in both, which is why one function
    serves the backfill and the live path.
    """
    name = canonical(model)
    if name not in RATES:
        return None
    rate_in, rate_out = RATES[name]

    def n(key: str, src: Mapping[str, object] = usage) -> int:
        value = src.get(key)
        return int(value) if isinstance(value, (int, float)) else 0

    # The TTL split is authoritative where present. Where it is absent -- older
    # records, or a provider that does not report it -- fall back to the flat
    # total at the 5-minute rate, which is the cheaper of the two and so cannot
    # silently inflate a total. The report names this case.
    creation = usage.get("cache_creation")
    if isinstance(creation, Mapping):
        write_1h = n("ephemeral_1h_input_tokens", creation)
        write_5m = n("ephemeral_5m_input_tokens", creation)
    else:
        write_1h = 0
        write_5m = n("cache_creation_input_tokens")

    return (
        n("input_tokens") * rate_in
        + n("output_tokens") * rate_out
        + n("cache_read_input_tokens") * rate_in * CACHE_READ
        + write_5m * rate_in * CACHE_WRITE_5M
        + write_1h * rate_in * CACHE_WRITE_1H
    ) / _PER_MTOK
