### Title
Wallet crash on maliciously crafted offer via unguarded `_additions` dict lookup in `Offer._get_offered_coins()` - (File: chia/wallet/trading/offer.py)

### Summary
`Offer.__post_init__` silently drops a `CoinSpend`'s entry from the `_additions` cache whenever `compute_spend_hints_and_additions()` raises any generic `Exception` for that spend, while the spend itself remains in `self._bundle.coin_spends`. `_get_offered_coins()` later indexes `self._additions[parent_spend.coin]` unconditionally for every coin spend in the bundle, with no `.get()`/None-check, so a crafted offer that makes hint/addition computation fail for one spend but still parses successfully as an `Offer` will raise an uncaught `KeyError` the moment a wallet user inspects or takes that offer. This mirrors the CVE-2017-17440 pattern of a NULL/missing-value dereference triggered by parsing a crafted file that partially, but not fully, fails validation.

### Finding Description
In `chia/wallet/trading/offer.py`:

- `__post_init__` builds `_additions` by iterating `self._bundle.coin_spends` and calling `compute_spend_hints_and_additions(cs, max_cost=max_cost)`. Failures are handled inconsistently:
  - `ValidationError` is re-raised (aborts offer construction).
  - `ValueError` with a specific cost message raises `ValidationError`; any other `ValueError` triggers `continue` (skip, no entry added).
  - Any other `Exception` is silently swallowed via `except Exception: continue`. [1](#0-0) 

  In all the `continue` branches, `cs.coin` is **not** added to `adds`, yet the coin spend `cs` remains part of `self._bundle.coin_spends` — there is no removal or rejection of the whole offer.

- Later, `_get_offered_coins()` iterates the same `self._bundle.coin_spends` and does a direct, unguarded dictionary lookup:
```
additions: list[Coin] = self._additions[parent_spend.coin]
``` [2](#0-1) 

  Since `_additions` may be missing an entry for `parent_spend.coin` (due to the swallowed exception above), this line raises an unhandled `KeyError`, crashing whatever code path invoked it.

This is directly analogous to the CVE-2017-17440 bug class: a parser that "successfully" accepts a crafted, malformed input object while internally failing to populate an expected data structure, and a later consumer that dereferences/looks up that missing entry without a guard, causing a crash.

### Impact Explanation
`Offer` objects are attacker-influenced: any wallet user, offer counterparty, or CLI/RPC caller who loads an offer file/bech32 blob (`Offer.from_bech32`, `Offer.from_bytes`) triggers `__post_init__`. If an attacker crafts a coin spend inside the offer whose puzzle reveal/solution makes `compute_spend_hints_and_additions` throw a generic exception (e.g., malformed CLVM structure that raises `TypeError`/`AttributeError`/a non-cost `ValueError` deep in hint computation) for one spend while the overall bundle still constructs without error, the resulting `Offer` object is "valid" but has an incomplete `_additions` map. As soon as the recipient calls any path that reaches `_get_offered_coins()` — e.g., `offer.summary()` used by `take_offer` CLI/RPC flow, `get_offered_coins()`, or fee/addition calculations — the process crashes with an unhandled `KeyError`. This is a spend-triggered denial-of-service against a wallet user/RPC caller simply from examining or taking an untrusted offer, requiring no privileges beyond receiving/opening the offer file. Severity is Medium, consistent with a reachable crash/DoS rather than fund theft.

### Likelihood Explanation
Likelihood is moderate: it requires finding or crafting a coin spend whose puzzle/solution causes `compute_spend_hints_and_additions` to throw a non-`ValidationError`, non-cost-`ValueError` exception (the broad `except Exception: continue` suggests such paths exist and were anticipated defensively, but the corresponding consumer in `_get_offered_coins()` was not updated to tolerate the resulting gap). I was not able to fully inspect `compute_spend_hints_and_additions` (chia/wallet/util/compute_hints.py) in this session to enumerate concrete inputs that raise a generic exception — this should be verified by a background agent with full file access before treating this as fully proven.

### Recommendation
- In `Offer._get_offered_coins()`, use `self._additions.get(parent_spend.coin, [])` instead of `self._additions[parent_spend.coin]`, or explicitly reject/raise a clear `ValueError` during `__post_init__` when a coin spend cannot have its additions computed, rather than silently continuing.
- Audit all other direct `self._additions[...]` accesses in `offer.py` for the same unguarded-lookup pattern.
- Narrow the `except Exception: continue` in `__post_init__` to only the specific expected exception types, and treat unexpected exceptions as offer construction failures (fail closed) rather than silently dropping data that other code assumes is always present.

### Proof of Concept
Conceptual PoC (would need to be validated by a background agent with sandbox access to `compute_spend_hints_and_additions`):
1. Construct a `WalletSpendBundle` with two `CoinSpend`s: one normal spend, and one with a puzzle reveal/solution crafted so that `compute_spend_hints_and_additions` raises a generic exception (not `ValidationError`, not the specific cost `ValueError`) — e.g., a puzzle that runs successfully under `run_with_cost` but whose output conditions are malformed enough to cause an unexpected error inside hint/addition extraction.
2. Wrap this bundle into an `Offer` via `Offer.from_spend_bundle()` or manually construct `Offer(requested_payments, bundle, driver_dict)`. Because of the broad `except Exception: continue`, offer construction succeeds without raising.
3. Have a wallet user call `offer.summary()` or any path leading to `_get_offered_coins()` (e.g., CLI `take_offer` preview, RPC `take_offer`/`get_offer_summary`).
4. Observe an unhandled `KeyError: Coin(...)` raised from `chia/wallet/trading/offer.py` line 253, crashing the calling RPC/CLI flow. [1](#0-0) [2](#0-1)

### Citations

**File:** chia/wallet/trading/offer.py (L163-186)
```python
        adds: dict[Coin, list[Coin]] = {}
        hints: dict[bytes32, bytes32] = {}
        max_cost = int(DEFAULT_CONSTANTS.MAX_BLOCK_COST_CLVM)
        for cs in self._bundle.coin_spends:
            # you can't spend the same coin twice in the same SpendBundle
            assert cs.coin not in adds
            try:
                hinted_coins, cost = compute_spend_hints_and_additions(cs, max_cost=max_cost)
                max_cost -= cost
                adds[cs.coin] = [hc.coin for hc in hinted_coins.values()]
                hints = {**hints, **{id: hc.hint for id, hc in hinted_coins.items() if hc.hint is not None}}
            except ValidationError:
                raise
            except ValueError as e:
                if e.args and e.args[0] == "cost exceeded or below zero":
                    raise ValidationError(Err.BLOCK_COST_EXCEEDS_MAX, "compute_additions for CoinSpend") from e
                continue
            except Exception:
                continue
            if max_cost < 0:
                raise ValidationError(Err.BLOCK_COST_EXCEEDS_MAX, "compute_additions for CoinSpend")
        object.__setattr__(self, "_additions", adds)
        object.__setattr__(self, "_hints", hints)
        object.__setattr__(self, "_conditions", None)
```

**File:** chia/wallet/trading/offer.py (L248-253)
```python
        for parent_spend in self._bundle.coin_spends:
            coins_for_this_spend: list[Coin] = []

            parent_puzzle: UnknownPuzzle = UnknownPuzzle(known_program=parent_spend.puzzle_reveal)
            parent_solution = Program.from_serialized(parent_spend.solution)
            additions: list[Coin] = self._additions[parent_spend.coin]
```
