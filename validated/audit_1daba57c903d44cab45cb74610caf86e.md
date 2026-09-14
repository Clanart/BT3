### Title
Uncontrolled Recursion in Wallet Outer-Puzzle Driver Chain (`also()`/`match_puzzle`) Enables Attacker-Triggered Stack Exhaustion on Offer Parsing - (File: `chia/wallet/outer_puzzles.py`, `chia/wallet/puzzle_drivers.py`, `chia/wallet/trading/offer.py`)

### Summary
The wallet's outer-puzzle driver framework (`match_puzzle`, `construct_puzzle`, `get_inner_puzzle`, `get_inner_solution`, `solve_puzzle` in `chia/wallet/outer_puzzles.py`) recurses once for every nested outer-puzzle layer (CAT, singleton, NFT metadata, NFT ownership, CR) discovered in a puzzle reveal, with no depth limit anywhere in the chain. `PuzzleInfo.also()`/`check_type()` in `chia/wallet/puzzle_drivers.py` encode the same unbounded recursive relationship. This mirrors the bug class in CVE-2026-24401 (Avahi `lookup_handle_cname`): unbounded recursion driven entirely by attacker-supplied, self-referential/nested input with no cycle or depth guard.

### Finding Description
Each outer-puzzle driver (`CATOuterPuzzle.match`, `SingletonOuterPuzzle.match`, `MetadataOuterPuzzle.match`, `OwnershipOuterPuzzle.match`, `CROuterPuzzle.match`, defined in `chia/wallet/cat_wallet/cat_outer_puzzle.py`, `chia/wallet/nft_wallet/singleton_outer_puzzle.py`, `chia/wallet/nft_wallet/metadata_outer_puzzle.py`, `chia/wallet/nft_wallet/ownership_outer_puzzle.py`, `chia/wallet/vc_wallet/cr_outer_puzzle.py`) calls back into `self._match(...)` on its own `inner_puzzle` to see if another recognized outer layer sits underneath: [1](#0-0) 

This chain is wired through `driver_lookup`/`match_puzzle` in `chia/wallet/outer_puzzles.py`: [2](#0-1) 

Because `match_cat_puzzle`/`match_singleton_puzzle`/`match_metadata_layer_puzzle`/etc. only check that the current layer's mod matches a known template; they place no restriction on what the *inner puzzle* is. An attacker can construct a puzzle reveal that is, e.g., `CAT_MOD` curried around another `CAT_MOD`-curried puzzle, repeated thousands of times (each layer is cheap to construct and serialize; CLVM `curry` nesting scales linearly in size, not exponentially). Each additional layer forces one more level of Python recursion through `match()`/`get_inner_puzzle()`/`get_inner_solution()`/`construct()`/`solve()`, and further through `PuzzleInfo.also()`/`check_type()`: [3](#0-2) 

None of these paths cap recursion depth or detect the (structurally legal but semantically meaningless) repetition of the same wrapper type.

The most directly reachable trigger is offer/trade parsing. When a wallet processes an untrusted `Offer` (received from an offer counterparty as a spend-bundle/offer file, well before signatures or full spend validity are checked), `Offer._get_offered_coins()` and `Offer.from_spend_bundle()` call `match_puzzle()` on every `puzzle_reveal` in the bundle: [4](#0-3) [5](#0-4) 

This is invoked from the wallet's normal offer-handling flow (`chia/wallet/trade_manager.py`, `chia/wallet/wallet_rpc_api.py`) whenever a user or RPC caller inspects/accepts an offer, i.e. it is reachable from an unprivileged offer counterparty sending a crafted offer file, or from a wallet's own coin-state processing path where `match_puzzle`/`UnknownPuzzle` is used on puzzle reveals pulled from the chain (e.g. `chia/wallet/wallet_state_manager.py`, `chia/wallet/cat_wallet/cat_wallet.py`).

I found an existing test module (`chia/_tests/wallet/test_offer_parsing_performance.py`) explicitly dedicated to offer-parsing performance, indicating the project is aware that offer-parsing cost/complexity is a sensitivity area, but I could not fully confirm from the index whether that test enforces or measures a depth bound on nested outer-puzzle layers specifically (I could not retrieve its contents in this session due to a tool error), so I cannot state with certainty whether a mitigation already exists that would cap the depth of `also()` chaining in practice.

### Impact Explanation
If unbounded, this allows a spend-bundle submitter or offer counterparty to force the recipient wallet process to recurse per nested layer with no bound, which in CPython either raises an uncaught `RecursionError` (if not wrapped in exception handling at the call sites shown above) or, in older/pathological interpreter configurations, exhausts the C stack. Either outcome is a spend/offer-triggered denial of service against the wallet process handling the malicious puzzle reveal — consistent with the "spend-triggered transaction-processing halt" impact category. No coin movement, inflation, or forged identity results; the impact is availability-only against the wallet component that parses the crafted puzzle/offer.

### Likelihood Explanation
Constructing a deeply nested but structurally valid CAT/singleton/NFT-layer puzzle reveal is inexpensive (linear cost in the number of layers to build and serialize with `curry()`), and delivering it only requires sending an offer file or having the wallet observe/sync a coin with such a puzzle reveal — both are actions available to an unprivileged, unauthenticated counterparty. However, I was not able to confirm in this session whether `match_puzzle`/offer-processing call sites already wrap this logic in broad exception handling that would turn a `RecursionError` into a harmless rejected/failed offer rather than a process crash, nor whether `test_offer_parsing_performance.py` already exercises and bounds this exact nested-layer scenario. This uncertainty should be resolved by directly reviewing that test file and the exception-handling wrappers around `trade_manager.py`'s offer-response code path before treating this as a confirmed, unmitigated vulnerability.

### Recommendation
Add an explicit, low nesting-depth limit (e.g., a counter threaded through `match()`/`get_inner_puzzle()`/`get_inner_solution()`/`construct()`/`solve()` and `PuzzleInfo.also()`/`check_type()`) that rejects/returns `None` once a reasonable maximum outer-layer depth is exceeded, mirroring how `sha256_treehash` was deliberately made iterative specifically "to avoid Python recursion limits on deeply nested CLVM" (see `chia/types/blockchain_format/tree_hash.py`). Additionally, ensure all call sites that invoke `match_puzzle`/`Offer.from_spend_bundle`/`Offer._get_offered_coins` on externally supplied data catch `RecursionError` (or the depth-limit exception) and fail the offer/spend gracefully rather than allowing the exception to propagate and crash the wallet service.

### Proof of Concept
Conceptually (mirrors the existing unit test pattern already present in the repo, `chia/_tests/wallet/cat_wallet/test_cat_outer_puzzle.py`, but scaled up):
1. Build `inner = ACS` (any always-true inner puzzle).
2. For `i in range(N)` with large `N` (e.g. 5,000–50,000), set `inner = construct_cat_puzzle(CAT_MOD, tail, inner)`, producing `N` nested CAT layers.
3. Place `inner` as the `puzzle_reveal` of a coin spend inside a `WalletSpendBundle`/`Offer`.
4. Submit this as an offer to a wallet, or call `match_puzzle(UnknownPuzzle(known_program=inner))` / `Offer.from_spend_bundle(bundle)` / `Offer._get_offered_coins()` directly.
5. Observe unbounded Python recursion depth proportional to `N`, eventually raising `RecursionError` (or exhausting the stack), inside the wallet process handling the offer. [6](#0-5)

### Citations

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

**File:** chia/wallet/outer_puzzles.py (L49-54)
```python
def match_puzzle(puzzle: UnknownPuzzle) -> PuzzleInfo | None:
    for driver in driver_lookup.values():
        potential_info: PuzzleInfo | None = driver.match(puzzle)
        if potential_info is not None:
            return potential_info
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

**File:** chia/wallet/trading/offer.py (L251-259)
```python
            parent_puzzle: UnknownPuzzle = UnknownPuzzle(known_program=parent_spend.puzzle_reveal)
            parent_solution = Program.from_serialized(parent_spend.solution)
            additions: list[Coin] = self._additions[parent_spend.coin]

            puzzle_driver = match_puzzle(parent_puzzle)
            if puzzle_driver is not None:
                asset_id = create_asset_id(puzzle_driver)
                inner_puzzle: Program | None = get_inner_puzzle(puzzle_driver, parent_puzzle, parent_solution)
                inner_solution: Program | None = get_inner_solution(puzzle_driver, parent_solution)
```

**File:** chia/wallet/trading/offer.py (L640-645)
```python
        for coin_spend in bundle.coin_spends:
            driver = match_puzzle(UnknownPuzzle(known_program=coin_spend.puzzle_reveal))
            if driver is not None:
                asset_id = create_asset_id(driver)
                assert asset_id is not None
                driver_dict[asset_id] = driver
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
