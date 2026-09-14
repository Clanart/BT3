## Analog Vulnerability Found

### Title
Unbounded per-spend CPU/memory cost when parsing an untrusted `Offer` (`chia/wallet/trading/offer.py`)

### Summary
The Werkzeug advisory describes CPU/memory exhaustion caused by parsing an attacker-controlled input (`multipart/form-data`) with an unbounded number of parts, where each part is cheap in bytes but requires non-trivial CPU/memory to parse, before any request-body-size or cost limit is enforced. The analogous Chia surface is `Offer` deserialization/inspection (`chia/wallet/trading/offer.py`), which any wallet user reaches simply by receiving an offer file from an untrusted counterparty and calling `get_offer_summary`, `check_offer_validity`, or `take_offer` — all of which parse the offer *before* it is ever pushed to mempool, so the standard `MAX_BLOCK_COST_CLVM` mempool admission gate does not apply at this stage.

### Finding Description
`Offer.from_bytes()` / `Offer.from_bech32()` deserialize an attacker-supplied `WalletSpendBundle` with no limit on the number of `CoinSpend`s contained in it [1](#0-0) . Immediately in `__post_init__`, every coin spend in the bundle is iterated and run through `compute_spend_hints_and_additions()` to build the additions/hints cache [2](#0-1) . While a total CLVM-cost budget (`DEFAULT_CONSTANTS.MAX_BLOCK_COST_CLVM`) is tracked and decremented per spend, that only bounds the *CLVM execution cost*, not the *Python-level per-spend work* (dict construction, `run_with_cost` call overhead, condition iteration, exception handling) that runs for every entry regardless of how cheap each individual spend's CLVM cost is.

Beyond construction, `Offer.get_offered_coins()` (`_get_offered_coins()`) again iterates every coin spend, performing puzzle-driver matching (`match_puzzle`), uncurrying (`get_inner_puzzle`/`get_inner_solution`), and re-running the inner puzzle with `cost_left = INFINITE_COST` — an unmetered budget distinct from the bounded budget used in `__post_init__` [3](#0-2) . This method is invoked from `summary()`, `arbitrage()`, `is_valid()`, `to_valid_spend()`, and `get_pending_amounts()` [4](#0-3) .

All of this is reachable without any admission-control cost check via:
- `GetOfferSummaryCMD` / RPC `get_offer_summary`, which calls `request.parsed_offer.summary()` directly on a client-supplied offer [5](#0-4) .
- CLI `take_offer` / RPC `check_offer_validity`, which parses and summarizes an offer file/hex blob before the user even confirms taking it [6](#0-5) .

Because a `WalletSpendBundle`/offer can contain an attacker-chosen number of small, cheap-CLVM-cost coin spends (each individually well under any single-spend cost cap), an attacker can craft an offer with a very large spend count that stays under `MAX_BLOCK_COST_CLVM` in aggregate CLVM cost yet forces the receiving wallet to perform a correspondingly large amount of Python-level parsing, puzzle-driver matching, and repeated CLVM re-execution (in `_get_offered_coins`) merely to display a summary or check validity — work triggered by an untrusted, unauthenticated party (the offer's other side) and performed before any mempool cost-based admission gate is reached.

### Impact Explanation
A malicious offer counterparty can cause high CPU/memory usage on a victim wallet's RPC/CLI process simply by having the victim examine (`get_offer_summary`, `check_offer_validity`) or attempt to take (`take_offer`) a crafted offer file — actions users routinely perform on offers received out-of-band (e.g., via chat, marketplace, or file exchange) before ever deciding whether to accept them. This can degrade or stall the wallet's RPC service (denial of service to the wallet's own daemon/RPC), matching CWE-400/CWE-770.

### Likelihood Explanation
Constructing an offer with a large number of small, low-cost coin spends does not require any special privilege, key material, or network position — it only requires standard offer-construction primitives available to any wallet user, and it can be shared as a plain string/file. The receiving user only needs to open/examine the offer through normal UX flows (`take_offer -e`, `get_offer_summary`), which is a routine, expected action for any offer recipient.

### Recommendation
Introduce an explicit cap on the number of `CoinSpend`s permitted in an `Offer`'s underlying `SpendBundle` at parse time (`Offer.from_bytes`/`Offer.__post_init__`), independent of aggregate CLVM cost, and enforce a single shared, exhaustible cost/work budget across `_get_offered_coins()` (replacing the unmetered `cost_left = INFINITE_COST` with a budget derived from the same `MAX_BLOCK_COST_CLVM`-based accounting used in `__post_init__`), so that repeated per-spend Python/CLVM work in offer-inspection paths is bounded regardless of individual spend cost.

### Proof of Concept
1. Construct a `WalletSpendBundle` containing a very large number (e.g., tens of thousands) of `CoinSpend`s, each using a trivial puzzle/solution (e.g., `Program.to(1)` with a cheap `CREATE_COIN`) so each spend's CLVM cost is minimal and the aggregate stays under `MAX_BLOCK_COST_CLVM`.
2. Wrap it as an `Offer` (`requested_payments={}`, empty `driver_dict`) and serialize via `Offer.to_bech32()`.
3. Send the resulting offer string to a victim wallet user.
4. Have the victim run `chia wallet take_offer -e <offer>` or call the `get_offer_summary`/`check_offer_validity` RPC on it; observe the elevated CPU time and memory usage spent in `Offer.__post_init__` and `Offer._get_offered_coins()` proportional to the (attacker-controlled, effectively unbounded) spend count rather than to the bounded aggregate CLVM cost.

### Citations

**File:** chia/wallet/trading/offer.py (L162-184)
```python
        # populate the _additions cache
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

**File:** chia/wallet/trading/offer.py (L242-303)
```python
    # This method does not get every coin that is being offered, only the `settlement_payment` children
    # It's also a little heuristic, but it should get most things
    def _get_offered_coins(self) -> dict[bytes32 | None, list[Coin]]:
        offered_coins: dict[bytes32 | None, list[Coin]] = {}

        cost_left = INFINITE_COST
        for parent_spend in self._bundle.coin_spends:
            coins_for_this_spend: list[Coin] = []

            parent_puzzle: UnknownPuzzle = UnknownPuzzle(known_program=parent_spend.puzzle_reveal)
            parent_solution = Program.from_serialized(parent_spend.solution)
            additions: list[Coin] = self._additions[parent_spend.coin]

            puzzle_driver = match_puzzle(parent_puzzle)
            if puzzle_driver is not None:
                asset_id = create_asset_id(puzzle_driver)
                inner_puzzle: Program | None = get_inner_puzzle(puzzle_driver, parent_puzzle, parent_solution)
                inner_solution: Program | None = get_inner_solution(puzzle_driver, parent_solution)
                assert inner_puzzle is not None and inner_solution is not None

                # We're going to look at the conditions created by the inner puzzle
                puzzle_cost, conditions = inner_puzzle.run_with_cost(cost_left, inner_solution)
                assert cost_left >= puzzle_cost
                cost_left -= puzzle_cost
                expected_num_matches: int = 0
                offered_amounts: list[int] = []
                for condition in conditions.as_iter():
                    if condition.first() == 51 and condition.rest().first() == OFFER_MOD_HASH:
                        expected_num_matches += 1
                        offered_amounts.append(condition.rest().rest().first().as_int())

                # Start by filtering additions that match the amount
                matching_spend_additions = [a for a in additions if a.amount in offered_amounts]

                if len(matching_spend_additions) == expected_num_matches:
                    coins_for_this_spend.extend(matching_spend_additions)
                # We didn't quite get there so now lets narrow it down by puzzle hash
                else:
                    # If we narrowed down too much, we can't trust the amounts so start over with all additions
                    if len(matching_spend_additions) < expected_num_matches:
                        matching_spend_additions = additions
                    matching_spend_additions = [
                        a
                        for a in matching_spend_additions
                        if a.puzzle_hash == construct_puzzle(puzzle_driver, OFFER_MOD).get_tree_hash()
                    ]
                    if len(matching_spend_additions) == expected_num_matches:
                        coins_for_this_spend.extend(matching_spend_additions)
                    else:
                        raise ValueError("Could not properly guess offered coins from parent spend")
            else:
                # It's much easier if the asset is bare XCH
                asset_id = None
                coins_for_this_spend.extend([a for a in additions if a.puzzle_hash == OFFER_MOD_HASH])

            # We only care about unspent coins
            coins_for_this_spend = [c for c in coins_for_this_spend if c not in self._bundle.removals()]

            if coins_for_this_spend != []:
                offered_coins.setdefault(asset_id, [])
                offered_coins[asset_id].extend(coins_for_this_spend)
        return offered_coins
```

**File:** chia/wallet/trading/offer.py (L313-343)
```python
    def get_offered_amounts(self) -> dict[bytes32 | None, int]:
        offered_coins: dict[bytes32 | None, list[Coin]] = self.get_offered_coins()
        offered_amounts: dict[bytes32 | None, int] = {}
        for asset_id, coins in offered_coins.items():
            offered_amounts[asset_id] = uint64(sum(c.amount for c in coins))
        return offered_amounts

    def get_requested_payments(self) -> dict[bytes32 | None, list[NotarizedPayment]]:
        return self.requested_payments

    def get_requested_amounts(self) -> dict[bytes32 | None, int]:
        requested_amounts: dict[bytes32 | None, int] = {}
        for asset_id, coins in self.get_requested_payments().items():
            requested_amounts[asset_id] = uint64(sum(c.amount for c in coins))
        return requested_amounts

    def arbitrage(self) -> dict[bytes32 | None, int]:
        """
        Returns a dictionary of the type of each asset and amount that is involved in the trade
        With the amount being how much their offered amount within the offer
        exceeds/falls short of their requested amount.
        """
        offered_amounts: dict[bytes32 | None, int] = self.get_offered_amounts()
        requested_amounts: dict[bytes32 | None, int] = self.get_requested_amounts()

        arbitrage_dict: dict[bytes32 | None, int] = {}
        for asset_id in [*requested_amounts.keys(), *offered_amounts.keys()]:
            arbitrage_dict[asset_id] = offered_amounts.get(asset_id, 0) - requested_amounts.get(asset_id, 0)

        return arbitrage_dict

```

**File:** chia/wallet/trading/offer.py (L720-724)
```python
    @classmethod
    def from_bytes(cls, as_bytes: bytes) -> Offer:
        # Because of the __bytes__ method, we need to parse the dummy CoinSpends as `requested_payments`
        bundle = WalletSpendBundle.from_bytes(as_bytes)
        return cls.from_spend_bundle(bundle)
```

**File:** chia/wallet/wallet_rpc_api.py (L1985-2009)
```python
    async def get_offer_summary(self, request: GetOfferSummary) -> GetOfferSummaryResponse:
        dl_summary = None
        if not request.advanced:
            dl_summary = await self.service.wallet_state_manager.trade_manager.get_dl_offer_summary(
                request.parsed_offer
            )
        if dl_summary is not None:
            response = GetOfferSummaryResponse(
                data_layer_summary=dl_summary,
                id=request.parsed_offer.name(),
            )
        else:
            offered, requested, infos, valid_times = request.parsed_offer.summary()
            response = GetOfferSummaryResponse(
                summary=OfferSummary(
                    offered=offered,
                    requested=requested,
                    fees=uint64(request.parsed_offer.fees()),
                    infos=infos,
                    additions=[c.name() for c in request.parsed_offer.additions()],
                    removals=[c.name() for c in request.parsed_offer.removals()],
                    valid_times=valid_times.only_absolutes(),
                ),
                id=request.parsed_offer.name(),
            )
```

**File:** chia/cmds/wallet_funcs.py (L807-840)
```python
async def take_offer(
    wallet_info: WalletClientInfo,
    fee: uint64,
    file: str,
    examine_only: bool,
    push: bool,
    condition_valid_times: ConditionValidTimes,
    tx_config: TXConfig,
) -> list[TransactionRecord]:
    wallet_client = wallet_info.client
    fingerprint = wallet_info.fingerprint
    config = wallet_info.config
    if os.path.exists(file):
        filepath = pathlib.Path(file)
        with open(filepath) as ffile:
            offer_hex: str = ffile.read()
            ffile.close()
    else:
        offer_hex = file

    try:
        offer = Offer.from_bech32(offer_hex)
    except ValueError:
        print("Please enter a valid offer file or hex blob")
        return []

    offered, requested, _, _ = offer.summary()
    cat_name_resolver = wallet_client.cat_asset_id_to_name
    network_xch = AddressType.XCH.hrp(config).upper()
    print("Summary:")
    print("  OFFERED:")
    await print_offer_summary(cat_name_resolver, offered, network_xch=network_xch)
    print("  REQUESTED:")
    await print_offer_summary(cat_name_resolver, requested, network_xch=network_xch)
```
