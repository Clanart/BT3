Found the reachable chain. `Offer.from_bech32()` / `Offer.from_bytes()` → `Offer.from_spend_bundle()` calls `match_puzzle()` for every coin spend in an attacker-supplied offer file, with no depth or size bound on puzzle nesting. [1](#0-0) [2](#0-1) 

`match_puzzle()` dispatches to each `*OuterPuzzle.match()` (CAT, Singleton, Ownership, Metadata, CR), and each of those recursively calls `self._match(...)` (which is `match_puzzle` itself) on the inner puzzle if it doesn't already return `None`, building an arbitrarily long `also` chain with **no depth limit anywhere**: [3](#0-2) [4](#0-3) 

Since `construct_cat_puzzle`/CR layer construction impose no limit on how many times a CAT/CR/ownership/singleton layer can be nested inside another puzzle (only the wrapped puzzle's tree-hash needs to match the coin's puzzle hash — attacker fully controls the puzzle reveal bytes in an offer they author), one can build a puzzle reveal with thousands of nested CAT layers. When any wallet user calls `TakeOffer`/`take_offer` on that offer file (via `chia wallet take_offer` CLI or RPC `take_offer`), `Offer.from_bech32()` is invoked before any validation, which walks the fully attacker-controlled recursive `match_puzzle` chain and can exhaust Python's recursion limit, raising an uncaught `RecursionError` that crashes/aborts the wallet's offer-processing call. [5](#0-4) [6](#0-5) [7](#0-6) 

This is exactly the eml_parser bug class: unbounded Python-level recursion driven by an attacker-controlled nested structure (nested `message/rfc822` there, nested CAT/CR/singleton "also" puzzle layers here), with no depth check anywhere in the call chain (`Offer.from_bech32` → `Offer.from_spend_bundle` → `match_puzzle` → `*OuterPuzzle.match` → `match_puzzle` → ...).

### Title
Unbounded recursive puzzle-layer matching in `Offer.from_spend_bundle()`/`match_puzzle()` allows offer-file-triggered DoS via `RecursionError` - ([File: chia/wallet/outer_puzzles.py])

### Summary
Parsing an untrusted offer file (`Offer.from_bech32()`/`Offer.from_bytes()`) recursively matches each coin's puzzle reveal against known "outer puzzle" drivers (CAT, singleton, ownership, metadata, CR) via `match_puzzle()`. Each driver's `match()` method recursively re-invokes `match_puzzle()` on the puzzle's inner layer with no depth limit. An attacker can construct a puzzle reveal with thousands of nested layers (e.g., CAT-wrapped-in-CAT repeatedly) that still passes puzzle-hash-vs-coin checks (since the attacker fully controls both the coin and the puzzle reveal in an offer they craft), causing Python's recursion limit to be exceeded when a victim wallet parses/examines/takes the offer.

### Finding Description
`Offer.from_spend_bundle()` calls `match_puzzle(UnknownPuzzle(known_program=coin_spend.puzzle_reveal))` for every coin spend in the (attacker-authored) spend bundle embedded in the offer file, before any consensus-level validation occurs. [8](#0-7) 

`match_puzzle()` iterates known drivers and returns the first non-`None` match: [2](#0-1) 

Each outer-puzzle driver's `match()` recurses into `self._match` (bound to `match_puzzle` itself) on the puzzle's inner layer whenever a further match is found, with no counter or maximum-depth guard, e.g. in `CATOuterPuzzle.match()`: [3](#0-2)  and identically in `SingletonOuterPuzzle.match()`, `OwnershipOuterPuzzle.match()`, `MetadataOuterPuzzle.match()`, and `CROuterPuzzle.match()`.

Because the offer's `SpendBundle` is entirely attacker-constructed data (an offer author picks both the "settlement" coin puzzle hash and its puzzle reveal), there is no external constraint preventing arbitrarily deep nesting of these puzzle layers — unlike full-node consensus validation, which is never reached at this stage. This mirrors the reported `eml_parser` `RecursionError` bug class: unbounded Python-level recursion driven directly by attacker-controlled nested structure, with no depth check anywhere in the call chain.

### Impact Explanation
Any wallet user, RPC caller, or CLI user who inspects or accepts a maliciously crafted offer file triggers this code path: `Offer.from_bech32()`/`Offer.from_bytes()` is called unconditionally at parse time by `TakeOffer.parsed_offer` [5](#0-4) , by the `take_offer` RPC endpoint [6](#0-5) , and by the CLI's `take_offer` command before any confirmation prompt [9](#0-8) . A `RecursionError` here is uncaught and will abort the wallet's request-handling coroutine for the wallet RPC service (and, depending on process/task lifecycle, may destabilize the wallet daemon process), denying service to a benign offer counterparty who merely opens/examines an untrusted offer.

### Likelihood Explanation
Likelihood is Medium: the attacker (an "offer counterparty") needs no privileged access — only the ability to author and deliver an offer file/bech32 string to a victim (a routine, expected trust boundary in Chia's offer-file ecosystem, where offers are commonly shared publicly). Constructing the deeply-nested puzzle-reveal bytes requires only local CLVM tooling and no interaction with any live node.

### Recommendation
Add an explicit recursion/nesting depth limit to `match_puzzle()` (and to each `*OuterPuzzle.match()` implementation's recursive call into `self._match`), raising a clean, catchable error (e.g., `ValueError`) once a configured maximum layer count is exceeded, mirroring the fix pattern recommended for `eml_parser` (bounded recursion depth). Alternatively, refactor `match_puzzle` to an explicit iterative loop with a maximum-iteration cap, and ensure `Offer.from_bytes()`/`Offer.from_spend_bundle()` wrap puzzle-matching in a try/except that converts unexpected exceptions (including `RecursionError`) into a normal "invalid offer" error rather than propagating an uncaught interpreter-level exception.

### Proof of Concept
Conceptually (using existing test helpers such as `construct_cat_puzzle`/`construct_cr_layer` seen in `chia/_tests/wallet/cat_wallet/test_cat_outer_puzzle.py` and `chia/_tests/wallet/vc_wallet/test_cr_outer_puzzle.py`):
1. Build an innermost puzzle `ACS = Program.to(1)`.
2. Repeatedly wrap it N times (N ≈ 1000+) using `construct_cat_puzzle(CAT_MOD, tail, inner)` (each iteration's output becomes the next iteration's `inner`), producing a puzzle `P_N` whose true `get_tree_hash()` is used as a coin's `puzzle_hash`.
3. Construct a `CoinSpend(coin, P_N, solution)` with `coin.parent_coin_info == bytes32.zeros` (to satisfy the `Offer` settlement-coin convention) and wrap it in a `WalletSpendBundle`.
4. Serialize via `Offer(...).to_bech32()` (or hand-craft the bech32 offer bytes directly) and have a victim call `TakeOffer(offer=offer_bech32, ...)` via RPC, or run `chia wallet take_offer -f <fp> <file>` — both paths call `Offer.from_bech32()` → `Offer.from_spend_bundle()` → `match_puzzle()`, recursing N times through `CATOuterPuzzle.match()`/`match_puzzle()` and raising an uncaught `RecursionError` before N approaches Python's default recursion limit (~1000).

### Citations

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

**File:** chia/wallet/outer_puzzles.py (L49-54)
```python
def match_puzzle(puzzle: UnknownPuzzle) -> PuzzleInfo | None:
    for driver in driver_lookup.values():
        potential_info: PuzzleInfo | None = driver.match(puzzle)
        if potential_info is not None:
            return potential_info
    return None
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

**File:** chia/wallet/wallet_request_types.py (L2304-2312)
```python
@streamable
@dataclass(kw_only=True, frozen=True)
class TakeOffer(TransactionEndpointRequest):
    offer: str
    solver: Solver | None = None

    @cached_property
    def parsed_offer(self) -> Offer:
        return Offer.from_bech32(self.offer)
```

**File:** chia/wallet/wallet_rpc_api.py (L2050-2064)
```python
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

**File:** chia/cmds/wallet_funcs.py (L807-832)
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

```
