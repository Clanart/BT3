### Title
Unbounded recursive puzzle-driver matching on attacker-controlled offer nesting causes wallet RecursionError DoS - ([File: chia/wallet/outer_puzzles.py])

### Summary
### Finding Description
Chia's outer-puzzle driver system (`chia/wallet/outer_puzzles.py`, `chia/wallet/cat_wallet/cat_outer_puzzle.py`, `chia/wallet/nft_wallet/singleton_outer_puzzle.py`, `chia/wallet/nft_wallet/ownership_outer_puzzle.py`, `chia/wallet/nft_wallet/metadata_outer_puzzle.py`, `chia/wallet/vc_wallet/cr_outer_puzzle.py`) implements a chain-of-responsibility pattern where each driver's `match()`, `get_inner_puzzle()`, and `get_inner_solution()` recursively call `self._match(...)`/`self._get_inner_puzzle(...)`/`self._get_inner_solution(...)` on the *inner* puzzle layer whenever that inner puzzle also matches a known driver type (the `"also"` chain), e.g.: [1](#0-0) [2](#0-1) 

This chain depth is entirely a function of how many curried puzzle layers exist in an attacker-supplied puzzle reveal — there is no depth cap. `Program.curry()` allows arbitrary nesting cheaply (each layer is just `(2 (1 . self) args)`), as demonstrated by tests wrapping CAT-in-CAT arbitrarily deep: [3](#0-2) 

The reachable entry point is `Offer` parsing/summarization: when a wallet receives an untrusted offer file from a counterparty and calls `Offer._get_offered_coins()`, it calls `match_puzzle()` (and subsequently `get_inner_puzzle`/`get_inner_solution`) on each coin spend's puzzle reveal: [4](#0-3) 

`match_puzzle()` dispatches into the recursive driver chain shown above: [5](#0-4) 

This exactly matches the GHSA-g56x-7j6w-g8r8 bug class: a recursive-descent structure walking attacker-controlled nested input with no depth limit, leading to a Python `RecursionError` (the Python analog of a JVM `StackOverflowError`) — CWE-400 / uncontrolled resource consumption.

A related, independently reachable recursive path exists in the custody architecture used for clawback/custody puzzle syncing: `PuzzleWithRestrictions.from_memo()` recursively reconstructs `MofN` members from an on-chain memo with no depth bound, and `unknown_puzzles` recurses similarly: [6](#0-5) 

### Impact Explanation
A malicious offer counterparty can craft an `Offer` (or a coin spend whose puzzle reveal is deeply curried/nested, e.g., many stacked CAT/singleton/ownership/CR layers) such that when the victim's wallet parses it — via `get_offer_summary`, `take_offer`, or any code path that calls `Offer.get_offered_coins()`/`.summary()` — the recursive `match`/`get_inner_puzzle`/`get_inner_solution` chain exceeds Python's default recursion limit (1000 frames) and raises `RecursionError`. This is an unhandled exception in wallet RPC processing that can crash or hang the wallet's request-handling task, denying service to the wallet operator without requiring any funds, signature, or prior trust relationship — the attacker only needs to hand the victim an offer file or offer string. This matches "offers and trades" and "clawback and custody" in scope, and produces a spend-triggered transaction-processing halt (wallet-side).

### Likelihood Explanation
Constructing arbitrarily deep curried nesting is cheap and requires no special CLVM execution cost, since `Program.curry()` composition is simple structural nesting done outside of any cost-metered CLVM run; the recursive Python matching happens before/independent of any mempool cost check. An attacker only needs to distribute a crafted offer file to a victim wallet user — a normal, low-friction interaction pattern in the Chia offer/trade ecosystem. No special privileges are required.

### Recommendation
- Add an explicit maximum recursion/nesting depth check in the outer-puzzle driver chain (`match_puzzle`, and the `get_inner_puzzle`/`get_inner_solution`/`construct`/`solve` recursive calls in `cat_outer_puzzle.py`, `singleton_outer_puzzle.py`, `ownership_outer_puzzle.py`, `metadata_outer_puzzle.py`, `cr_outer_puzzle.py`), raising a clean `ValueError`/`ValidationError` once a sane limit (e.g., a few dozen layers) is exceeded.
- Convert the recursive traversal into an iterative loop with an explicit stack, similar to the non-recursive `sha256_treehash()` pattern already used elsewhere in the codebase, to remove reliance on the Python call stack entirely.
- Apply the same depth bound to `PuzzleWithRestrictions.from_memo()` / `unknown_puzzles` in the custody architecture.
- Wrap `Offer` parsing/summarization RPC handlers to catch `RecursionError` and convert it into a normal RPC error response rather than allowing it to propagate/crash the service task.

### Proof of Concept
1. Construct an inner ACS puzzle `P0 = Program.to(1)`.
2. Iteratively wrap it N times (N ~ 2000, well above Python's default recursion limit) using `construct_cat_puzzle(CAT_MOD, tail, P_{i-1})` (or equivalently chain singleton/ownership/CR constructors), producing `P_N` — a legitimately-curriable, cheap-to-build puzzle with N nested outer layers.
3. Build a `CoinSpend`/`WalletSpendBundle` using `P_N` as the `puzzle_reveal` (solution content is irrelevant to the DoS since the crash happens during driver matching, before puzzle execution), and package it into an `Offer` object (as done in `chia/_tests/wallet/cat_wallet/test_offer_lifecycle.py`).
4. Serialize this to an offer file/string and deliver it to a victim wallet.
5. Victim wallet calls `Offer.from_bytes(...)` then invokes `.summary()`/`.get_offered_coins()` (directly, or via the `get_offer_summary`/`take_offer` RPC endpoints) which calls `match_puzzle()` → `CATOuterPuzzle.match()` recursing N times through `self._match(...)`.
6. Python raises `RecursionError` once the call depth exceeds `sys.getrecursionlimit()` (default 1000), crashing/erroring the wallet's offer-processing task.

*Note: I was unable to fully trace which exact wallet RPC endpoints (e.g., `take_offer`, `get_offer_summary`) call into `Offer.get_offered_coins()`/`.summary()` before running out of iterations — the grep for those endpoints in `chia/wallet/wallet_rpc_api.py` returned matches but I did not get to read their bodies. This should be verified by reading `chia/wallet/wallet_rpc_api.py` around the `take_offer`/`get_offer_summary` handlers and `chia/wallet/trading/trade_manager.py` to confirm the exact call chain from network-facing RPC input to `_get_offered_coins()`.*

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

**File:** chia/wallet/nft_wallet/singleton_outer_puzzle.py (L66-81)
```python
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
```

**File:** chia/_tests/wallet/cat_wallet/test_cat_outer_puzzle.py (L17-23)
```python
def test_cat_outer_puzzle() -> None:
    ACS = Program.to(1)
    tail = bytes32.zeros
    cat_puzzle: Program = construct_cat_puzzle(CAT_MOD, tail, ACS)
    double_cat_puzzle: Program = construct_cat_puzzle(CAT_MOD, tail, cat_puzzle)
    uncurried_cat_puzzle = UnknownPuzzle(known_program=double_cat_puzzle)
    cat_driver: PuzzleInfo | None = match_puzzle(uncurried_cat_puzzle)
```

**File:** chia/wallet/trading/offer.py (L244-266)
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

**File:** chia/wallet/puzzles/custody/custody_architecture.py (L271-318)
```python
    @classmethod
    def from_memo(cls, memo: Program) -> PuzzleWithRestrictions:
        if memo.atom is not None or memo.first() != Program.to(cls.spec_namespace):
            raise ValueError("Attempting to parse a memo that does not belong to this spec")
        nonce = memo.at("rf")
        restriction_hints_prog = memo.at("rrf")
        further_branching_prog = memo.at("rrrf")
        puzzle_hint_prog = memo.at("rrrrf")
        additional_memos = memo.at("rrrrrf") if memo.at("rrrrr").atom is None else None
        restriction_hints = [RestrictionHint.from_program(hint) for hint in restriction_hints_prog.as_iter()]
        further_branching = further_branching_prog != Program.to(None)
        if further_branching:
            m_of_n_hint = MofNHint.from_program(puzzle_hint_prog)
            puzzle: MIPSComponent = MofN(
                m=m_of_n_hint.m, members=[PuzzleWithRestrictions.from_memo(memo) for memo in m_of_n_hint.member_memos]
            )
        else:
            puzzle_hint = MemberHint.from_program(puzzle_hint_prog)
            puzzle = UnknownMember(puzzle_hint)

        return PuzzleWithRestrictions(
            nonce=nonce.as_int(),
            restrictions=[UnknownRestriction(hint) for hint in restriction_hints],
            puzzle=puzzle,
            additional_memos=additional_memos,
        )

    @property
    def unknown_puzzles(self) -> Mapping[bytes32, UnknownMember | UnknownRestriction]:
        unknown_restrictions = {
            ur.restriction_hint.puzhash: ur for ur in self.restrictions if isinstance(ur, UnknownRestriction)
        }

        unknown_puzzles: Mapping[bytes32, UnknownMember | UnknownRestriction]
        if isinstance(self.puzzle, UnknownMember):
            unknown_puzzles = {self.puzzle.puzzle_hint.puzhash: self.puzzle}
        elif isinstance(self.puzzle, MofN):
            unknown_puzzles = {
                uph: up
                for puz_w_restriction in self.puzzle.members
                for uph, up in puz_w_restriction.unknown_puzzles.items()
            }
        else:
            unknown_puzzles = {}
        return {
            **unknown_puzzles,
            **unknown_restrictions,
        }
```
