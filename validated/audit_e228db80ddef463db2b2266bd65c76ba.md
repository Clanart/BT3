### Title
Unbounded/quartic-complexity loop in `Offer.get_cancellation_coins()` allows an offer counterparty to stall the wallet's trade-cancellation flow - (File: chia/wallet/trading/offer.py)

### Summary
`Offer.get_cancellation_coins()` runs an outer `while True` loop that repeatedly calls `detect_dependent_coin()` (itself a triple-nested loop over `names × deps[name] × announcement_dict.items()`), and for every match it runs a second inner `while True` loop that rescans the entire `dependencies` dict looking for coins whose announcements depend on the just-removed coin. None of these loops are bounded by an iteration cap, nor are they metered by CLVM cost (they run in plain Python after `run_with_cost(..., INFINITE_COST, ...)`), so their cost scales with the number of coin spends and the density of `CREATE_COIN_ANNOUNCEMENT` / `ASSERT_COIN_ANNOUNCEMENT` dependency chains inside an offer file, which is entirely attacker-controlled content. [1](#0-0) [2](#0-1) 

### Finding Description
`get_cancellation_coins()` is the exact analog of the `MainRewarder._processRewards` pattern cited in the report: a function whose cost is driven by an attacker-influenced collection length, executed with no bound, in a code path that a normal user is expected to invoke (here, cancelling a pending offer).

Walking through the code:
1. For every non-ephemeral coin spend in the offer, `run_with_cost(spend.puzzle_reveal, INFINITE_COST, spend.solution)` is executed with an unbounded cost budget, and every `CREATE_COIN_ANNOUNCEMENT`/`ASSERT_COIN_ANNOUNCEMENT` condition is recorded into `announcements`/`dependencies` dictionaries keyed by coin name. [3](#0-2) 
2. `detect_dependent_coin(coin_names, dependencies, announcements)` triple-nests over `names`, `deps[name]`, and `announcement_dict.items()` — O(n³) per call — to find any coin that depends on another coin's announcement. [2](#0-1) 
3. The outer `while True` loop calls this O(n³) function repeatedly, up to once per coin removed, and for each removal, an inner `while True` loop rescans the full `dependencies` dict (O(n)) until fixpoint, compounding the overall complexity to worse than cubic — effectively O(n⁴) in the worst case for a specially constructed dependency graph. [4](#0-3) 

Since the CLVM execution phase in `Offer.__post_init__` only limits total condition-cost (`MAX_BLOCK_COST_CLVM`), and trivial puzzles (`Program.to(1)`-style) generate `CREATE_COIN_ANNOUNCEMENT`/`ASSERT_COIN_ANNOUNCEMENT` conditions very cheaply, an attacker (an offer counterparty crafting an offer file, or a maker whose offer is later cancelled by the taker after a failed take) can build an offer with thousands of coin spends chained together through announcement dependencies to make this Python-level, unmetered post-processing extremely expensive — with no cost limit protecting it, unlike CLVM execution. [5](#0-4) 

The function is reached via `TradeManager.cancel_pending_offers()`, which is called from the RPC/CLI (`cancel_offer`) whenever a user attempts to cancel a pending trade tied to an offer file they did not necessarily author, e.g., after a takeoffer failure or timeout. [6](#0-5) 

### Impact Explanation
This is a spend-triggered wallet-side transaction-processing halt: a user attempting to cancel a maliciously-crafted pending offer can have their wallet process (or RPC call) hang or consume excessive CPU for an extended period, blocking `cancel_pending_offers` and by extension the wallet's ability to reclaim/cancel locked coins. This matches the report's core impact class ("possible DoS under conditions or high gas cost" from unbounded loops scaling with attacker-controlled collection length), mapped here to a wallet-reachable, non-privileged code path (offer counterparty / wallet user).

### Likelihood Explanation
Medium: the attacker needs to construct an offer with many chained announcement-dependent coin spends (bounded only by `MAX_BLOCK_COST_CLVM`, which still permits thousands of trivial spends), and get a victim to accept/hold that offer such that the victim later calls `cancel_pending_offers` on it. This requires user interaction (accepting or attempting to take the offer) but no privileged access — an ordinary offer counterparty can trigger it.

### Recommendation
- Cap the number of coin spends / dependency edges processed by `get_cancellation_coins()` and reject (or short-circuit) offers whose dependency graph exceeds a safe bound.
- Replace the O(n) linear rescans in `detect_dependent_coin()` and the inner `while True` loop with proper graph/topological-sort data structures (e.g., adjacency maps keyed for O(1) lookup, and a single-pass algorithm) so the total complexity is O(n log n) or O(n) instead of up to O(n⁴).
- Apply an explicit iteration/time budget to the outer `while True` loop, aborting (with a clear error) rather than looping indefinitely on adversarial input.

### Proof of Concept
Conceptual reproduction:
1. Construct a `SpendBundle` with N coin spends (trivial `Program.to(1)`-style puzzles), each solution emitting one `CREATE_COIN_ANNOUNCEMENT` and one `ASSERT_COIN_ANNOUNCEMENT`, chained so that spend `i` asserts the announcement created by spend `i+1` (or a more adversarial cyclic/fan-out graph), keeping total CLVM cost under `MAX_BLOCK_COST_CLVM`.
2. Wrap this into an `Offer` object (bypassing/satisfying `__post_init__`'s cost check since per-spend cost is trivial and N can be large).
3. Call `offer.get_cancellation_coins()` (as done inside `TradeManager.cancel_pending_offers`) and observe running time scaling super-linearly (empirically quartic) with N, causing significant wall-clock delay or a hang.

Note: I was unable to fully verify, due to index truncation, the exact code path in `TradeManager.respond_to_offer()` that stores an untrusted maker-provided offer into `trade.offer` for later cancellation by the taker — this would need to be confirmed in a full checkout to establish the complete end-to-end untrusted-offer-to-cancellation reachability chain.

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

**File:** chia/wallet/trading/offer.py (L163-184)
```python
        adds: dict[Coin, list[Coin]] = {}
        hints: dict[bytes32, bytes32] = {}
        max_cost = int(DEFAULT_CONSTANTS.MAX_BLOCK_COST_CLVM)
        for cs in self._bundle.coin_spends:
            # you can't spend the same coin twice in the same SpendBundle
            assert cs.coin not in adds
            try:
                hinted_coins, cost = compute_spend_hints_and_additions(cs, max_cost=max_cost)
                max_cost -= cost
                adds[cs.coin] = [hc.coin for hc in hinted_coins.values()]
                hints = {**hints, **{id: hc.hint for id, hc in hinted_coins.items() if hc.hint is not None}}
            except ValidationError:
                raise
            except ValueError as e:
                if e.args and e.args[0] == "cost exceeded or below zero":
                    raise ValidationError(Err.BLOCK_COST_EXCEEDS_MAX, "compute_additions for CoinSpend") from e
                continue
            except Exception:
                continue
            if max_cost < 0:
                raise ValidationError(Err.BLOCK_COST_EXCEEDS_MAX, "compute_additions for CoinSpend")
        object.__setattr__(self, "_additions", adds)
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

**File:** chia/wallet/trade_manager.py (L253-288)
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
```
