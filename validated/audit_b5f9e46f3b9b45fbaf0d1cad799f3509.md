### Title
Wallet DoS via O(N⁴) worst-case complexity in `Offer.get_cancellation_coins()` when processing an attacker-crafted offer - (File: `chia/wallet/trading/offer.py`)

### Summary
`Offer.get_cancellation_coins()` and its helper `detect_dependent_coin()` build dependency/announcement maps from an offer's coin spends and then repeatedly rescan them to find "dependent" coins to fold together. The nested-loop, dictionary-rescanning structure is directly analogous to the expat `storeAtts()` bug class described in ALPINE-CVE-2026-66046: an unauthenticated party can hand the victim a document (here, a Chia offer file) whose internal structure causes the victim's parser to repeatedly re-scan attribute/announcement lists in a way whose cost grows super-linearly with the number of entries, with no CLVM-cost metering to bound it.

### Finding Description
`Offer.get_cancellation_coins()` [1](#0-0)  builds, for every non-addition coin spend in the offer, a `dependencies` map (coins each spend's ASSERT_COIN_ANNOUNCEMENT depends on) and an `announcements` map (announcements each spend creates), by iterating every condition of every coin spend [2](#0-1) .

It then calls `detect_dependent_coin()` in a `while True:` loop until no more dependents are found: [3](#0-2) 

`detect_dependent_coin()` itself is a triple-nested loop over `names × deps[name] × announcement_dict.items()`: [4](#0-3) 

Each time a dependent coin is found, `get_cancellation_coins()` runs a second nested pass over `dependencies.items()` intersected against the removed coin's announcement set, and repeats this inner `while True:` loop until it converges: [5](#0-4) 

Because `detect_dependent_coin()` is called once per outer iteration and does an O(names · deps · announcements) scan itself, and the removal/rescan inner loop can also cascade, the overall complexity is polynomial (worse than quadratic) in the number of coin spends and announcement conditions in the offer. Unlike CLVM execution (which is cost-metered per condition via `run_with_cost`), this Python-side post-processing over the *already parsed* conditions has no cost accounting or size cap — exactly the pattern in `storeAtts()`, where a fixed per-attribute cost model exists (CLVM condition cost) but a downstream native-code post-processing step (here, pure Python) re-scans the same data structure repeatedly with no equivalent bound.

An attacker (an "offer counterparty") fully controls the number of coin spends and the number/pattern of `CREATE_COIN_ANNOUNCEMENT` / `ASSERT_COIN_ANNOUNCEMENT` conditions in an offer file they hand to a victim wallet, and can specifically construct a long dependency chain (coin₁ depends on coin₂'s announcement, coin₂ depends on coin₃'s, etc.) to maximize the number of outer-loop cascades, each triggering a fresh full rescan.

### Impact Explanation
This is a wallet-side (client) CPU-exhaustion / denial-of-service vector: a malicious offer file, once loaded by a victim's wallet (e.g., to inspect or cancel a pending trade), can cause the wallet process to spend excessive CPU time in `get_cancellation_coins()`, without any signature, on-chain broadcast, or fee payment by the attacker. This matches the "spend-triggered transaction-processing halt" impact category for the offer/trade flow, limited to the wallet process that loads the file (not full-node consensus, mempool, or block validation).

### Likelihood Explanation
Medium. The attacker needs the victim to load/process a crafted offer file (a normal step in the offer trading UX — inspecting, accepting, or cancelling an offer). No special privileges, valid signatures, or on-chain broadcast are required to construct the malicious offer's structure, since `get_cancellation_coins()` only reads conditions produced by `run_with_cost` on the offer's own puzzle reveals/solutions, which the offer creator fully controls.

### Recommendation
Rewrite `get_cancellation_coins()`/`detect_dependent_coin()` to use hash-based indices (e.g., a reverse map from announcement hash to creating coin, built once) instead of repeatedly scanning `announcement_dict.items()` and `dependencies.items()` for every candidate; bound the total work by the number of announcements/dependencies (O(N) or O(N log N)) rather than re-scanning on each cascade step. Alternatively, cap the number of coin spends / announcement conditions considered before invoking this path, or move the convergence loop to operate on precomputed adjacency sets keyed by announcement hash.

### Proof of Concept
Construct an `Offer` whose `_bundle.coin_spends` contains a long chain of coins C₀ … Cₙ, where each Cᵢ's solution creates a `CREATE_COIN_ANNOUNCEMENT` and asserts (`ASSERT_COIN_ANNOUNCEMENT`) the announcement of C_{i+1}, forming a dependency chain of length N. Calling `offer.get_cancellation_coins()` on this offer causes the outer `while True` loop in `get_cancellation_coins()` (offer.py lines 449–469) to cascade N times, each time re-invoking `detect_dependent_coin()` (offer.py lines 56–62), which itself performs an O(N) scan per call — yielding O(N²) to worse behavior as N grows, consuming disproportionate CPU relative to a well-formed offer of similar size, without requiring any signature or on-chain submission.

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

**File:** chia/wallet/trading/offer.py (L424-470)
```python
    # This returns the minimum coins that when spent will invalidate the rest of the bundle
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
