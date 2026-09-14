### Title
Cubic-time coin-announcement dependency resolution in `Offer.get_cancellation_coins()` enables offer-triggered wallet CPU DoS - (File: `chia/wallet/trading/offer.py`)

### Summary
`Offer.get_cancellation_coins()` in [1](#0-0)  resolves which coins must be spent to invalidate an entire offer by repeatedly calling `detect_dependent_coin()` in a `while True:` loop until no more dependent coins are found. Each call to `detect_dependent_coin()` performs a full triple-nested scan over the remaining coin names, their announcement-assertion dependencies, and every other coin's created announcements, as shown in [2](#0-1) . Because the outer loop removes at most one dependency chain per iteration and then re-scans the (still largely unchanged) data structures from scratch, the total work is polynomial (effectively cubic in the worst case) in the number of coin spends in the offer bundle — directly analogous to the mistune advisory's pattern of "re-scanning from scratch for every new element" instead of amortizing the work.

### Finding Description
`get_cancellation_coins()` builds `dependencies` (coin → announcements it asserts) and `announcements` (coin → announcements it creates) maps for every non-addition coin spend in the offer's underlying `SpendBundle`, shown at [3](#0-2) . It then enters a loop:

```
while True:
    removed = detect_dependent_coin(coin_names, dependencies, announcements)
    ...
```

`detect_dependent_coin()` iterates `names × deps[name] × announcement_dict.items()` on every single call [2](#0-1) . In the worst case (a chain of N coins where each asserts the announcement of the previous coin, a completely legitimate offer pattern used for chaining spends), each outer-loop iteration only removes one coin, but must re-scan close to the full remaining N×N×N search space to find it, giving roughly O(N³) total work for N chained coin spends. There is also an inner nested loop (lines 456-468) that repeatedly does `set()` intersections and dict pops over all remaining coins per removal round, compounding the cost further.

This mirrors the mistune bug class exactly: a linear amount of attacker-supplied structure (many reference-link definitions / many chained coin spends) is processed by an algorithm that re-derives global state from scratch on every unit of progress instead of amortizing the scan, producing quadratic-or-worse blowup.

### Impact Explanation
`get_cancellation_coins()` is exposed to any offer a wallet has stored, and is invoked from `chia/wallet/trade_manager.py` and `chia/wallet/wallet_rpc_api.py` when a wallet cancels a trade (e.g., via the `cancel_offer`/`cancel_offers` RPC endpoints, confirmed present via grep matches in both files). The offer itself — including the number and dependency chaining of its constituent coin spends — is attacker-controlled: an offer counterparty crafts the offer file, and it is deserialized and later processed by the receiving wallet (or the offer's own maker, when cancelling their own outstanding offer) without any bound on chain depth of coin-announcement dependencies. A malicious offer with many chained announcement-dependent coin spends (bounded only by the mempool/block CLVM cost ceiling, which still permits several thousand minimal-cost spends, as demonstrated by the `test_many_create_coin` benchmark handling ~6094 conditions in `chia/_tests/core/mempool/test_mempool.py`) can make `get_cancellation_coins()` take vastly longer than a normal offer, stalling the wallet RPC / trade-manager processing for that request. This is a local, spend/offer-triggered CPU-exhaustion condition on the wallet, matching the "spend-triggered transaction-processing halt" impact class.

### Likelihood Explanation
An unprivileged offer counterparty fully controls the shape of an offer's `SpendBundle`, including how many coin spends it contains and how they chain coin announcements to each other — this is a normal, legal offer-construction pattern, not a protocol violation. No signature or on-chain validity is required to trigger the vulnerable code path locally; `get_cancellation_coins()` only requires a syntactically well-formed offer object that a wallet or trade manager decides to inspect for cancellation. This makes exploitation straightforward for anyone who can hand a crafted offer file to a victim's wallet or who creates their own oversized offer and later cancels it, causing that RPC call/thread to hang.

### Recommendation
Rewrite `get_cancellation_coins()`/`detect_dependent_coin()` to use a single forward pass with hash-indexed lookups (e.g., build a reverse index from announcement hash → creating coin once, and use a work-queue/BFS over dependency edges) so that dependency-chain resolution is amortized to O(N) or O(N log N) instead of being re-derived from scratch on every removal. Add a regression benchmark asserting that `get_cancellation_coins()` completes within a bounded time for a synthetic offer with several thousand chained coin-announcement dependencies, mirroring the `benchmark_runner.assert_runtime` pattern already used elsewhere in `chia/_tests/core/mempool/test_mempool.py`.

### Proof of Concept
Construct (or have a counterparty construct) an `Offer` whose underlying `SpendBundle` contains N coin spends where coin `i` asserts a coin announcement created by coin `i-1` (a linear announcement-dependency chain), for N in the low thousands (bounded by CLVM cost limits, e.g. similar magnitude to the 6094-condition case benchmarked in `test_many_create_coin`). Call `offer.get_cancellation_coins()` (reachable via the wallet's `cancel_offer`/`cancel_offers` RPC path) and observe that runtime grows non-linearly (polynomially) with N due to the repeated full re-scans in `detect_dependent_coin()` inside the `while True` loop at [4](#0-3) , versus near-linear growth for a non-chained (independent) set of N coin spends of the same size.

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

**File:** chia/wallet/trading/offer.py (L425-470)
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
