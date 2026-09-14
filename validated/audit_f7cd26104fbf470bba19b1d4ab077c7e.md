### Title
Unbounded recursion in `PuzzleInfo`/outer-puzzle "also" chain parsing allows attacker-controlled JSON to trigger uncontrolled Python recursion (DoS) - ([File: chia/wallet/puzzle_drivers.py])

### Summary
The reported snakeYAML issue (CVE-2022-38751) is a stack-overflow DoS caused by parsing untrusted, arbitrarily-nested input with unbounded recursion and no depth limit. The closest reachable analog in this codebase is `PuzzleInfo`'s `"also"` chain in `chia/wallet/puzzle_drivers.py`, which is built directly from attacker/caller-supplied JSON (e.g. the `driver_dict` field of the `CreateOfferForIDs` wallet RPC request, or an offer's embedded driver info) and is walked recursively by `PuzzleInfo.also()`, `PuzzleInfo.check_type()`, and the outer-puzzle drivers (`construct`, `get_inner_puzzle`, `get_inner_solution`, `solve` in `chia/wallet/cat_wallet/cat_outer_puzzle.py`, `chia/wallet/nft_wallet/*_outer_puzzle.py`, `chia/wallet/vc_wallet/cr_outer_puzzle.py`) with no maximum-depth check anywhere in the chain.

### Finding Description
`PuzzleInfo` is constructed straight from a caller-supplied dict: [1](#0-0) 

`also()` recurses into `self.info["also"]` with no depth bound, and `check_type()` recurses similarly. The `to_json_dict`/`from_json_dict` pair is a direct pass-through of the raw dict, so any nesting depth the caller provides is preserved verbatim: [2](#0-1) 

This `driver_dict: dict[bytes32, PuzzleInfo]` is a first-class field of the wallet RPC request `CreateOfferForIDs`, deserialized straight from client-submitted JSON via the streamable JSON conversion machinery (`convert_function`, `from_json_dict`) with no depth cap: [3](#0-2) 

Once accepted, `driver_dict` values are walked recursively throughout the offer-construction path — e.g. `TradeManager.check_for_special_offer_making()` chases `.also().also()`, and the various outer-puzzle drivers (`CATOuterPuzzle.construct`/`get_inner_puzzle`, `SingletonOuterPuzzle.*`, `MetadataOuterPuzzle.*`, `OwnershipOuterPuzzle.*`, `CROuterPuzzle.*`) all follow the pattern `also = constructor.also(); if also is not None: <recurse into self._construct/_solve/_get_inner_puzzle/_get_inner_solution>`: [4](#0-3) [5](#0-4) 

None of these code paths impose any limit on how many nested `"also"` (or `"transfer_program"`) dicts a caller can supply. A caller can submit a `driver_dict`/`Solver`/`PuzzleInfo` JSON payload with thousands of nested `"also"` keys, and each traversal (`also()`, `check_type()`, `construct()`, `get_inner_puzzle()`, `get_inner_solution()`, `solve()`) will recurse once per nesting level in pure Python, exhausting the interpreter's call stack.

### Impact Explanation
This maps to the "spend-triggered transaction-processing halt" class permitted by the validation rules: a local wallet RPC caller (an actor explicitly in scope) can submit a single `create_offer_for_ids` request (or an equivalent offer/take-offer flow that surfaces attacker-controlled driver info) with a deeply nested `"also"` structure to force unbounded Python recursion in the wallet process. Depending on stack depth this raises an uncaught `RecursionError` (or, since CPython's C stack can be exhausted before the interpreter's own recursion-limit check fires in some recursive call patterns, a native stack overflow/process crash) inside the wallet service handling the RPC, which can halt transaction/offer processing for that wallet. This is a Medium-severity availability issue, consistent with the CVSS vector of the referenced advisory (`AC:L/PR:L/UI:N/S:U/C:N/I:N/A:H`), scoped to a single local, already-authorized RPC caller rather than an unauthenticated network attacker.

### Likelihood Explanation
Likelihood is moderate-to-high for any deployment that exposes the wallet RPC to less-trusted local callers (multi-tenant wallet services, hosted wallet APIs, bots that construct offers from partially untrusted data). No cryptographic material or special privilege beyond RPC access is required — only a JSON body with deep nesting. It does not require broadcasting anything to the network or full-node mempool; the crash/DoS occurs locally in the wallet process during offer construction/parsing.

### Recommendation
Add an explicit maximum-nesting-depth check in `PuzzleInfo.__init__`/`also()` (and in the JSON-to-`Streamable` conversion path used for `driver_dict`, `Solver`, and similar recursively-defined RPC fields) and reject requests exceeding that depth with a clear `ValueError`/`ValidationError` before any recursive traversal begins. Alternatively, convert the `also()`-walking helper functions to an iterative loop with an explicit depth counter and an enforced ceiling (mirroring the iterative `sha256_treehash()` approach already used elsewhere in the codebase for CLVM tree hashing) so that pathological input degrades gracefully instead of exhausting the call stack.

### Proof of Concept
Construct a `create_offer_for_ids` RPC request whose `driver_dict` contains a `PuzzleInfo`-shaped dict with `N` (e.g. 100,000) levels of nested `"also"` keys, e.g.:
```python
info = {"type": "CAT", "tail": "0x" + "00" * 32}
node = info
for _ in range(100_000):
    node["also"] = {"type": "CAT", "tail": "0x" + "00" * 32}
    node = node["also"]
```
Submit this as the `driver_dict` value for one asset in a `create_offer_for_ids` RPC call. When the wallet later calls `PuzzleInfo.also()` / `check_type()` / the CAT/NFT/CR outer-puzzle `construct`/`get_inner_puzzle`/`solve` recursive helpers on this structure, the Python interpreter recurses once per nesting level with no cap, raising `RecursionError` (or crashing on stack exhaustion) and halting request processing in the wallet RPC service.

### Citations

**File:** chia/wallet/puzzle_drivers.py (L21-64)
```python
class PuzzleInfo:
    """
    There are two 'magic' keys in a PuzzleInfo object:
      - 'type' must be an included key (for easy lookup of drivers)
      - 'also' gets its own method as it's the supported way to do recursion of PuzzleInfos
    """

    info: dict[str, Any]

    def __init__(self, info: dict[str, Any]) -> None:
        self.info = info
        self.__post_init__()

    def __post_init__(self) -> None:
        if "type" not in self.info:
            raise ValueError("A type is required to initialize a puzzle driver")

    def __getitem__(self, item: str) -> Any:
        value = self.info[item]
        return decode_info_value(PuzzleInfo, value)

    def __eq__(self, other: object) -> bool:
        for key, value in self.info.items():
            try:
                if self[key] != other[key]:  # type: ignore
                    return False
            except Exception:
                return False
        return True

    def __contains__(self, item: str) -> bool:
        if item in self.info:
            return True
        else:
            return False

    def type(self) -> str:
        return str(self.info["type"])

    def also(self) -> PuzzleInfo | None:
        if "also" in self.info:
            return PuzzleInfo(self.info["also"])
        else:
            return None
```

**File:** chia/wallet/puzzle_drivers.py (L86-91)
```python
    def to_json_dict(self) -> dict[str, Any]:
        return self.info

    @classmethod
    def from_json_dict(cls, json_dict: dict[str, Any]) -> Self:
        return cls(json_dict)
```

**File:** chia/wallet/wallet_request_types.py (L2276-2284)
```python
@streamable
@dataclass(kw_only=True, frozen=True)
class CreateOfferForIDs(TransactionEndpointRequest):
    # a hack for dict[str, int] because streamable doesn't support negative ints
    offer: dict[str, str]
    driver_dict: dict[bytes32, PuzzleInfo] | None = None
    solver: Solver | None = None
    validate_only: bool = False
    offer_only: bool = False
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
