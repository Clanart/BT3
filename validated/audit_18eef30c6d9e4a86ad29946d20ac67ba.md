[1](#0-0) [2](#0-1) 

### Title
Algorithmic-complexity DoS in `Offer.get_cancellation_coins()` via crafted offer announcement graph - (File: chia/wallet/trading/offer.py)

### Summary
`Offer.get_cancellation_coins()` and its helper `detect_dependent_coin()` compute a dependency graph of coin announcements from an offer's `CoinSpend`s using nested loops with no bound on the number of coins/announcements, and re-run this scan repeatedly inside a `while True` loop until no more dependent coins are found. A counterparty can craft an offer whose CLVM puzzle/solution cheaply emits a very large number of `CREATE_COIN_ANNOUNCEMENT`/`ASSERT_COIN_ANNOUNCEMENT` conditions (conditions are parsed locally with `run_with_cost(..., INFINITE_COST, ...)`, i.e. no CLVM cost limit is applied at this stage), causing this function to take super-linear time and hang the victim wallet's process when the victim (maker or taker) attempts to securely cancel the resulting pending trade.

### Finding Description
`get_cancellation_coins()` builds two dictionaries, `dependencies` and `announcements`, one entry per non-addition coin in the offer bundle, and populates each entry's list by iterating every CLVM condition produced by running `spend.puzzle_reveal` against `spend.solution` with `INFINITE_COST`: [3](#0-2) 

Because the run is done with `INFINITE_COST` and is purely local (not going through the mempool's CLVM cost accounting), an attacker who controls the offer's puzzle/solution content can make a single `CoinSpend` emit an arbitrarily large number of announcement conditions cheaply (the codebase's own `_tests/core/mempool/test_mempool.py` malicious-generator patterns, e.g. `CREATE_ANNOUNCE_COND`, demonstrate how a small CLVM program can generate thousands of conditions in a tight loop).

The subsequent resolution loop calls `detect_dependent_coin()`: [1](#0-0) 
which is a triple-nested loop over `names × deps[name] × announcement_dict.items()`, with an inner `in announces` membership test against a Python list (itself O(len(announces))). This function is invoked repeatedly from the `while True:` loop in `get_cancellation_coins()`: [4](#0-3) 
so a single crafted offer, blown up with many coins each carrying many announcement/assertion conditions, causes work that scales combinatorially in the number of coins and conditions, well beyond what CLVM cost accounting would ever allow inside a real transaction.

`get_cancellation_coins()` is only reached from `TradeManager.cancel_pending_offers()`: [5](#0-4) 
which is triggered by the local wallet RPC `CancelOffer`/`CancelOfferCMD` with `secure=True` (the default). A wallet that has taken (or made) a trade containing a malicious offer will have that offer's `WalletSpendBundle` persisted as `trade.offer` (see `respond_to_offer` / `save_trade`), and calling "cancel" on that pending trade — an entirely normal user action — runs the vulnerable code against attacker-supplied content.

### Impact Explanation
This is a spend-triggered transaction-processing halt: a wallet user (maker or taker of an offer) who attempts to cancel a pending trade built from a maliciously crafted offer can have their wallet process hang or consume excessive CPU/memory, denying service to that wallet (unable to process further RPC calls, sync, or respond to other trades) until the operation is killed. It does not directly cause coin theft or consensus divergence, but it is a legitimate, remotely-triggerable resource-exhaustion vector matching CWE-770, reachable purely through the offer/trade flow available to any offer counterparty.

### Likelihood Explanation
Likelihood is moderate-to-high for a targeted attack: the attacker only needs to send a normal-looking offer file (via the standard out-of-band offer exchange) with a puzzle/solution that emits a large number of coin announcement conditions on one or more coins. No special access or race condition is required — the victim only needs to accept/take the offer (or receive it as maker after aggregation) and later call the standard "cancel trade" action, which is a routine wallet operation, especially for stale/unconfirmed offers.

### Recommendation
- Cap the number of coins and announcements considered in `get_cancellation_coins()`/`detect_dependent_coin()` (e.g., reject or fall back to a naive "cancel everything" strategy above a coin/announcement count threshold).
- Replace the O(N) list membership checks (`dependency in announces`, `deps & set(...)`) with set-based lookups, and avoid re-scanning the full `announcement_dict` on every iteration of the outer `while True` loop (e.g., maintain a reverse index from announcement hash to coin).
- Apply a real CLVM cost limit (not `INFINITE_COST`) when locally evaluating solutions from an untrusted/counterparty-supplied offer before extracting conditions for cancellation-graph analysis.

### Proof of Concept
1. Attacker crafts an `Offer` whose CLVM puzzle for one or more offered coins runs a tight loop (à la `CREATE_ANNOUNCE_COND` from `chia/_tests/core/mempool/test_mempool.py`) that emits, e.g., tens of thousands of `CREATE_COIN_ANNOUNCEMENT`/`ASSERT_COIN_ANNOUNCEMENT` conditions referencing many distinct coin IDs across many coins in the bundle, forming a dense dependency graph.
2. Attacker sends this offer file to a victim; victim runs `chia wallet take_offer` (or aggregates it as maker), which stores the resulting `WalletSpendBundle` as `trade.offer`.
3. Victim later runs `chia wallet cancel_offer <trade_id>` (secure cancel, the default), invoking `TradeManager.cancel_pending_offers()` → `Offer.get_cancellation_coins()` → repeated `detect_dependent_coin()` calls on the attacker-crafted graph, causing the wallet process to hang or spike CPU for an extended period, denying the wallet service.

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

**File:** chia/wallet/trade_manager.py (L253-290)
```python
    async def cancel_pending_offers(
        self,
        trade_ids: list[bytes32],
        action_scope: WalletActionScope,
        fee: uint64 = uint64(0),
        secure: bool = True,  # Cancel with a transaction on chain
        trade_cache: dict[bytes32, TradeRecord] = {},  # Optional pre-fetched trade records for optimization
        extra_conditions: tuple[Condition, ...] = tuple(),
    ) -> None:
        """This will create a transaction that includes coins that were offered"""

        # Need to do some pre-figuring of announcements that will be need to be made
        announcement_nonce: bytes32 = std_hash(b"".join(trade_ids))
        trade_records: list[TradeRecord] = []
        all_cancellation_coins: list[list[Coin]] = []
        announcement_creations: deque[CreateCoinAnnouncement] = deque()
        announcement_assertions: deque[AssertCoinAnnouncement] = deque()
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

        # Make every coin assert the announcement from the one before them
```
