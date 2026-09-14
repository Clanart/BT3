### Title
Infinite loop in `Offer.get_root_removal` via cyclic `parent_coin_info` references in an untrusted offer bundle - (File: chia/wallet/trading/offer.py)

### Summary
`get_root_removal()` walks a chain of coins backwards through `parent_coin_info` until it finds a coin considered "non-ephemeral" (a coin whose parent is not itself a removal in the bundle). The removal/coin set is taken directly from the offer's `SpendBundle`, which is attacker-controlled data (an offer file received from a counterparty). If a crafted offer bundle contains two or more coin entries whose `parent_coin_info` fields point at each other (a cycle), no coin in the cycle is ever classified as non-ephemeral, and the `while` loop never terminates.

### Finding Description
`get_root_removal` is defined as: [1](#0-0) 

It computes `non_ephemeral_removals` purely from the coin data embedded in the offer's `SpendBundle` (`self.removals()`, which comes from `self._bundle`), not from data verified against the actual blockchain state: [2](#0-1) 

A `Coin`'s `parent_coin_info`, `puzzle_hash`, and `amount` are simply fields chosen by whoever constructs the `CoinSpend`/`SpendBundle` — they are not required to reflect real coin lineage until independently validated against the coin store. An `Offer` object is built directly from bytes supplied by a counterparty (`Offer.from_bytes`) and its `removals()`/`additions()` are derived straight from that bundle without checking real coin ancestry: [3](#0-2) 

Because nothing prevents two coins `A` and `B` in the offer's `coin_spends` from being crafted so that `A.parent_coin_info == B.name()` and `B.parent_coin_info == A.name()`, the set comprehension `{c for c in all_removals if c.parent_coin_info not in {r.name() for r in all_removals}}` will exclude both `A` and `B` from `non_ephemeral_removals`. When `get_root_removal` is called with either coin, the `while coin not in non_ephemeral_removals` loop keeps calling `next(c for c in all_removals if c.name() == coin.parent_coin_info)`, bouncing between `A` and `B` forever, hanging the calling coroutine/event loop.

`get_root_removal` is called from `get_primary_coins()`, which iterates over `get_offered_coins()` for every asset, and `get_primary_coins()`/`get_root_removal` are used by `TradeManager` when reconciling coin state for trades (e.g., filtering `offer.additions()` by `offer.get_root_removal(c) in our_primary_coins`): [4](#0-3) [5](#0-4) 

Because `Offer` objects are attacker-supplied byte blobs handled by ordinary wallet users (via `create_offer_for_ids` responses, `respond_to_offer`, offer file exchange, and trade-tracking logic that re-parses stored/received offers), a wallet or trade-manager code path that calls `get_primary_coins()`/`get_root_removal()` on such a crafted offer can hang indefinitely.

### Impact Explanation
This is a CWE-835-style infinite loop (analogous to the CVE-2018-1999012 PVA-demuxer bug) reachable by feeding a specially crafted, unprivileged input (an offer file) to normal wallet processing code. It causes the wallet's coroutine (and potentially the whole async event loop, since these are synchronous CPU-bound loops called from async wallet methods) to hang, denying service to the wallet user who receives/tracks the malicious offer. This does not corrupt consensus state or steal funds, but it is a concrete resource/availability impact (CWE-835-class DoS) triggered purely by a spend-adjacent artifact (an offer bundle) that an unprivileged counterparty controls.

### Likelihood Explanation
Likelihood is moderate: constructing an `Offer`/`SpendBundle` with two `CoinSpend` entries whose `Coin.parent_coin_info` values reference each other is straightforward — no signature or on-chain validity is required to build the bytes structure, since the hang occurs purely in Python-side bookkeeping before/around signature or full spend validation. The victim simply needs code that calls `get_primary_coins()` (or the related `get_cancellation_coins()` cycle-handling logic, which has separate but adjacent loop logic) on the malicious offer, e.g. through trade tracking/reconciliation flows in `TradeManager`.

### Recommendation
- Add cycle detection (e.g., a visited-set of coin ids) inside `get_root_removal`'s `while` loop, raising a `ValueError` if a cycle is detected instead of looping forever.
- Bound the loop iterations by `len(all_removals)` since a valid lineage chain can never exceed the number of distinct removals in the bundle.
- Apply the same defensive bound/cycle check to the `while True` loops in `get_cancellation_coins()`, which similarly trust attacker-supplied `dependencies`/`announcements` maps derived from an untrusted offer bundle.

### Proof of Concept
Conceptual PoC (cannot be executed here, but illustrates the trigger):
1. Construct two `Coin` objects `A` and `B` such that `A = Coin(parent_coin_info=B.name(), puzzle_hash=ph_a, amount=amt_a)` and `B = Coin(parent_coin_info=A.name(), puzzle_hash=ph_b, amount=amt_b)`.
2. Build `CoinSpend`s for `A` and `B` (arbitrary puzzle/solution, since the loop never depends on execution results) and include neither as an addition of the other, so both appear in `self._bundle.removals()`.
3. Wrap them (with any settlement-payment addition coins needed to make `Offer.__init__`'s existing checks pass) into an `Offer` and serialize it, then send it to a victim wallet as an offer file.
4. When the victim's `TradeManager` (or any caller) invokes `offer.get_primary_coins()` on this offer, `get_root_removal(A)` (or `B`) enters the infinite `while` loop shown at `chia/wallet/trading/offer.py:411-412`, hanging the caller.

### Citations

**File:** chia/wallet/trading/offer.py (L227-228)
```python
    def removals(self) -> list[Coin]:
        return self._bundle.removals()
```

**File:** chia/wallet/trading/offer.py (L400-414)
```python
    # This returns the non-ephemeral removal that is an ancestor of the specified coin
    # This should maybe move to the SpendBundle object at some point
    def get_root_removal(self, coin: Coin) -> Coin:
        all_removals: set[Coin] = set(self.removals())
        all_removal_ids: set[bytes32] = {c.name() for c in all_removals}
        non_ephemeral_removals: set[Coin] = {
            c for c in all_removals if c.parent_coin_info not in {r.name() for r in all_removals}
        }
        if coin.name() not in all_removal_ids and coin.parent_coin_info not in all_removal_ids:
            raise ValueError("The specified coin is not a coin in this bundle")

        while coin not in non_ephemeral_removals:
            coin = next(c for c in all_removals if c.name() == coin.parent_coin_info)

        return coin
```

**File:** chia/wallet/trading/offer.py (L416-422)
```python
    # This will only return coins that are ancestors of settlement payments
    def get_primary_coins(self) -> list[Coin]:
        primary_coins: set[Coin] = set()
        for _, coins in self.get_offered_coins().items():
            for coin in coins:
                primary_coins.add(self.get_root_removal(coin))
        return list(primary_coins)
```

**File:** chia/wallet/trade_manager.py (L178-180)
```python
            our_additions: list[Coin] = list(
                filter(lambda c: offer.get_root_removal(c) in our_primary_coins, offer.additions())
            )
```
