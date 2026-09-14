### Title
Unhandled `AssertionError` on malformed `CREATE_COIN` puzzle-hash argument crashes wallet coin/offer/DID processing - ([File: chia/wallet/util/compute_hints.py])

### Summary
`compute_spend_hints_and_additions()` in `chia/wallet/util/compute_hints.py` re-runs a `CoinSpend`'s puzzle against its solution and manually walks the resulting condition list to build hint/addition metadata. For every `CREATE_COIN` condition it does:

```python
rf = condition.at("rf").atom
assert rf is not None
```

instead of raising a handled `ValidationError` (as is done a few lines above for cost overruns). This mirrors the CVE-2016-8569 bug class: a helper that formats/parses attacker-influenced object data assumes a value is always present and dereferences it unconditionally, so a crafted input triggers an unhandled fault instead of a graceful error.

### Finding Description
A `CREATE_COIN` condition is normally `(51 <puzzle_hash_atom> <amount> ...)`, but nothing here validates that the puzzle_hash slot is actually an atom. Any puzzle whose CLVM output emits `(51 (a . b) amount)` (a cons pair instead of an atom in the puzzle-hash position) causes `condition.at("rf").atom` to return `None`, and the bare `assert rf is not None` raises `AssertionError`. Because this is a plain `assert` (not a `ValidationError`), it propagates as an unhandled exception through every call site.

Call sites are reachable from attacker-influenced puzzle reveals:
- `chia/wallet/wallet_state_manager.py` — `coin_added`/`_add_coin_state`/`find_lost_did`, invoked while syncing spends seen on-chain (e.g. a coin sent to a wallet address, or DID recovery flows) [1](#0-0) .
- `chia/wallet/did_wallet/did_wallet.py` `identify()` — processes an arbitrary incoming `CoinSpend`'s puzzle reveal during DID discovery [2](#0-1) .
- `chia/wallet/trading/offer.py` `Offer.__post_init__` — processes every `CoinSpend` in an offer received from a counterparty, though here it is wrapped in a broad `except Exception: continue`, which suppresses the crash but silently drops the addition/hint data for that spend, corrupting the offer's computed additions/hints [3](#0-2) .
- `chia/wallet/vc_wallet/cr_cat_wallet.py` `add_crcat_coin` — also guarded by a broad `except Exception`, so it degrades to “cannot add CRCAT coin” rather than crashing, but again masks a correctness issue rather than fixing it.

The unguarded call sites (`wallet_state_manager.py`, `did_wallet.py`) have no surrounding `try/except`, so a crafted puzzle reveal that a wallet is asked to process (via a coin sent to it, a DID launcher spend, or `find_lost_did`) can raise an uncaught `AssertionError` and halt wallet transaction/sync processing for that flow.

Root cause: `chia/wallet/util/compute_hints.py` lines 52-53 use `assert` instead of raising `ValidationError`/skipping malformed conditions, unlike the cost-check three lines above it (`raise ValidationError(Err.BLOCK_COST_EXCEEDS_MAX, ...)`).

### Impact Explanation
This is a wallet-side denial-of-service/logic-halt bug reachable by any party who can get a crafted puzzle reveal in front of a victim wallet (e.g., a coin sent to the wallet's tracked puzzle hashes, or a DID-related spend the wallet is asked to sync/identify). It does not corrupt consensus state or allow theft directly, but it can:
- Crash/halt wallet sync or DID-processing code paths (unhandled `AssertionError`), and
- In the `Offer`/`CRCAT` call sites, silently swallow the hint/addition computation via broad `except Exception`, which can produce an `Offer` object with incomplete `_additions`/`_hints` caches — a correctness risk for downstream offer validation/settlement logic that trusts these caches.

Given the constraints (Medium severity, no low/resource-only findings), this qualifies as a spend-triggered processing halt on the wallet side, analogous to the NULL-deref DoS in the original CVE, rather than a supply-inflation or fund-theft bug.

### Likelihood Explanation
Likelihood is moderate: constructing a puzzle whose CLVM output places a cons pair (rather than an atom) in the `CREATE_COIN` puzzle-hash slot is trivial with CLVM (`(51 (c 1 2) 100)`), and delivering such a coin/spend to a victim wallet only requires sending a coin to an address the wallet tracks, or handing the wallet a crafted offer/DID spend to inspect. No privileged position or malicious peer/node capability is required — it fits the "wallet user" / "offer counterparty" / "DID owner" reachable-path constraint.

### Recommendation
Replace the bare `assert rf is not None` in `compute_spend_hints_and_additions` with an explicit check that raises `ValidationError` (matching the existing cost-check pattern), and remove/avoid catching this class of malformed-condition error with an overly broad `except Exception` in `Offer.__post_init__`/`add_crcat_coin` so that malformed spends are surfaced as validation failures rather than silently dropped or allowed to crash.

### Proof of Concept
1. Construct a puzzle whose solution causes it to output a `CREATE_COIN` condition with a non-atom second argument, e.g. CLVM: `(mod (amount) (list (list 51 (c 1 1) amount)))`.
2. Create a `CoinSpend` using that puzzle/solution as the `puzzle_reveal`/`solution` for a coin sent to an address the target wallet controls (or embed it in a DID/offer coin spend that the wallet is made to process, e.g. via `find_lost_did` or `DidWallet.identify`).
3. When the wallet processes the incoming coin state and calls `compute_spend_hints_and_additions(coin_spend)` (from `chia/wallet/wallet_state_manager.py` or `chia/wallet/did_wallet/did_wallet.py`), `condition.at("rf").atom` is `None` for the crafted condition, and `assert rf is not None` raises an uncaught `AssertionError`, halting that wallet processing path.

Note: I was unable to fully trace every runtime guard around `wallet_state_manager.py`'s async sync loop (e.g., whether an outer `try/except` at a higher level in the sync driver ultimately catches and logs this `AssertionError` without crashing the whole wallet process); this would need to be confirmed by tracing the callers of `_add_coin_state`/`coin_added` up to the top-level sync loop, which was not fully covered by the indexed context available.

### Citations

**File:** chia/wallet/wallet_state_manager.py (L2437-2437)
```python
        hinted_coins, _ = compute_spend_hints_and_additions(coin_spend)
```

**File:** chia/wallet/did_wallet/did_wallet.py (L463-464)
```python
        hinted_coin = compute_spend_hints_and_additions(coin_spend)[0][coin_state.coin.name()]
        assert hinted_coin.hint is not None, f"hint missing for coin {hinted_coin.coin}"
```

**File:** chia/wallet/trading/offer.py (L169-181)
```python
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
```
