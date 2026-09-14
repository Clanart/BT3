### Title
Potential denial-of-service (infinite loop / crash) when computing offer cancellation coins from an untrusted offer - (File: `chia/wallet/trading/offer.py`)

### Summary
`Offer.get_cancellation_coins()` in `chia/wallet/trading/offer.py` contains a nested `while True` loop that walks announcement/assertion dependency graphs built directly from an untrusted offer's `SpendBundle` conditions. Because the loop-termination logic depends on graph shape derived from attacker-controlled `CREATE_COIN_ANNOUNCEMENT`/`ASSERT_COIN_ANNOUNCEMENT` condition data, a maliciously crafted offer can prevent the loop from converging or cause a crash, similar in class to CVE‑2021‑1252 (improper error handling causing an infinite loop while parsing an attacker-supplied file).

### Finding Description
`get_cancellation_coins()` builds two dictionaries, `dependencies` and `announcements`, per non-ephemeral coin in the offer bundle, populated by iterating the conditions produced by running each coin's `puzzle_reveal`/`solution` [1](#0-0) . It then repeatedly calls `detect_dependent_coin(coin_names, dependencies, announcements)` in an outer `while True`, and for each match runs an inner `while True` loop that removes coins whose dependencies intersect the newly removed coin's announcements [2](#0-1) .

The inner loop's termination condition is `if removed_announcements == []: break`, and `remove_these_keys` can be populated with the same coin multiple times if several remaining coins independently match the removed-announcement set (since the code appends to `remove_these_keys` for every matching `(coin, deps)` pair without deduplication) [3](#0-2) . If a coin name ends up in `remove_these_keys` twice, `dependencies.pop(coin)` / `announcements.pop(coin)` is called twice for the same key, raising `KeyError` on the second pop — an unhandled exception during offer processing. More generally, because `dependencies`/`announcements` are populated purely from attacker-supplied `CREATE_COIN_ANNOUNCEMENT`/`ASSERT_COIN_ANNOUNCEMENT` args in the offer's puzzle solutions, a counterparty can construct cyclic or degenerate announcement graphs (e.g., two or more coins whose announcement hashes cross-reference each other) that are not proven to converge with the current termination check, since there is no bound on iteration count and no cycle detection.

This mirrors the CVE-2021-1252 bug class: a document/file parser trusts attacker-controlled structure to drive loop termination, and a crafted input can defeat that termination logic, hanging or crashing the parsing process.

### Impact Explanation
`get_cancellation_coins()` is invoked when a wallet inspects an offer to determine which coins must be spent to cancel it — a code path reachable by any wallet user processing an offer file received from an untrusted counterparty (offers are shared out-of-band and examined locally before acceptance/cancellation). A crafted offer can cause the wallet process handling it to raise an unhandled `KeyError` or spin in the nested loop, denying service to the wallet's offer-management functionality. This does not lead to fund loss or consensus divergence, so it is bounded to a DoS/availability issue in the wallet's local RPC/CLI offer-cancellation flow.

### Likelihood Explanation
Likelihood is moderate: constructing an offer with multiple settlement coins carrying `CREATE_COIN_ANNOUNCEMENT`/`ASSERT_COIN_ANNOUNCEMENT` conditions is straightforward with standard offer-construction tooling (no signature validity is required for the conditions to be parsed by this function, since `get_cancellation_coins()` only runs the puzzle to extract conditions, not to validate signatures). Reaching the vulnerable duplicate-removal or non-convergent path requires careful crafting of announcement/assertion hash overlaps across several coins, which is inconvenient to verify precisely from static review alone without exercising `detect_dependent_coin`'s exact matching semantics (its implementation was not retrievable in this pass).

### Recommendation
Add cycle detection and idempotent bookkeeping in `get_cancellation_coins()`: deduplicate `remove_these_keys` before iterating, guard `dependencies.pop`/`announcements.pop` calls with membership checks (or use `dict.pop(coin, None)`), and add an explicit iteration cap or visited-set check in both `while True` loops to guarantee termination regardless of attacker-supplied announcement graph shape. Treat offer-derived condition data as untrusted input throughout this method.

### Proof of Concept
A concrete PoC requires constructing an offer `SpendBundle` where two or more non-ephemeral coins' puzzle solutions emit `CREATE_COIN_ANNOUNCEMENT`/`ASSERT_COIN_ANNOUNCEMENT` conditions such that multiple coins simultaneously satisfy the removal condition against the same `removed_announcements` set in `chia/wallet/trading/offer.py:457-463`, causing `remove_these_keys` to contain a duplicate coin id and triggering a `KeyError` on the second `dependencies.pop(coin)` call when `Offer.get_cancellation_coins()` is invoked on the resulting offer file. Full validation of this PoC (and of whether truly cyclic dependency graphs can prevent outer-loop convergence) requires inspecting `detect_dependent_coin`'s implementation, which could not be located in the indexed code during this investigation.

### Citations

**File:** chia/wallet/trading/offer.py (L425-443)
```python
    def get_cancellation_coins(self) -> list[Coin]:
        # First, we're going to gather:
        dependencies: dict[bytes32, list[bytes32]] = {}  # all of the hashes that each coin depends on
        announcements: dict[bytes32, list[bytes32]] = {}  # all of the hashes of the announcement that each coin makes
        coin_names: list[bytes32] = []  # The names of all the coins
        additions = self.additions()
        for spend in [cs for cs in self._bundle.coin_spends if cs.coin not in additions]:
            name = bytes32(spend.coin.name())
            coin_names.append(name)
            dependencies[name] = []
            announcements[name] = []
            conditions: Program = run_with_cost(spend.puzzle_reveal, INFINITE_COST, spend.solution)[1]
            for condition in conditions.as_iter():
                if condition.first() == 60:  # create coin announcement
                    announcements[name].append(
                        AssertCoinAnnouncement(asserted_id=name, asserted_msg=condition.at("rf").as_python()).msg_calc
                    )
                elif condition.first() == 61:  # assert coin announcement
                    dependencies[name].append(bytes32(condition.at("rf").as_python()))
```

**File:** chia/wallet/trading/offer.py (L449-468)
```python
        while True:
            removed = detect_dependent_coin(coin_names, dependencies, announcements)
            if removed is None:
                break
            removed_coin, provider = removed
            removed_announcements: list[bytes32] = announcements[removed_coin]
            remove_these_keys: list[bytes32] = [removed_coin]
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
