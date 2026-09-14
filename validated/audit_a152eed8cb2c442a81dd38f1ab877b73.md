### Title
Unbounded recursive parsing of untrusted `MofN` custody memos causes Python stack overflow / wallet crash - ([File: chia/wallet/puzzles/custody/custody_architecture.py])

### Summary
CVE-2022-0435 is a stack-overflow bug class caused by processing an attacker-controlled, unbounded nested/list structure without a depth limit before recursively walking it. The Chia codebase contains a directly analogous pattern in `PuzzleWithRestrictions.from_memo()`, which recursively reconstructs `MofN` custody structures from an on-chain memo with no recursion-depth bound, and this code path is wired into production wallet code (plotnft/custody wallet drivers), not just tests.

### Finding Description
`PuzzleWithRestrictions.from_memo()` is the "on-chain/exported synchronization contract" used to reconstruct a custody puzzle tree (used by the PlotNFT v2 / custody architecture) from a `Program` memo attached to a coin: [1](#0-0) 

```
@classmethod
def from_memo(cls, memo: Program) -> PuzzleWithRestrictions:
    ...
    further_branching = further_branching_prog != Program.to(None)
    if further_branching:
        m_of_n_hint = MofNHint.from_program(puzzle_hint_prog)
        puzzle: MIPSComponent = MofN(
            m=m_of_n_hint.m, members=[PuzzleWithRestrictions.from_memo(memo) for memo in m_of_n_hint.member_memos]
        )
    ...
```

When the memo encodes a `MofN` node, `from_memo` recurses into `PuzzleWithRestrictions.from_memo(memo)` once per member, and each member's memo can itself encode another `MofN` node, recursing arbitrarily deep. There is no maximum nesting-depth check anywhere in `from_memo`, `MofNHint.from_program`, or the caller code. This mirrors the docstring-acknowledged concern elsewhere in the codebase that CLVM-derived Python structures can be "deeply nested" enough to "blow out the python stack" — a concern explicitly called out for `sha256_treehash`, which was deliberately made iterative for this reason: [2](#0-1) . No equivalent iterative/depth-limited design was applied to `PuzzleWithRestrictions.from_memo()`.

The memo Program is attacker-controlled: it is the CLVM memo attached to a coin (per `PuzzleWithRestrictions.memo()`/`from_memo()` round-trip contract), and this custody-architecture code is used by production PlotNFT wallet drivers, not only test code, as shown by references in `chia/pools/plotnft_drivers.py`, `chia/wallet/plotnft_wallet/plotnft_wallet.py`, and `chia/wallet/puzzles/custody/restriction_utilities.py`.

### Impact Explanation
Any party that creates a coin (e.g., in a spend bundle sent to the mempool, or one crafted for a wallet to observe) can attach an arbitrarily deep, nested `MofN` memo. When the wallet (or any consumer that calls `PuzzleWithRestrictions.from_memo` to sync/recognize the custody puzzle for that coin) parses the memo, `from_memo`'s recursion will run until it hits Python's interpreter recursion limit, raising an uncaught `RecursionError` (unless caught generically) and crashing/halting the wallet process that is handling the coin/transaction. This matches the "Validate" criterion of a spend-triggered transaction-processing halt — an unprivileged coin creator can remotely trigger a crash in another party's wallet process simply by getting them to observe/sync a coin carrying the malicious memo, with no signature or special privilege required.

### Likelihood Explanation
Likelihood is high for any deployment that uses the PlotNFT v2 / custody-architecture (CHIP-0043) memo format, since:
- The memo format and depth are entirely attacker-controlled.
- No size/depth validation exists before recursion in `from_memo`.
- The trigger only requires crafting a coin with a deeply nested `MofN` memo hint and getting it observed by a wallet performing custody-puzzle recognition (a normal wallet-sync operation), not a privileged or complex interaction.

### Recommendation
Rewrite `PuzzleWithRestrictions.from_memo()` (and the related `MofN`/`fill_in_unknown_puzzles`/`unknown_puzzles` recursive helpers) to use an iterative, explicit-stack traversal instead of Python recursion, following the same pattern already used in `sha256_treehash()`. Additionally, enforce an explicit maximum nesting depth (and/or maximum total node count) for `MofN` member reconstruction and reject memos that exceed it before any recursive/iterative processing begins.

### Proof of Concept
Construct a memo `Program` that encodes nested `MofN` hints recursively N times deep (each level being `[nonce, [], 1, MofNHint(m=1, member_memos=[deeper_memo])]` prefixed by the `"CHIP-0043"` namespace), attach it as a coin's memo, and call `PuzzleWithRestrictions.from_memo(memo)` on it (as done in wallet sync/custody-recognition code paths). For sufficiently large N (a few thousand, well within CLVM program size limits), this raises `RecursionError` and crashes the calling process, as directly demonstrated by the legitimate round-trip test exercising the exact same recursive code path: [3](#0-2)

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

**File:** chia/types/blockchain_format/tree_hash.py (L1-7)
```python
"""
This is an implementation of `sha256_treehash`, used to calculate
puzzle hashes in clvm.

This implementation goes to great pains to be non-recursive so we don't
have to worry about blowing out the python stack.
"""
```

**File:** chia/_tests/clvm/test_custody_architecture.py (L119-131)
```python
def test_back_and_forth_hint_parsing(restrictions: list[Restriction[MemberOrDPuz]], puzzle: MIPSComponent) -> None:
    """
    This tests that a PuzzleWithRestrictions can be exported to a clvm program to be reimported from.

    This is necessary functionality to sync an unknown inner puzzle from on chain.
    """
    cwr = PuzzleWithRestrictions(
        nonce=0,
        restrictions=restrictions,
        puzzle=puzzle,
    )

    assert PuzzleWithRestrictions.from_memo(cwr.memo()) == cwr
```
