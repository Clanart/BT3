### Title
Unbounded Python recursion in `PuzzleWithRestrictions.from_memo` allows a wallet-sync-triggered DoS via crafted MofN memo nesting - (File: chia/wallet/puzzles/custody/custody_architecture.py)

### Summary
`PuzzleWithRestrictions.from_memo` recursively parses a custody-architecture memo blob embedded in a coin's hint/memo, and when the memo describes an `MofN` construct it recurses into `from_memo` once per member [1](#0-0) . The recursion depth is fully controlled by the attacker-supplied memo structure (nested `MofN` hints), with no depth limit enforced anywhere in the parsing path.

### Finding Description
`from_memo` decodes the CHIP-0043 memo format and, when `further_branching` is true, builds an `MofN` by recursively calling `PuzzleWithRestrictions.from_memo` for every entry in `MofNHint.member_memos` [2](#0-1) . This mirrors the CVE-2020-8285 bug class: a callback/parser that recurses once per "entry" supplied by an untrusted remote party, with no bound on how many times it can recurse.

This code is reached from `PlotNFT.get_next_from_coin_spend`, which is used while syncing plotNFT/pool singleton state: when the memoization branch is taken it calls `PuzzleWithRestrictions.from_memo(singleton_create_coin.memo_blob.rest())` directly on data taken from a `CREATE_COIN` condition's memo, which is produced by whatever puzzle/solution the counterparty (or any peer serving coin-spend data) supplies [3](#0-2) . The `CreateCoin.memo_blob` value comes directly from the inner puzzle's output conditions of an untrusted coin spend, so an attacker who creates (or serves) such a coin spend fully controls the shape of the memo, including how many levels of `MofN` nesting it contains.

Because `from_memo` is implemented with plain Python recursion (one stack frame per nesting level) rather than an iterative/worklist algorithm (contrast with `sha256_treehash`, which explicitly avoids Python recursion "so we don't have to worry about blowing out the python stack" [4](#0-3) ), a sufficiently deep memo (in the low thousands of nesting levels, well under Python's default recursion limit and easily encodable in a small number of bytes) will raise `RecursionError` while the wallet is processing that coin's spend.

### Impact Explanation
This hits a wallet-reachable state-sync code path (`plotnft/pool transitions`, an explicitly in-scope category). An uncaught `RecursionError` raised deep inside coin-spend processing during sync can abort processing of that spend/batch, potentially halting the wallet's ability to make progress past a poisoned coin (a spend-triggered transaction-processing halt). It is a denial-of-service against the specific wallet processing that coin — analogous in nature (recursive-parse stack exhaustion driven by attacker-controlled fan-out/depth) to the libcurl wildcard bug, though the concrete blast radius (single wallet vs. full node) and severity are lower than a consensus-level halt.

### Likelihood Explanation
Likelihood is moderate: the attacker needs to get a spend containing this memo format onto the chain (or served to the target wallet as a coin-spend during sync) tied to a puzzle hash the target wallet is interested in (e.g., a plotNFT/pool singleton the wallet tracks). Crafting nested `MofN` memos of sufficient depth is straightforward once such a spend is accepted; no signature or additional privilege is required beyond being able to produce/broadcast a valid spend that creates a coin with the crafted memo.

### Recommendation
Convert `PuzzleWithRestrictions.from_memo` (and the corresponding `MofN` reconstruction) to an iterative/stack-based algorithm, or enforce an explicit maximum nesting depth (rejecting memos beyond a small bound, e.g. the same bound the CLVM cost model would realistically allow) before recursing. Apply the same review to any other CHIP-0043-related recursive memo/puzzle parsers (e.g., `unknown_puzzles` in the same file, which also recurses over `MofN.members` [5](#0-4) ).

### Proof of Concept
Not fully executable without running the wallet sync stack, but conceptually:
1. Build a `MofNHint` whose `member_memos` contains a single nested `MofNHint` program, and repeat this nesting N times (N ≈ 2000–5000, comfortably exceeding Python's default recursion limit while still being a small CLVM program).
2. Curry/create a `PuzzleWithRestrictions` memo (`spec_namespace = "CHIP-0043"`) wrapping this deeply nested `MofNHint` as `puzzle_hint_prog`, per the layout consumed by `from_memo` [6](#0-5) .
3. Emit a `CREATE_COIN` condition whose memo is this blob, from any coin spend that a target wallet will sync and interpret via `PlotNFT.get_next_from_coin_spend`'s memoization fallback branch [3](#0-2) .
4. When the victim wallet syncs this spend, `PuzzleWithRestrictions.from_memo` recurses N times and raises `RecursionError`, aborting processing of that coin.

Note: I could not fully trace every caller/consumer of `PlotNFT.get_next_from_coin_spend` and confirm whether any wrapping `try/except` currently swallows `RecursionError` broadly enough to prevent a sync halt — this would need to be verified directly in a running environment (e.g., a Devin session with full repo/test access) before treating this as a confirmed, unmitigated DoS.

### Citations

**File:** chia/wallet/puzzles/custody/custody_architecture.py (L271-296)
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
```

**File:** chia/wallet/puzzles/custody/custody_architecture.py (L298-318)
```python
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

**File:** chia/pools/plotnft_drivers.py (L476-486)
```python
        # Finally, we try to look for the memos
        if plotnft_puzzle is None:
            if singleton_create_coin.memo_blob is None:
                raise GetNextPlotNFTError("Invalid memoization of PlotNFT")
            try:
                unknown_inner_puzzle = PuzzleWithRestrictions.from_memo(singleton_create_coin.memo_blob.rest())
            except ValueError:
                raise GetNextPlotNFTError("Invalid memoization of PlotNFT")
            if unknown_inner_puzzle.additional_memos is None:
                raise GetNextPlotNFTError("Invalid memoization of PlotNFT")
            pubkey = G1Element.from_bytes(unknown_inner_puzzle.additional_memos.at("f").as_atom())
```

**File:** chia/types/blockchain_format/tree_hash.py (L1-7)
```python
"""
This is an implementation of `sha256_treehash`, used to calculate
puzzle hashes in clvm.

This implementation goes to great pains to be non-recursive so we don't
have to worry about blowing out the python stack.
"""
```
