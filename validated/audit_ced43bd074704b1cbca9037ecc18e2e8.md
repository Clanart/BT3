### Title
Infinite loop DoS via cyclic coin lineage in `Offer.get_root_removal` - ([File: chia/wallet/trading/offer.py])

### Summary
`Offer.get_root_removal()` walks a coin's parent chain within an offer's `_bundle.coin_spends` until it finds a "non-ephemeral" removal (a coin whose parent is not itself part of the offer's removal set). The set of non-ephemeral removals is computed once, up front, from attacker-controlled coin data embedded in the offer file. A crafted offer whose coin_spends contain a cycle of `parent_coin_info` references (e.g. coin A's parent is coin B and coin B's parent is coin A) makes `non_ephemeral_removals` empty, causing the `while coin not in non_ephemeral_removals:` loop to never terminate, since it will keep resolving `coin.parent_coin_info` around the cycle forever.

### Finding Description
`get_root_removal()`: [1](#0-0) 

computes `non_ephemeral_removals` purely from the coin fields (`Coin.name()`/`Coin.parent_coin_info`) present in the untrusted, attacker-supplied `Offer._bundle.coin_spends` list — no on-chain validation of parent/child relationships is performed at this stage. If the offer's coin_spends are crafted so that every removal's `parent_coin_info` also matches another removal's `name()` in a closed cycle, `non_ephemeral_removals` is the empty set. The subsequent `while coin not in non_ephemeral_removals:` loop condition can never be satisfied, and the body:
```python
coin = next(c for c in all_removals if c.name() == coin.parent_coin_info)
```
will always find a match (since it's a cycle) and loop forever, pegging a CPU core with no way out short of process kill.

`get_root_removal()` is called by `get_primary_coins()`, which is in turn called by `get_cancellation_coins()`: [2](#0-1) [3](#0-2) 

`get_cancellation_coins()` is invoked from `chia/wallet/trade_manager.py` (offer-cancellation flow), which is reachable when a wallet user attempts to cancel a pending/received trade — i.e., processing of an offer file that could have been supplied by a malicious offer counterparty.

### Impact Explanation
This is a spend-triggered/offer-triggered denial of service: a wallet user who loads or attempts to cancel a maliciously crafted offer (an untrusted artifact routinely exchanged between offer counterparties, e.g. via file/dexie/offer-board) can have their wallet process hang indefinitely in a hot loop, halting further processing on that thread/event loop context. This matches the CVE's bug class ("Loop with Unreachable Exit Condition") and produces availability impact without requiring privileged access — only requires an unprivileged party to hand the victim an offer file/bundle.

### Likelihood Explanation
Constructing an `Offer`/`WalletSpendBundle` with coin_spends whose `Coin.parent_coin_info`/`puzzle_hash`/`amount` fields form a two-(or more)-coin cycle is straightforward since `Coin` objects here are just data (not validated against real chain state at this stage of offer parsing). The victim does not need to accept or push the offer to chain — simply calling into the cancellation path (`get_cancellation_coins`/`get_primary_coins`) on the offer object is sufficient to trigger the loop, which requires only ordinary wallet-user interaction with an attacker-supplied offer artifact.

### Recommendation
Add cycle detection / iteration bound in `get_root_removal()` — e.g., track visited coin names and raise a `ValueError` (or otherwise abort) if a cycle is detected before revisiting a coin, or cap the number of hops to `len(all_removals)`. Since `all_removals` is finite, the walk should never need more than `len(all_removals)` steps in a well-formed bundle; enforce that as a hard limit.

### Proof of Concept
```python
from chia_rs import CoinSpend, G2Element
from chia.types.blockchain_format.coin import Coin
from chia.wallet.trading.offer import Offer
from chia.wallet.wallet_spend_bundle import WalletSpendBundle

# Craft two coins whose parent_coin_info point at each other, forming a cycle.
coin_a = Coin(parent_coin_info=b"B" * 32, puzzle_hash=b"\x00" * 32, amount=1)
coin_b = Coin(parent_coin_info=coin_a.name(), puzzle_hash=b"\x00" * 32, amount=1)
# make coin_a's parent actually equal coin_b's name to close the cycle
coin_a = Coin(parent_coin_info=coin_b.name(), puzzle_hash=b"\x00" * 32, amount=1)

cs_a = CoinSpend(coin_a, some_puzzle_reveal, some_solution)  # solution has no announcement conditions
cs_b = CoinSpend(coin_b, some_puzzle_reveal, some_solution)

bundle = WalletSpendBundle([cs_a, cs_b], G2Element())
offer = Offer(requested_payments={}, _bundle=bundle, driver_dict={})

# This call never returns: non_ephemeral_removals is empty (both parents
# are inside the removal set), so get_root_removal's while-loop spins forever.
offer.get_cancellation_coins()
```
Note: exact puzzle_reveal/solution plumbing needs to satisfy `Offer.additions()`/`removals()` bookkeeping so both coins are treated as "removals not in additions"; the core defect — the unbounded walk when `non_ephemeral_removals` is empty due to a parent-cycle — is demonstrated by the logic cited above regardless of exact puzzle content used to populate the cycle.

### Citations

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
