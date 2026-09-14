### Title
Cubic-complexity coin-cancellation graph walk in `Offer.get_cancellation_coins()` enables spend-triggered processing halt - (File: chia/wallet/trading/offer.py)

### Summary
`Offer.get_cancellation_coins()` builds a dependency/announcement graph from every non-addition coin spend in a received offer and then repeatedly calls `detect_dependent_coin()` in a nested loop until no more dependent coins are found. `detect_dependent_coin()` itself is a triple-nested loop over `names × deps × announcement_dict`, and the outer `while True` loop in `get_cancellation_coins()` re-scans the shrinking coin/announcement maps on every removal. This mirrors the Suricata krb5 bug class: an algorithm whose cost scales worse than linearly with attacker-controlled input size, causing a caller-triggered performance/DoS condition rather than a memory-safety bug.

### Finding Description
`get_cancellation_coins()` in `chia/wallet/trading/offer.py` (lines 425-470) runs each coin spend's puzzle/solution to collect `CREATE_COIN_ANNOUNCEMENT` (opcode 60) and `ASSERT_COIN_ANNOUNCEMENT` (opcode 61) conditions into two dictionaries keyed by coin name: [1](#0-0) 

It then enters an outer `while True` loop that calls `detect_dependent_coin(coin_names, dependencies, announcements)` to find one dependent coin at a time, and, once found, an inner `while True` loop that scans **all** `dependencies.items()` against the just-removed announcements to find every coin that depended on the removed one, repeating until no more announcements cascade: [2](#0-1) 

`detect_dependent_coin()` itself is a triple-nested loop: for every coin name, for every dependency of that coin, for every `(coin, announces)` pair in the announcement dictionary, checking set membership: [3](#0-2) 

Because this function returns only the *first* match found and the outer `get_cancellation_coins()` loop reruns `detect_dependent_coin()` from scratch after every single removal, the overall complexity for an offer containing `N` coin spends, each with `D` announcement/assertion conditions, can approach `O(N^2 · D)` to `O(N^3)` in adversarially constructed dependency chains (e.g., a long chain of coins each asserting the previous one's announcement, causing worst-case single-item removals per outer iteration). This is analogous to the krb5 quadratic-buffering bug: the cost of processing a bounded-size external input (the offer bytes / a coin_spends list) grows much faster than the input size, without any cost accounting or iteration cap.

### Impact Explanation
An offer counterparty can hand-craft (or `Offer.from_bytes`-decode) a `WalletSpendBundle` with a large number of coin spends, each carrying `CREATE_COIN_ANNOUNCEMENT`/`ASSERT_COIN_ANNOUNCEMENT` conditions arranged as a long dependency chain, then send this offer file to a victim wallet. When the wallet later needs to compute cancellation coins for that trade (a normal, wallet-user-triggered "cancel offer" action reachable via `trade_manager.py`), the CPU cost of `get_cancellation_coins()` scales super-linearly with the number of coins/announcements the attacker embedded in the offer, well beyond what the underlying CLVM cost accounting for the bundle would suggest. This can stall the wallet process (a single-threaded async loop) for an extended period on a request that should be cheap, producing a spend-triggered transaction-processing halt for the victim's wallet during trade cancellation. This satisfies the "spend-triggered transaction-processing halt" impact category from the validation criteria.

### Likelihood Explanation
Likelihood is moderate: the attacker (an offer counterparty) fully controls the offer's coin spend structure and announcement/assertion graph, and offers routinely originate from untrusted counterparties in the normal trading flow (`take_offer`, `cancel_offer`). No special privileges are required — this is reachable purely through the standard offer-creation/exchange workflow that any wallet user can trigger by receiving and later canceling a maliciously constructed offer. The number of coins an offer can practically contain is bounded by CLVM cost and mempool spend-count limits, which somewhat limits the worst-case blow-up compared to an unbounded network buffer, but a moderately sized offer (tens to low hundreds of coins with chained announcements) is well within normal cost limits while still making this graph-walk disproportionately expensive.

### Recommendation
Refactor `get_cancellation_coins()`/`detect_dependent_coin()` to avoid re-scanning the full dependency/announcement maps on every single coin removal. Build a single announcement→coin index once (e.g., `dict[bytes32, bytes32]` mapping announcement hash to producing coin) and use it to do the cascade removal in a single linear/BFS pass over `coin_names`/`dependencies`, rather than the current nested-loop-per-removal approach. Add a hard cap on the number of iterations (or fall back to a coarser, but bounded-cost, cancellation strategy) proportional to the number of coin spends in the offer to bound worst-case cost.

### Proof of Concept
Conceptual PoC (not executed): construct an `Offer` whose `_bundle.coin_spends` contains `N` coins `C_1 .. C_N`, where each `C_i` creates a coin announcement consumed by `C_{i+1}`'s `ASSERT_COIN_ANNOUNCEMENT`, forming a single long dependency chain, and none of these coins appear in `self.additions()`. Calling `offer.get_cancellation_coins()` on this bundle causes `detect_dependent_coin()` (triple-nested loop) to be re-invoked from scratch by the outer `while True` loop for each of the `N` cascading removals, yielding measurable superlinear growth in wall-clock time as `N` increases (e.g., timing the call for `N = 50, 100, 200, 400` coins and observing non-linear scaling), consistent with the quadratic/cubic complexity identified in the code paths cited above.

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

**File:** chia/wallet/trading/offer.py (L445-466)
```python
        # We now enter a loop that is attempting to express the following logic:
        # "If I am depending on another coin in the same bundle, you may as well cancel that coin instead of me"
        # By the end of the loop, we should have filtered down the list of coin_names to include only those that will
        # cancel everything else
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
```
