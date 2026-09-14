### Title
Wallet Denial of Service via Cyclic Coin References in Untrusted Offer — Infinite Loop / Unhandled `StopIteration` in `Offer.get_root_removal()` - ([File: chia/wallet/trading/offer.py])

### Summary
`Offer.get_root_removal()` walks a `Coin`'s ancestry chain inside a spend bundle by repeatedly looking up the coin whose `name()` equals the current coin's `parent_coin_info`, until it finds a coin considered "non-ephemeral" (a coin whose parent isn't itself one of the bundle's removals). This traversal is driven entirely by attacker-supplied `Coin` fields inside an untrusted `Offer`/`SpendBundle` and has no cycle detection or iteration bound, mirroring the Node.js `dns.resolveAny()` bug class where an unbounded, unvalidated list from an untrusted source drives a fatal, uncontrolled loop. [1](#0-0) 

### Finding Description
`get_root_removal()` computes:
- `all_removals` — every removed `Coin` in the bundle.
- `non_ephemeral_removals` — removals whose `parent_coin_info` is *not* the `name()` of another removal in the same bundle.

It then loops `while coin not in non_ephemeral_removals: coin = next(c for c in all_removals if c.name() == coin.parent_coin_info)`. [2](#0-1) 

Because `Coin.parent_coin_info` is just an arbitrary `bytes32` chosen by whoever constructs the offer bytes (structural validity of an unsigned/unaccepted offer is not enforced before these summary helpers run), an attacker can craft two or more coin spends whose `parent_coin_info` values point at each other, e.g. `coin1.parent_coin_info == coin2.name()` and `coin2.parent_coin_info == coin1.name()`. In that case:
- Both coins qualify as "ephemeral" (`non_ephemeral_removals` is empty for that cycle), so the `while` condition is always true.
- The `next(...)` generator alternates between `coin1` and `coin2` forever, producing an infinite loop with no timeout or step limit.

If instead the parent-chain simply dangles (points to a `parent_coin_info` not present among `all_removals` after the first hop, while still satisfying the initial one-time guard on the input `coin`), `next()` over an empty generator raises `StopIteration`, which propagates as an uncaught exception out of a generator expression context.

`get_root_removal()` is called from:
- `Offer.get_primary_coins()` and `Offer.get_pending_amounts()`, used for offer summaries. [3](#0-2) 
- `Offer.get_primary_coins()` again via a loop over `get_offered_coins()`. [4](#0-3) 
- `TradeManager.coins_of_interest_farmed()`, invoked automatically by the wallet's coin-state sync path whenever a coin belonging to an active trade is spent/farmed — not just by explicit CLI/RPC inspection. [5](#0-4) 

The `coins_of_interest_farmed` path is the most serious: once a user has accepted or created a trade involving an attacker-supplied offer with a cyclic removal graph, every subsequent coin-state notification for that trade re-invokes `Offer.from_bytes(trade.offer)` and the vulnerable ancestry walk, hanging (or crashing) the wallet's sync/transaction-processing coroutine.

### Impact Explanation
This maps to the report's accepted impact category of a "spend-triggered transaction-processing halt." A counterparty who supplies a maliciously structured `Offer` (via `take_offer`, offer files, or any code path that loads `Offer.from_bytes(...)` and later calls `get_primary_coins`/`get_pending_amounts`, or drives `coins_of_interest_farmed`) can hang or crash the recipient wallet's processing loop with an infinite loop or unhandled `StopIteration`, denying service to that wallet's normal transaction and sync operations — reachable purely from wallet-user/offer-counterparty interaction, not any privileged or network-layer position.

### Likelihood Explanation
Likelihood is high for any wallet user who accepts, examines, or tracks an offer from an untrusted counterparty: constructing two `CoinSpend`s with mutually-referencing `parent_coin_info` values requires no special privileges, signatures, or chain state — it only requires crafting the raw `Offer`/`SpendBundle` bytes, since these summary helpers operate on the bundle's structural fields before consensus-level ancestry/signature validation occurs.

### Recommendation
Add cycle/iteration-bound protection to `Offer.get_root_removal()` (e.g., track visited coin names and raise a clear `ValueError` if a cycle is detected or a bound on hops is exceeded), and make the `next(...)` lookup fail with an explicit exception (not an unguarded `StopIteration`) when no matching parent coin exists in `all_removals`. Apply the same hardening anywhere else that walks attacker-supplied coin ancestry chains derived from unaccepted offers (e.g., `Offer.get_cancellation_coins()`'s dependency loop) before they can crash or hang the wallet.

### Proof of Concept
1. Construct two `Coin` objects `coin_a`, `coin_b` such that `coin_a.parent_coin_info == coin_b.name()` and `coin_b.parent_coin_info == coin_a.name()` (values are free-form `bytes32`, no signature/validity check applies at this stage).
2. Build corresponding `CoinSpend`s for `coin_a` and `coin_b` (any puzzle reveal/solution acceptable to `Offer.from_bytes` parsing) and place them, plus a legitimate settlement-payment spend, inside a `WalletSpendBundle` to form an `Offer`.
3. Send this offer to a victim wallet, or otherwise get it stored as a trade (`take_offer` flow).
4. Trigger `Offer.get_primary_coins()` / `get_pending_amounts()` (offer summary display) or wait for `TradeManager.coins_of_interest_farmed()` to run when a coin relevant to the trade changes state.
5. `get_root_removal()` enters an infinite loop alternating between `coin_a` and `coin_b` (or raises an uncaught `StopIteration` for a dangling-chain variant), hanging/crashing the wallet's processing task.

### Citations

**File:** chia/wallet/trading/offer.py (L371-393)
```python
    def get_pending_amounts(self) -> dict[str, int]:
        all_additions: list[Coin] = self.additions()
        all_removals: list[Coin] = self.removals()
        non_ephemeral_removals: list[Coin] = list(filter(lambda c: c not in all_additions, all_removals))

        pending_dict: dict[str, int] = {}
        # First we add up the amounts of all coins that share an ancestor with the offered coins (i.e. a primary coin)
        for asset_id, coins in self.get_offered_coins().items():
            name = "xch" if asset_id is None else asset_id.hex()
            pending_dict[name] = 0
            for coin in coins:
                root_removal: Coin = self.get_root_removal(coin)

                for addition in filter(lambda c: c.parent_coin_info == root_removal.name(), all_additions):
                    pending_dict[name] += addition.amount

        # Then we gather anything else as unknown
        sum_of_additions_so_far: int = sum(pending_dict.values())
        unknown: int = sum(c.amount for c in non_ephemeral_removals) - sum_of_additions_so_far
        if unknown > 0:
            pending_dict["unknown"] = unknown

        return pending_dict
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

**File:** chia/wallet/trade_manager.py (L163-181)
```python
            # Then let's filter the offer into coins that WE offered
            if (
                self.most_recently_deserialized_trade is not None
                and trade.trade_id == self.most_recently_deserialized_trade[0]
            ):
                offer = self.most_recently_deserialized_trade[1]
            else:
                offer = Offer.from_bytes(trade.offer)
                self.most_recently_deserialized_trade = (trade.trade_id, offer)
            primary_coin_ids = [c.name() for c in offer.removals()]
            # TODO: Add `WalletCoinStore.get_coins`.
            result = await self.wallet_state_manager.coin_store.get_coin_records(
                coin_id_filter=HashFilter.include(primary_coin_ids)
            )
            our_primary_coins: list[Coin] = [cr.coin for cr in result.records]
            our_additions: list[Coin] = list(
                filter(lambda c: offer.get_root_removal(c) in our_primary_coins, offer.additions())
            )
            our_addition_ids: list[bytes32] = [c.name() for c in our_additions]
```
