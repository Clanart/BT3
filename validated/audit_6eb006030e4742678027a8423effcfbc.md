### Title
Cubic/exponential-complexity coin dependency resolution when cancelling offers - (File: chia/wallet/trading/offer.py)

### Summary
`Offer.get_cancellation_coins()` and its helper `detect_dependent_coin()` in [1](#0-0)  resolve which coins in an offer's spend bundle can be used to cancel the whole offer by walking coin-announcement dependencies. The resolution algorithm is a nested loop with super-linear (worst case cubic or worse) complexity in the number of coin spends contained in the offer, similar in class to the Suricata SMTP/MIME quadratic URL-scanning bug: both scan an untrusted, attacker-influenced structure (MIME parts / offer coin spends) with nested loops whose cost grows much faster than linearly with input size.

### Finding Description
`detect_dependent_coin()` triple-nest-iterates over `names`, `deps[name]`, and `announcement_dict.items()` [2](#0-1) . It is invoked repeatedly inside the `while True:` loop in `get_cancellation_coins()` [3](#0-2) , and that outer loop itself contains a second nested `while True:` loop that iterates `dependencies.items()` against `removed_announcements` sets [4](#0-3) . Because `detect_dependent_coin` is called once per outer iteration and does O(N) × O(deps) × O(announcements) work internally, and the outer loop itself can run up to O(N) times (each iteration removes at least one coin), the overall complexity is polynomial (cubic or worse) in the number of coin spends `N` inside a single offer's `SpendBundle`.

An offer file is an untrusted artifact that any offer counterparty can hand to a wallet user (or an attacker can craft and send). The number of coin spends and the number of announcement/assert-announcement conditions per spend are attacker-controlled up to the mempool/offer size limits, so an attacker can construct an offer with many coin spends chained together via `CREATE_COIN_ANNOUNCEMENT`/`ASSERT_COIN_ANNOUNCEMENT` conditions (opcodes 60/61, see lines 438–443) to maximize the number of dependency chains and force worst-case behavior in `detect_dependent_coin`.

### Impact Explanation
This matches the "spend-triggered transaction-processing halt" impact category: a single malicious offer, when a wallet user attempts to inspect it, calculates cancellation coins for it, or cancels a pending trade, can cause the wallet process to spend a disproportionate amount of CPU time in `get_cancellation_coins()`, causing a denial of service in wallet transaction processing for that user. Because `get_cancellation_coins()` runs client-side (not part of consensus), it does not directly cause block/mempool consensus divergence, but it can hang or significantly stall the affected wallet's transaction handling pipeline for a low cost to the attacker (just crafting an offer with a large number of correlated coin spends and announcements).

### Likelihood Explanation
Moderate. The offer content (spend count, condition structure) is fully controlled by whoever creates the offer file (an untrusted "offer counterparty"), and offers are routinely passed around and processed automatically by wallet software before a user chooses to accept or cancel them. However, this path is only reached in the cancellation flow, and I was not able to fully confirm within the tool-call budget the exact call site(s) in `chia/wallet/trade_manager.py` that invoke `get_cancellation_coins()` (only one match was found; its surrounding trigger conditions — e.g. whether it's auto-invoked when receiving/inspecting an offer, or only on explicit user-initiated cancel — were not fully verified).

### Recommendation
- Replace the O(N) repeated linear/triple-nested scans in `detect_dependent_coin()`/`get_cancellation_coins()` with a single-pass graph algorithm: build a reverse index from announcement message → coin name once, and use it to do a union-find or BFS/DFS-based dependency-chain collapse in O(N) or O(N log N) instead of re-scanning `announcement_dict` for every dependency of every remaining coin on every outer-loop iteration.
- Cap the number of coin spends / conditions processed by `get_cancellation_coins()` and reject/short-circuit with a clear error for oversized offers, consistent with other spend-bundle-derived DoS protections already present in mempool code (e.g., `MAX_SPENDS_PER_BLOCK`, `MAX_BLOCK_ATOMS`/`MAX_BLOCK_PAIRS` limits and the 2-second mempool validation duration guard mentioned in `.cursor/context/mempool.md`).
- Add a wall-clock or iteration-count timeout around offer-cancellation-coin computation so a pathological offer cannot stall the wallet indefinitely.

### Proof of Concept
Conceptual PoC (not executed, due to lack of terminal access in this session):
1. Construct an offer `SpendBundle` with `N` coin spends (e.g., N = 500–2000), each producing a `CREATE_COIN_ANNOUNCEMENT` and consuming an `ASSERT_COIN_ANNOUNCEMENT` from a different coin in the bundle, forming a long dependency chain (coin₁ → announces used by coin₂, coin₂ → announces used by coin₃, etc.), while staying within `MAX_BLOCK_COST_CLVM`/offer size limits.
2. Serve this offer file to a target wallet (e.g., via the standard offer exchange flow) and have the wallet process cancellation for it (`trade_manager` → `Offer.get_cancellation_coins()`).
3. Measure wall-clock time of `get_cancellation_coins()` versus N to observe super-linear growth, analogous to how the Suricata bug's URL-extraction routine grows quadratically with the number of MIME parts/URLs.

Note: I could not fully verify the exact reachability path (auto-triggered vs. explicit user action) from `trade_manager.py` into `get_cancellation_coins()` within the available tool budget; a deeper investigation of `chia/wallet/trade_manager.py`'s cancellation call sites is recommended to confirm precise trigger conditions before treating this as fully validated.

### Citations

**File:** chia/wallet/trading/offer.py (L53-63)
```python
def detect_dependent_coin(
    names: list[bytes32], deps: dict[bytes32, list[bytes32]], announcement_dict: dict[bytes32, list[bytes32]]
) -> tuple[bytes32, bytes32] | None:
    # First, we check for any dependencies on coins in the same bundle
    for name in names:
        for dependency in deps[name]:
            for coin, announces in announcement_dict.items():
                if dependency in announces and coin != name:
                    # We found one, now remove it and anything that depends on it (except the "provider")
                    return name, coin
    return None
```

**File:** chia/wallet/trading/offer.py (L449-452)
```python
        while True:
            removed = detect_dependent_coin(coin_names, dependencies, announcements)
            if removed is None:
                break
```

**File:** chia/wallet/trading/offer.py (L456-468)
```python
            while True:
                for coin, deps in dependencies.items():
                    if set(deps) & set(removed_announcements) and coin != provider:
                        remove_these_keys.append(coin)
                removed_announcements = []
                for coin in remove_these_keys:
                    dependencies.pop(coin)
                    removed_announcements.extend(announcements.pop(coin))
                coin_names = [n for n in coin_names if n not in remove_these_keys]
                if removed_announcements == []:
                    break
                else:
                    remove_these_keys = []
```
