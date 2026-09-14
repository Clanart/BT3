### Title
Unhandled `AssertionError` on malformed `CREATE_COIN` output in `compute_spend_hints_and_additions` can halt wallet spend-bundle/coin processing - ([File: chia/wallet/util/compute_hints.py])

### Summary
`compute_spend_hints_and_additions()` in `chia/wallet/util/compute_hints.py` runs an untrusted puzzle reveal/solution and then blindly assumes the resulting `CREATE_COIN` condition's puzzle-hash argument is always a CLVM atom, asserting on it rather than validating it. [1](#0-0)  This mirrors the Suricata `tls.alpn` bug class: an optional/attacker-influenced field is dereferenced/assumed-present without a defensive check, and the resulting exception is not a graceful parse failure but an assertion crash that can propagate out of the function into whatever wallet-processing loop invoked it.

### Finding Description
`compute_spend_hints_and_additions()` executes `cs.puzzle_reveal`/`cs.solution` (fully attacker-controlled CLVM for any coin the wallet is tracking — CAT, DID, NFT, VC/CR-CAT, or plain coins) and iterates the resulting conditions:

```python
rf = condition.at("rf").atom
assert rf is not None
coin: Coin = Coin(cs.coin.name(), bytes32(rf), uint64(condition.at("rrf").as_int()))
``` [2](#0-1) 

`Program.at(...).atom` returns `None` whenever the value at that CLVM path is a pair (cons cell) rather than an atom. A spend whose puzzle outputs a condition such as `(CREATE_COIN (X . Y) amount ...)` — i.e., a `CREATE_COIN` opcode whose second argument is a cons pair instead of a puzzle-hash atom — makes `condition.at("rf").atom` evaluate to `None`, triggering the `assert`. Because this is a bare Python `assert`, it raises an unhandled `AssertionError` rather than a typed `ValidationError`/`ValueError`, and unlike `Offer.__post_init__` (which happens to broadly catch `Exception` around its own call to this helper) [3](#0-2) , other call sites (`chia/wallet/wallet_state_manager.py`, `chia/wallet/cat_wallet/cat_wallet.py`, `chia/wallet/did_wallet/did_wallet.py`, `chia/wallet/vc_wallet/cr_cat_wallet.py`, `chia/wallet/trade_manager.py`) invoke this helper directly while processing coin spends related to a user's wallet. I was not able to confirm within the available search budget whether every one of these call sites wraps the call in an equivalent broad exception handler; this is a gap in my verification.

### Impact Explanation
If any of these unguarded call sites processes a coin spend whose puzzle emits a malformed `CREATE_COIN` condition (a cons instead of an atom for the puzzle hash), the `AssertionError` will propagate out of `compute_spend_hints_and_additions`. Depending on the call site, this can abort wallet-side processing of that spend bundle/coin (a spend-triggered transaction-processing halt for the affected wallet), since the exception is not converted into a typed, catchable error before reaching this function. This matches the "spend-triggered transaction-processing halt" impact category, reachable purely by a wallet observing or being sent a coin spend with adversarial CLVM output (e.g., via an offer, CAT/DID/NFT/VC coin, or a crafted transaction) — no privileged network position or malicious peer is required.

### Likelihood Explanation
Likelihood is Medium: constructing a puzzle whose `run()` output contains `(CREATE_COIN (X . Y) amount)` is straightforward CLVM (any custom inner puzzle can emit arbitrary condition lists), and any coin type the wallet tracks (CAT/DID/NFT/VC/plain) can carry such a puzzle. The main uncertainty is whether all real production call paths (as opposed to the one demonstrably-safe path in `Offer.__post_init__`) lack a broad exception handler around this call — I could not fully confirm this for `wallet_state_manager.py`, `cat_wallet.py`, `did_wallet.py`, and `cr_cat_wallet.py` within this investigation, so this should be treated as a probable but not fully confirmed root-cause chain to a full processing halt.

### Recommendation
Replace the bare `assert rf is not None` in `compute_spend_hints_and_additions` with an explicit check that raises a typed, catchable error (e.g., skip the malformed condition or raise `ValidationError`) instead of an `AssertionError`, and audit every call site (`wallet_state_manager.py`, `cat_wallet.py`, `did_wallet.py`, `cr_cat_wallet.py`, `trade_manager.py`) to ensure malformed/adversarial CLVM output from any tracked coin's puzzle cannot abort wallet processing of subsequent coins or blocks.

### Proof of Concept
1. Create/curry a puzzle whose solution causes it to output the condition list `[[CREATE_COIN, (0xAA . 0xBB), 1]]` — i.e., where the CREATE_COIN puzzle-hash argument is a CLVM pair, not an atom (any inner puzzle can be authored to do this, e.g., `(list 51 (c 0xAA 0xBB) 1)`).
2. Get this coin (as a CAT/DID/NFT/VC coin, or via an offer) into a state where the wallet calls `compute_spend_hints_and_additions` on its `CoinSpend` outside of a broad exception handler.
3. Observe `condition.at("rf").atom` evaluate to `None` and the subsequent `assert rf is not None` raise an `AssertionError`, propagating out of the helper and interrupting whatever wallet loop (state sync, CAT/DID/CR-CAT processing, or trade management) invoked it.

### Citations

**File:** chia/wallet/util/compute_hints.py (L48-56)
```python
        if op != ConditionOpcode.CREATE_COIN.value:
            continue
        cost += ConditionCost.CREATE_COIN.value

        rf = condition.at("rf").atom
        assert rf is not None

        coin: Coin = Coin(cs.coin.name(), bytes32(rf), uint64(condition.at("rrf").as_int()))
        hint: bytes32 | None = None
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
