### Title
Unbounded, cost-unmetered Python loops when processing a counterparty `Offer` allow a wallet-side computational DoS - ([File: chia/wallet/trading/offer.py])

### Summary
`Offer.get_cancellation_coins()` and `Offer.to_valid_spend()` in `chia/wallet/trading/offer.py` iterate over the coin spends contained in an untrusted, externally-supplied `Offer` object using nested Python loops whose cost is **not** bounded by CLVM cost accounting. A malicious offer counterparty can craft an offer with a very large number of cheap coin spends (each individually well under `MAX_BLOCK_COST_CLVM`) to trigger quadratic (or worse) Python-level work in the victim wallet when the offer is inspected, taken, or cancelled — analogous to the reported `DepositerRewardDistributor.distribute()` issue where an attacker-controlled list length drives unbounded per-element expensive work.

### Finding Description
`Offer.get_cancellation_coins()` builds `dependencies`/`announcements` dictionaries for every non-addition coin spend in the bundle, then runs a `while True` loop that calls `detect_dependent_coin()` — itself a triple-nested loop over `names`, `deps[name]`, and `announcement_dict.items()` — repeatedly until fixpoint: [1](#0-0) [2](#0-1) 

Separately, `Offer.to_valid_spend()` contains an explicit `O(n^2)` loop: for every offered coin it iterates over *every other* offered coin to build `siblings`/`sibling_spends`/`sibling_puzzles`/`sibling_solutions` via string concatenation and `disassemble()` calls: [3](#0-2) 

None of this Python-side bookkeeping is metered by the CLVM cost budget that bounds `Offer.__post_init__`'s `compute_spend_hints_and_additions` call (`max_cost = DEFAULT_CONSTANTS.MAX_BLOCK_COST_CLVM`): [4](#0-3) 

Because CLVM execution cost per coin spend can be made arbitrarily small (e.g., a quote puzzle `Program.to(1)` that emits one cheap `CREATE_COIN_ANNOUNCEMENT`/`ASSERT_COIN_ANNOUNCEMENT` pair), an attacker can pack thousands of such spends into a single offer while staying under the `MAX_BLOCK_COST_CLVM` cost gate that the offer's constructor enforces. The victim then triggers the expensive Python loops either automatically (constructing a `TradeRecord`/inspecting the offer) or when calling `TradeManager.respond_to_offer()` (take) or `TradeManager.cancel_pending_offers()` (cancel), both of which call `Offer.get_cancellation_coins()`/`to_valid_spend()`: [5](#0-4) [6](#0-5) 

This mirrors the report's bug class exactly: a list whose length is attacker-controlled (`extraRewards` ↔ `coin_spends`/`offered_coins`) is iterated with per-element expensive work (external calls ↔ CLVM execution + string building), with no explicit cap on the list length.

### Impact Explanation
An offer counterparty (an unprivileged actor explicitly in scope) can send a specially crafted offer file to a victim wallet. When the victim's wallet examines/cancels/takes the offer, it can be forced into CPU-bound quadratic (or worse, since `detect_dependent_coin`'s fixpoint loop can re-scan the full dictionary many times) processing, freezing the wallet's transaction-processing event loop for an extended period. Because `WalletStateManager`/`TradeManager` are typically single-process/async, this stalls the victim's ability to send or receive other transactions while the computation runs — a spend-triggered transaction-processing halt local to the targeted wallet. This does not directly cause coin loss but is a legitimate availability/DoS impact against a specific wallet processing an untrusted offer.

### Likelihood Explanation
Likelihood is moderate: constructing an offer with many cheap coin spends (quote puzzles emitting minimal conditions) is straightforward and does not require any special privilege — it only requires the ability to author an offer file and get a victim to load/examine/take/cancel it, which is the ordinary offer-exchange workflow (e.g., via public offer boards or ephemeral sharing). The CLVM-side cost gate in `Offer.__post_init__` does not prevent this because it only bounds CLVM execution cost, not the number of coin spends or the resulting Python-side combinatorial work.

### Recommendation
Impose an explicit upper bound on the number of coin spends / offered coins that `Offer.get_cancellation_coins()` and `Offer.to_valid_spend()` will process (independent of CLVM cost), and/or replace the O(n²) sibling-building and repeated-fixpoint dependency scan with linear/near-linear algorithms (e.g., precomputed reverse-lookup maps from announcement hash to coin instead of re-scanning `announcement_dict.items()` for every dependency). Reject or truncate offers whose spend count exceeds the bound before performing dependency/sibling analysis.

### Proof of Concept
Not directly executable from the available static context (index does not include a runnable wallet harness), but the reachable path is:
1. Attacker builds an `Offer` (via `Offer.from_spend_bundle`) whose settlement side contains thousands of coin spends using minimal-cost puzzles (e.g., `(q . ((60 msg) (61 announcement)))`), each staying far under `MAX_BLOCK_COST_CLVM` individually, so `Offer.__post_init__`'s cost gate at `chia/wallet/trading/offer.py:163-186` passes.
2. Victim wallet calls `TradeManager.cancel_pending_offers()` or `TradeManager.respond_to_offer()` on the received offer, which invoke `Offer.get_cancellation_coins()` (`chia/wallet/trading/offer.py:425-470`) and/or `Offer.to_valid_spend()` (`chia/wallet/trading/offer.py:506-595`).
3. The nested/fixpoint loops in these functions scale super-linearly with the number of spends, consuming excessive CPU time on the victim's wallet process, delaying or halting normal transaction handling for that wallet.

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

**File:** chia/wallet/trading/offer.py (L163-186)
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
        object.__setattr__(self, "_hints", hints)
        object.__setattr__(self, "_conditions", None)
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

**File:** chia/wallet/trading/offer.py (L541-563)
```python
            for coin in offered_coins:
                if asset_id:
                    siblings: str = "("
                    sibling_spends: str = "("
                    sibling_puzzles: str = "("
                    sibling_solutions: str = "("
                    disassembled_offer_mod: str = disassemble(OFFER_MOD)
                    for sibling_coin in offered_coins:
                        if sibling_coin != coin:
                            siblings += (
                                "0x"
                                + sibling_coin.parent_coin_info.hex()
                                + sibling_coin.puzzle_hash.hex()
                                + uint64(sibling_coin.amount).stream_to_bytes().hex()
                                + " "
                            )
                            sibling_spends += "0x" + bytes(coin_to_spend_dict[sibling_coin]).hex() + " "
                            sibling_puzzles += disassembled_offer_mod + " "
                            sibling_solutions += disassemble(coin_to_solution_dict[sibling_coin]) + " "
                    siblings += ")"
                    sibling_spends += ")"
                    sibling_puzzles += ")"
                    sibling_solutions += ")"
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

**File:** chia/wallet/trade_manager.py (L822-855)
```python
    async def respond_to_offer(
        self,
        offer: Offer,
        peer: WSChiaConnection,
        action_scope: WalletActionScope,
        solver: Solver | None = None,
        fee: uint64 = uint64(0),
        extra_conditions: tuple[Condition, ...] = tuple(),
    ) -> TradeRecord:
        if solver is None:
            solver = Solver({})
        take_offer_dict: dict[bytes32 | int, int] = {}
        arbitrage: dict[bytes32 | None, int] = offer.arbitrage()

        for asset_id, amount in arbitrage.items():
            if asset_id is None:
                wallet: WalletProtocol | None = self.wallet_state_manager.main_wallet
                assert wallet is not None
                key: bytes32 | int = int(wallet.id())
            else:
                # ATTENTION: new wallets
                wallet = await self.wallet_state_manager.get_wallet_for_asset_id(asset_id)
                if wallet is None and amount < 0:
                    raise ValueError(f"Do not have a wallet for asset ID: {asset_id} to fulfill offer")
                elif wallet is None or wallet.type() in {WalletType.NFT, WalletType.DATA_LAYER}:
                    key = asset_id
                else:
                    key = int(wallet.id())
            take_offer_dict[key] = amount

        # First we validate that all of the coins in this offer exist
        valid: bool = await self.check_offer_validity(offer, peer)
        if not valid:
            raise ValueError("This offer is no longer valid")
```
