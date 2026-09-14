## Title
Polynomial-complexity `Offer.get_cancellation_coins()` enables an offer-counterparty DoS of the wallet's cancel-offer path - (File: `chia/wallet/trading/offer.py`)

## Summary
`Offer.get_cancellation_coins()` and its helper `detect_dependent_coin()` in [1](#0-0)  use nested, nearly-quadratic-per-call scans that are themselves re-invoked in an outer removal loop, giving the overall routine super-linear (worst case near-quartic) complexity in the number of coin spends in an offer. Because this code runs on an untrusted, counterparty-supplied `Offer` (any offer a wallet has accepted/stored can later be cancelled), a malicious counterparty can craft an offer with many coin spends that create long chains of `CREATE_COIN_ANNOUNCEMENT`/`ASSERT_COIN_ANNOUNCEMENT` dependencies, causing the victim's wallet to hang for an extended period the moment they call `cancel_offer`/`cancel_offers`.

## Finding Description
`get_cancellation_coins()` builds per-coin `dependencies` and `announcements` maps by replaying every coin spend's conditions, then repeatedly calls `detect_dependent_coin()`: [1](#0-0) 

```python
def detect_dependent_coin(
    names: list[bytes32], deps: dict[bytes32, list[bytes32]], announcement_dict: dict[bytes32, list[bytes32]]
) -> tuple[bytes32, bytes32] | None:
    # First, we check for any dependencies on coins in the same bundle
    for name in names:
        for dependency in deps[name]:
            for coin, announces in announcement_dict.items():
                if dependency in announces and coin != name:
                    ...
```

This single call is already `O(N * D * A)` where `N` is coin count, `D` is dependencies per coin, and `A` is announcements per coin — effectively `O(N^3)` for a fully-connected dependency graph.

`get_cancellation_coins()` then wraps this in an outer loop that calls `detect_dependent_coin` again on every iteration, and only removes (at minimum) one coin per outer iteration, plus an inner loop that itself scans all `dependencies.items()`: [2](#0-1) 

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
        ...
```

Since the outer `while True` runs up to `N` times (bounded by coins removed), and each iteration re-invokes the already `O(N^3)`-ish `detect_dependent_coin`, the worst-case cost of `get_cancellation_coins()` scales far faster than quadratically with the number of coin spends in the offer — the same class of "scan loop re-runs full unanchored search over remaining data on every iteration" bug described in the external report for `LinkifyIt.match`.

This function is reached from the wallet's cancel-offer flow: [3](#0-2) 

```python
for trade_id in trade_ids:
    ...
    cancellation_coins = Offer.from_bytes(trade.offer).get_cancellation_coins()
    for coin in cancellation_coins:
        creation = CreateCoinAnnouncement(msg=announcement_nonce, coin_id=coin.name())
```

`trade.offer` is the raw bytes of an `Offer` that was created by *either* party to a trade — including an offer *taken* from an untrusted counterparty and stored via the normal take-offer flow. A user can accept a large, adversarially constructed offer (which does not need to be economically sensible, just structurally valid with many coin spends and manufactured announce/assert condition chains), and later calling `cancel_offer`/`cancel_offers` RPC on it will invoke this exact code path.

## Impact Explanation
An offer counterparty who gets a victim to accept (or a user who stores) an offer with many coin spends and long announcement-dependency chains can make `get_cancellation_coins()` run for a very long time, blocking the calling task under the wallet's `wallet_state_manager.lock` (`cancel_pending_offers` is called from inside that lock in `WalletRpcApi.cancel_offer`/`cancel_offers`). Since this lock guards most wallet state transitions, this stalls the wallet RPC/daemon for local RPC callers, a localized denial-of-service against a single wallet process triggered by a counterparty-supplied offer.

## Likelihood Explanation
Constructing an offer with many coin spends carrying `CREATE_COIN_ANNOUNCEMENT`/`ASSERT_COIN_ANNOUNCEMENT` conditions arranged into deep dependency chains is entirely within an unprivileged party's control when building an offer — no signature validity beyond driver requirements or special privileges are needed to shape these conditions, and the offer only needs to be accepted/stored (a normal wallet operation) before cancellation is attempted. This makes the trigger straightforward for anyone who can convince (or wait for) a wallet operator to accept an offer, or for a user cancelling their own maliciously self-crafted offer to accidentally hit worst-case behavior at scale.

## Recommendation
Rework `get_cancellation_coins()`/`detect_dependent_coin()` to use indexed lookups (e.g., a reverse map from announcement hash to the coin that created it) instead of repeatedly scanning `announcement_dict.items()` and `dependencies.items()` inside nested loops, and avoid re-scanning already-processed coins across outer-loop iterations — track a work queue/visited set so each coin and each dependency edge is examined a bounded number of times, giving overall linear or near-linear complexity in the number of coin spends, analogous to converting the fuzzy scan loop in `linkify-it` to a single stateful pass.

## Proof of Concept
1. Construct an `Offer`/`WalletSpendBundle` with `K` coin spends where coin `i` creates a coin announcement consumed by coin `i+1`'s `ASSERT_COIN_ANNOUNCEMENT`, forming one long dependency chain (or several chains merging into hub coins to maximize the `set(deps) & set(removed_announcements)` fan-out in the inner loop).
2. Store this as a taken offer via the normal wallet trade flow so `trade.offer` bytes are populated.
3. Call the `cancel_offer`/`cancel_offers` RPC (or `TradeManager.cancel_pending_offers`) on this trade.
4. Observe that `Offer.get_cancellation_coins()` (`chia/wallet/trading/offer.py:425`) takes disproportionately long relative to `K`, growing much faster than linearly as `K` increases (e.g., doubling `K` more than doubling — approaching cubic/quartic — wall-clock time), while holding `wallet_state_manager.lock`.

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

**File:** chia/wallet/trade_manager.py (L270-288)
```python
        for trade_id in trade_ids:
            if trade_id in trade_cache:
                trade = trade_cache[trade_id]
            else:
                potential_trade = await self.trade_store.get_trade_record(trade_id)
                if potential_trade is None:
                    self.log.error(f"Cannot find offer {trade_id.hex()}, skip cancellation.")
                    continue
                else:
                    trade = potential_trade

            cancellation_coins = Offer.from_bytes(trade.offer).get_cancellation_coins()
            for coin in cancellation_coins:
                creation = CreateCoinAnnouncement(msg=announcement_nonce, coin_id=coin.name())
                announcement_creations.append(creation)
                announcement_assertions.append(creation.corresponding_assertion())

            trade_records.append(trade)
            all_cancellation_coins.append(cancellation_coins)
```
