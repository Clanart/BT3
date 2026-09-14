## Finding

Chia's wallet-side puzzle-driver dispatch (`match_puzzle`, `get_inner_puzzle`, `get_inner_solution`, `construct_puzzle`, `solve_puzzle` in `chia/wallet/outer_puzzles.py`) recurses through nested "outer puzzle" layers (CAT, singleton, metadata, ownership, CR) with **no depth bound**, driven entirely by the structure of an attacker-supplied puzzle reveal. This is the same bug class as CVE-2025-38614: a graph/tree traversal whose depth is only implicitly bounded by an unrelated resource (CLVM cost, in eventpoll's case epoll's own loop-detection), but which in practice allows semi-unbounded native (here, Python interpreter) recursion.

### Root cause [1](#0-0) 

Each driver's `match`, `get_inner_puzzle`, `get_inner_solution`, `construct`, and `solve` recurse into `self._match`/`self._get_inner_puzzle`/etc. for the "also" (inner) layer, with no depth limit: [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) 

Crucially, this matching happens purely on curried-puzzle-mod-hash structure (via `Program.uncurry()`), **before any CLVM execution/cost metering occurs** — there is no cost budget gating how many layers can be stacked, unlike the CLVM interpreter itself. A test already demonstrates a CAT layer nested inside a CAT layer is a valid, recognized structure: [6](#0-5) 

### Reachability

An offer counterparty / wallet user reaches this by simply loading or examining an untrusted offer file or spend bundle. `Offer.from_bytes`/`from_bech32` parse the bundle and call `match_puzzle` on every coin spend's puzzle reveal to build `driver_dict`, and `_get_offered_coins` calls `get_inner_puzzle`/`get_inner_solution` recursively as well: [7](#0-6) [8](#0-7) 

This is exercised from the CLI `take_offer` path and `TradeManager.respond_to_offer`: [9](#0-8) [10](#0-9) 

An attacker can construct a coin spend whose puzzle reveal is deeply nested (thousands of layers) of recognized outer-puzzle mods (e.g., CAT-in-CAT, or CAT→ownership→metadata→singleton chains looped many times), each layer only costing a curry-argument pair on-chain, no CLVM execution needed for the *matching* step. Presenting this as an offer (via file, bech32 string, or over the wallet protocol) drives unbounded Python recursion in the victim's wallet when it merely inspects/examines/takes the offer.

### Impact

Python's default recursion limit (~1000) means sufficiently deep nesting triggers an uncaught `RecursionError`, crashing/halting the offer-processing code path in the wallet (denial of service against a wallet user's ability to process transactions/trades) — matching the "spend-triggered transaction-processing halt" impact class. This does not lead to unauthorized coin movement or consensus divergence, only availability impact on the wallet examining/accepting the malicious offer.

---

### Title
Unbounded recursion in wallet outer-puzzle driver matching enables offer-triggered DoS - (File: `chia/wallet/outer_puzzles.py`)

### Summary
`match_puzzle`/`get_inner_puzzle`/`get_inner_solution`/`construct_puzzle`/`solve_puzzle` recurse through nested CAT/singleton/metadata/ownership/CR outer-puzzle layers with no depth limit, driven solely by attacker-controlled puzzle-reveal structure and evaluated before any CLVM cost metering.

### Finding Description
`chia/wallet/outer_puzzles.py`'s driver dispatch functions call into per-asset-type driver classes (`CATOuterPuzzle`, `SingletonOuterPuzzle`, `MetadataOuterPuzzle`, `OwnershipOuterPuzzle`, `CROuterPuzzle`), each of which recognizes a nested `also()` inner layer and recurses (`self._match`, `self._get_inner_puzzle`, `self._get_inner_solution`, `self._construct`, `self._solve`) with no maximum depth check. Since matching is based purely on curried mod-hash structure via `Program.uncurry()` (a cheap, per-layer operation with no cumulative cost budget), an attacker can nest arbitrarily many recognized layers in a single puzzle reveal. `Offer.from_bytes`/`from_bech32`/`from_spend_bundle` call `match_puzzle` (and `_get_offered_coins` calls `get_inner_puzzle`/`get_inner_solution`) on every coin spend when a wallet parses an offer, before signature/CLVM validation gates it.

### Impact Explanation
Deep enough nesting exceeds the Python recursion limit, raising an uncaught `RecursionError` in the offer-parsing/examination code path. This halts the victim wallet's ability to inspect, take, or otherwise process the malicious offer/spend bundle — a spend/offer-triggered availability failure on the wallet side reachable by any untrusted offer counterparty.

### Likelihood Explanation
High likelihood of reachability: creating a nested puzzle reveal only requires currying known mods inside each other (no valid CLVM execution or signature is needed for the *matching* code path to recurse), and offers/spend bundles from unknown counterparties are routinely examined by wallet users via `take_offer`/`respond_to_offer` before any trust decision is made.

### Recommendation
Add an explicit maximum layer-depth check (e.g., similar to `EP_MAX_NESTS` bound in the referenced kernel fix) in `match_puzzle`/`get_inner_puzzle`/`get_inner_solution`/`construct_puzzle`/`solve_puzzle` (or convert the recursive traversal to an iterative loop with a hard depth cap) before descending into nested outer-puzzle layers, and reject/­fail gracefully with a catchable error rather than allowing native recursion to grow unbounded with attacker-controlled input.

### Proof of Concept
Construct a coin spend puzzle reveal as `N` nested layers of `CAT_MOD` curried onto each other (as shown feasible in `test_cat_outer_puzzle.py`'s `double_cat_puzzle` pattern, but repeated thousands of times instead of once), place it as a leftover coin spend inside an `Offer`/`SpendBundle`, and have a victim wallet call `Offer.from_bech32(...)` or `TradeManager.respond_to_offer(...)` on it — `match_puzzle`'s recursive `_match` calls exceed Python's recursion limit and raise an unhandled `RecursionError`.

### Citations

**File:** chia/wallet/outer_puzzles.py (L49-76)
```python
def match_puzzle(puzzle: UnknownPuzzle) -> PuzzleInfo | None:
    for driver in driver_lookup.values():
        potential_info: PuzzleInfo | None = driver.match(puzzle)
        if potential_info is not None:
            return potential_info
    return None


def construct_puzzle(constructor: PuzzleInfo, inner_puzzle: Program) -> Program:
    return driver_lookup[AssetType(constructor.type())].construct(constructor, inner_puzzle)


def solve_puzzle(constructor: PuzzleInfo, solver: Solver, inner_puzzle: Program, inner_solution: Program) -> Program:
    return driver_lookup[AssetType(constructor.type())].solve(constructor, solver, inner_puzzle, inner_solution)


def get_inner_puzzle(
    constructor: PuzzleInfo, puzzle_reveal: UnknownPuzzle, solution: Program | None = None
) -> Program | None:
    return driver_lookup[AssetType(constructor.type())].get_inner_puzzle(constructor, puzzle_reveal, solution)


def get_inner_solution(constructor: PuzzleInfo, solution: Program) -> Program | None:
    return driver_lookup[AssetType(constructor.type())].get_inner_solution(constructor, solution)


def create_asset_id(constructor: PuzzleInfo) -> bytes32 | None:
    return driver_lookup[AssetType(constructor.type())].asset_id(constructor)
```

**File:** chia/wallet/cat_wallet/cat_outer_puzzle.py (L33-63)
```python
    def match(self, puzzle: UnknownPuzzle) -> PuzzleInfo | None:
        args = match_cat_puzzle(puzzle)
        if args is None:
            return None
        _, tail_hash, inner_puzzle = args
        constructor_dict: dict[str, Any] = {
            "type": "CAT",
            "tail": "0x" + tail_hash.as_atom().hex(),
        }
        next_constructor = self._match(UnknownPuzzle(known_program=inner_puzzle))
        if next_constructor is not None:
            constructor_dict["also"] = next_constructor.info
        return PuzzleInfo(constructor_dict)

    def get_inner_puzzle(
        self, constructor: PuzzleInfo, puzzle_reveal: UnknownPuzzle, solution: Program | None = None
    ) -> Program | None:
        args = match_cat_puzzle(puzzle_reveal)
        if args is None:
            raise ValueError("This driver is not for the specified puzzle reveal")
        _, _, inner_puzzle = args
        also = constructor.also()
        if also is not None:
            deep_inner_puzzle: Program | None = self._get_inner_puzzle(
                also,
                UnknownPuzzle(known_program=inner_puzzle),
                solution.first() if solution is not None else None,
            )
            return deep_inner_puzzle
        else:
            return inner_puzzle
```

**File:** chia/wallet/nft_wallet/metadata_outer_puzzle.py (L39-95)
```python
    def match(self, puzzle: UnknownPuzzle) -> PuzzleInfo | None:
        matched, curried_args = match_metadata_layer_puzzle(puzzle)
        if matched:
            _, metadata, updater_hash, inner_puzzle = curried_args
            constructor_dict = {
                "type": "metadata",
                "metadata": metadata,
                "updater_hash": "0x" + updater_hash.as_atom().hex(),
            }
            next_constructor = self._match(UnknownPuzzle(known_program=inner_puzzle))
            if next_constructor is not None:
                constructor_dict["also"] = next_constructor.info
            return PuzzleInfo(constructor_dict)
        else:
            return None
        return None  # Uncomment above when match_metadata_layer_puzzle works

    def asset_id(self, constructor: PuzzleInfo) -> bytes32 | None:
        return bytes32(constructor["updater_hash"])

    def construct(self, constructor: PuzzleInfo, inner_puzzle: Program) -> Program:
        also = constructor.also()
        if also is not None:
            inner_puzzle = self._construct(also, inner_puzzle)
        return puzzle_for_metadata_layer(constructor["metadata"], constructor["updater_hash"], inner_puzzle)

    def get_inner_puzzle(
        self, constructor: PuzzleInfo, puzzle_reveal: UnknownPuzzle, solution: Program | None = None
    ) -> Program | None:
        matched, curried_args = match_metadata_layer_puzzle(puzzle_reveal)
        if matched:
            _, _, _, inner_puzzle = curried_args
            also = constructor.also()
            if also is not None:
                deep_inner_puzzle: Program | None = self._get_inner_puzzle(
                    also, UnknownPuzzle(known_program=inner_puzzle), None
                )
                return deep_inner_puzzle
            else:
                return inner_puzzle
        else:
            raise ValueError("This driver is not for the specified puzzle reveal")

    def get_inner_solution(self, constructor: PuzzleInfo, solution: Program) -> Program | None:
        my_inner_solution: Program = solution.first()
        also = constructor.also()
        if also:
            deep_inner_solution: Program | None = self._get_inner_solution(also, my_inner_solution)
            return deep_inner_solution
        else:
            return my_inner_solution

    def solve(self, constructor: PuzzleInfo, solver: Solver, inner_puzzle: Program, inner_solution: Program) -> Program:
        also = constructor.also()
        if also is not None:
            inner_solution = self._solve(also, solver, inner_puzzle, inner_solution)
        return solution_for_metadata_layer(inner_solution)
```

**File:** chia/wallet/nft_wallet/ownership_outer_puzzle.py (L39-101)
```python
    def match(self, puzzle: UnknownPuzzle) -> PuzzleInfo | None:
        matched, curried_args = match_ownership_layer_puzzle(puzzle)
        if matched:
            _, current_owner, transfer_program, inner_puzzle = curried_args
            owner_bytes: bytes = current_owner.as_python()
            tp_match: PuzzleInfo | None = self._match(UnknownPuzzle(known_program=transfer_program))
            constructor_dict = {
                "type": "ownership",
                "owner": "()" if owner_bytes == b"" else "0x" + owner_bytes.hex(),
                "transfer_program": (disassemble(transfer_program) if tp_match is None else tp_match.info),
            }
            next_constructor = self._match(UnknownPuzzle(known_program=inner_puzzle))
            if next_constructor is not None:
                constructor_dict["also"] = next_constructor.info
            return PuzzleInfo(constructor_dict)
        else:
            return None

    def asset_id(self, constructor: PuzzleInfo) -> bytes32 | None:
        return None

    def construct(self, constructor: PuzzleInfo, inner_puzzle: Program) -> Program:
        also = constructor.also()
        if also is not None:
            inner_puzzle = self._construct(also, inner_puzzle)
        transfer_program_info: PuzzleInfo | Program = constructor["transfer_program"]
        if isinstance(transfer_program_info, Program):
            transfer_program: Program = transfer_program_info
        else:
            transfer_program = self._construct(transfer_program_info, inner_puzzle)
        return puzzle_for_ownership_layer(constructor["owner"], transfer_program, inner_puzzle)

    def get_inner_puzzle(
        self, constructor: PuzzleInfo, puzzle_reveal: UnknownPuzzle, solution: Program | None = None
    ) -> Program | None:
        matched, curried_args = match_ownership_layer_puzzle(puzzle_reveal)
        if matched:
            _, _, _, inner_puzzle = curried_args
            also = constructor.also()
            if also is not None:
                deep_inner_puzzle: Program | None = self._get_inner_puzzle(
                    also, UnknownPuzzle(known_program=inner_puzzle), None
                )
                return deep_inner_puzzle
            else:
                return inner_puzzle
        else:
            raise ValueError("This driver is not for the specified puzzle reveal")

    def get_inner_solution(self, constructor: PuzzleInfo, solution: Program) -> Program | None:
        my_inner_solution: Program = solution.first()
        also = constructor.also()
        if also:
            deep_inner_solution: Program | None = self._get_inner_solution(also, my_inner_solution)
            return deep_inner_solution
        else:
            return my_inner_solution

    def solve(self, constructor: PuzzleInfo, solver: Solver, inner_puzzle: Program, inner_solution: Program) -> Program:
        also = constructor.also()
        if also is not None:
            inner_solution = self._solve(also, solver, inner_puzzle, inner_solution)
        return solution_for_ownership_layer(inner_solution)
```

**File:** chia/wallet/nft_wallet/singleton_outer_puzzle.py (L32-107)
```python
    def match(self, puzzle: UnknownPuzzle) -> PuzzleInfo | None:
        matched, curried_args = match_singleton_puzzle(puzzle)
        if matched:
            singleton_struct, inner_puzzle = curried_args
            pair = singleton_struct.pair
            assert pair is not None
            launcher_struct = pair[1].pair
            assert launcher_struct is not None
            launcher_id = launcher_struct[0].atom
            assert launcher_id is not None
            launcher_ph = launcher_struct[1].atom
            assert launcher_ph is not None
            constructor_dict: dict[str, Any] = {
                "type": "singleton",
                "launcher_id": "0x" + launcher_id.hex(),
                "launcher_ph": "0x" + launcher_ph.hex(),
            }
            next_constructor = self._match(UnknownPuzzle(known_program=inner_puzzle))
            if next_constructor is not None:
                constructor_dict["also"] = next_constructor.info
            return PuzzleInfo(constructor_dict)
        else:
            return None

    def asset_id(self, constructor: PuzzleInfo) -> bytes32 | None:
        return bytes32(constructor["launcher_id"])

    def construct(self, constructor: PuzzleInfo, inner_puzzle: Program) -> Program:
        also = constructor.also()
        if also is not None:
            inner_puzzle = self._construct(also, inner_puzzle)
        launcher_hash = constructor["launcher_ph"] if "launcher_ph" in constructor else SINGLETON_LAUNCHER_HASH
        return puzzle_for_singleton(constructor["launcher_id"], inner_puzzle, launcher_hash)

    def get_inner_puzzle(
        self, constructor: PuzzleInfo, puzzle_reveal: UnknownPuzzle, solution: Program | None = None
    ) -> Program | None:
        matched, curried_args = match_singleton_puzzle(puzzle_reveal)
        if matched:
            _, inner_puzzle = curried_args
            also = constructor.also()
            if also is not None:
                deep_inner_puzzle: Program | None = self._get_inner_puzzle(
                    also, UnknownPuzzle(known_program=inner_puzzle), None
                )
                return deep_inner_puzzle
            else:
                return inner_puzzle
        else:
            raise ValueError("This driver is not for the specified puzzle reveal")

    def get_inner_solution(self, constructor: PuzzleInfo, solution: Program) -> Program | None:
        my_inner_solution: Program = solution.at("rrf")
        also = constructor.also()
        if also:
            deep_inner_solution: Program | None = self._get_inner_solution(also, my_inner_solution)
            return deep_inner_solution
        else:
            return my_inner_solution

    def solve(self, constructor: PuzzleInfo, solver: Solver, inner_puzzle: Program, inner_solution: Program) -> Program:
        coin_bytes: bytes = solver["coin"]
        coin: Coin = Coin(bytes32(coin_bytes[0:32]), bytes32(coin_bytes[32:64]), uint64.from_bytes(coin_bytes[64:72]))
        parent_spend: CoinSpend = CoinSpend.from_bytes(solver["parent_spend"])
        parent_coin: Coin = parent_spend.coin
        also = constructor.also()
        if also is not None:
            inner_solution = self._solve(also, solver, inner_puzzle, inner_solution)
        matched, curried_args = match_singleton_puzzle(UnknownPuzzle(known_program=parent_spend.puzzle_reveal))
        assert matched
        _, parent_inner_puzzle = curried_args
        return solution_for_singleton(
            LineageProof(parent_coin.parent_coin_info, parent_inner_puzzle.get_tree_hash(), uint64(parent_coin.amount)),
            uint64(coin.amount),
            inner_solution,
        )
```

**File:** chia/_tests/wallet/cat_wallet/test_cat_outer_puzzle.py (L17-33)
```python
def test_cat_outer_puzzle() -> None:
    ACS = Program.to(1)
    tail = bytes32.zeros
    cat_puzzle: Program = construct_cat_puzzle(CAT_MOD, tail, ACS)
    double_cat_puzzle: Program = construct_cat_puzzle(CAT_MOD, tail, cat_puzzle)
    uncurried_cat_puzzle = UnknownPuzzle(known_program=double_cat_puzzle)
    cat_driver: PuzzleInfo | None = match_puzzle(uncurried_cat_puzzle)

    assert cat_driver is not None
    assert cat_driver.type() == "CAT"
    assert cat_driver["tail"] == tail
    inside_cat_driver: PuzzleInfo | None = cat_driver.also()
    assert inside_cat_driver is not None
    assert inside_cat_driver.type() == "CAT"
    assert inside_cat_driver["tail"] == tail
    assert construct_puzzle(cat_driver, ACS) == double_cat_puzzle
    assert get_inner_puzzle(cat_driver, uncurried_cat_puzzle) == ACS
```

**File:** chia/wallet/trading/offer.py (L244-291)
```python
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
```

**File:** chia/wallet/trading/offer.py (L634-663)
```python
    @classmethod
    def from_spend_bundle(cls, bundle: WalletSpendBundle) -> Offer:
        # Because of the `to_spend_bundle` method, we need to parse the dummy CoinSpends as `requested_payments`
        requested_payments: dict[bytes32 | None, list[NotarizedPayment]] = {}
        driver_dict: dict[bytes32, PuzzleInfo] = {}
        leftover_coin_spends: list[CoinSpend] = []
        for coin_spend in bundle.coin_spends:
            driver = match_puzzle(UnknownPuzzle(known_program=coin_spend.puzzle_reveal))
            if driver is not None:
                asset_id = create_asset_id(driver)
                assert asset_id is not None
                driver_dict[asset_id] = driver
            else:
                asset_id = None
            if coin_spend.coin.parent_coin_info == bytes32.zeros:
                notarized_payments: list[NotarizedPayment] = []
                for payment_group in Program.from_serialized(coin_spend.solution).as_iter():
                    nonce = bytes32(payment_group.first().as_atom())
                    payment_args_list = payment_group.rest().as_iter()
                    notarized_payments.extend(
                        [NotarizedPayment.from_condition_and_nonce(condition, nonce) for condition in payment_args_list]
                    )

                requested_payments[asset_id] = notarized_payments
            else:
                leftover_coin_spends.append(coin_spend)

        return cls(
            requested_payments, WalletSpendBundle(leftover_coin_spends, bundle.aggregated_signature), driver_dict
        )
```

**File:** chia/cmds/wallet_funcs.py (L807-833)
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
```

**File:** chia/wallet/trade_manager.py (L822-856)
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
        # We need to sandbox the transactions here because we're going to make our own
```
