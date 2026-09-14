### Title
Quadratic-Complexity Coin Dependency Resolution in `Offer.get_cancellation_coins()` Enables Wallet CPU Exhaustion via Crafted Offer - (File: chia/wallet/trading/offer.py)

### Summary
`Offer.get_cancellation_coins()` in `chia/wallet/trading/offer.py` resolves coin-announcement dependencies among all coin spends in an offer using nested loops that repeatedly build `set()` objects and re-scan the full `dependencies`/`announcements` dictionaries on every removal round, mirroring the same complexity class as the Elysia `getAll`-based form-data normalization bug (CVE-2026-56669): work grows superlinearly with the number of unique coin/announcement entries, allowing an attacker-supplied offer with many coin spends and cross-referencing announcements to trigger CPU exhaustion on the machine processing the offer.

### Finding Description
`get_cancellation_coins` first builds per-coin `dependencies` and `announcements` maps for every non-addition coin spend in the offer [1](#0-0) . It then enters an outer `while True` loop that calls `detect_dependent_coin(coin_names, dependencies, announcements)` to find a coin whose announcement dependency is satisfied by another coin in the bundle, and, for every such match, runs an *inner* `while True` loop that iterates over **all** entries of `dependencies.items()`, computing a fresh `set(deps) & set(removed_announcements)` for each entry, to discover any coin depending on the just-removed coin's announcements: [2](#0-1) 

This inner loop is executed repeatedly (once per newly discovered dependent coin), and each iteration re-scans the entire remaining `dependencies` map and rebuilds `set()` objects from scratch rather than using an incrementally maintained index (e.g., a reverse-lookup from announcement hash to dependent coin). For a bundle with `N` coin spends chained together via `CREATE_COIN_ANNOUNCEMENT`/`ASSERT_COIN_ANNOUNCEMENT` conditions, the outer/inner loop structure combined with the O(N) dictionary scan per removal step yields quadratic (or worse, depending on how `detect_dependent_coin` itself scans) growth in Python-level work, all before any CLVM cost accounting applies (this logic runs entirely in wallet Python code, not in the metered CLVM execution).

This is directly analogous to the Elysia vulnerability class: normalization/grouping work over a set of "unique keys" (announcement hashes / coin ids) that should be O(N) is instead implemented with repeated full-collection scans and repeated `set()` construction, making the total cost O(N^2) or worse in the number of coin spends/announcements in the input.

### Impact Explanation
`get_cancellation_coins()` is invoked from wallet trade-management code path (`chia/wallet/trade_manager.py`) when a wallet needs to determine which coins to spend in order to cancel a trade/offer. An attacker who crafts a malicious offer file with a large number of coin spends and a dense web of `CREATE_COIN_ANNOUNCEMENT`/`ASSERT_COIN_ANNOUNCEMENT` conditions between them (offers can contain arbitrary numbers of coin spends up to block-cost limits, and processing an offer's cancellation set is done client-side in Python, independent of CLVM cost metering) can cause the receiving wallet's process to spend disproportionate CPU time in this nested-loop/set-rebuilding logic. Since this runs synchronously in wallet code (not sandboxed by CLVM cost), it can stall the wallet's transaction-processing (e.g., cancel-offer or take-offer flows), degrading availability for the wallet user handling the offer — a spend/offer-triggered transaction-processing slowdown/halt, consistent with the "unprivileged ... offer counterparty" reachability the scan is looking for.

### Likelihood Explanation
An unprivileged offer counterparty fully controls the content of an offer file (coin spends, puzzle reveals/solutions producing arbitrary announcement conditions), so triggering many chained announcement dependencies is straightforward and does not require any privileged access, valid signature, or on-chain broadcast — the cost is paid purely by whichever wallet locally computes `get_cancellation_coins()` (e.g., when the recipient examines/cancels the offer). No mining or block inclusion is required to trigger the expensive computation, making this readily reachable, though the resulting cost only affects the local wallet doing the calculation (not other honest nodes directly), which somewhat bounds the blast radius compared to a full network-wide DoS.

### Recommendation
Rewrite the dependency-resolution logic in `get_cancellation_coins()` to use amortized-linear data structures: maintain a reverse index from each announcement hash to the set of coin names that depend on it (built once), and use a work-queue/BFS approach to propagate removals instead of rescanning `dependencies.items()` and rebuilding `set()` objects on every iteration of the inner loop. This reduces the overall algorithm to O(N) or O(N log N) in the number of coin spends and announcements, eliminating the quadratic blowup while preserving the same cancellation semantics.

### Proof of Concept
Conceptual PoC (cannot be executed in this environment, but derivable from the code path):
1. Construct an `Offer` whose underlying `SpendBundle` contains `N` coin spends (e.g., N = 5,000–10,000), each puzzle-reveal/solution emitting one `CREATE_COIN_ANNOUNCEMENT` and one `ASSERT_COIN_ANNOUNCEMENT` condition chained so that coin `i` depends on the announcement created by coin `i-1`, and additionally have many coins share overlapping announcement dependencies to maximize the number of times the inner `while True` loop (lines 456–468) must rescan `dependencies.items()`.
2. Serve this offer to a wallet (e.g., via the offer file exchange mechanism used by `chia offers take`/`chia offers cancel`, ultimately calling into `TradeManager` → `Offer.get_cancellation_coins()`).
3. Measure wall-clock time of `get_cancellation_coins()` as `N` scales; observe superlinear growth (doubling `N` more than doubling runtime) matching the quadratic-scan pattern in [2](#0-1) , analogous to the `getAll`-based quadratic blowup described in CVE-2026-56669.

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

**File:** chia/wallet/trading/offer.py (L445-470)
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
                else:
                    remove_these_keys = []

        return [cs.coin for cs in self._bundle.coin_spends if cs.coin.name() in coin_names]
```
