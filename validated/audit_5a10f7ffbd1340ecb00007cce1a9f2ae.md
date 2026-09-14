### Title
Unbounded recursive memo parsing in `PuzzleWithRestrictions.from_memo()` allows a crafted coin memo to crash wallet sync via Python stack exhaustion - ([File: chia/wallet/puzzles/custody/custody_architecture.py])

### Summary
`PuzzleWithRestrictions.from_memo()` recursively parses a CLVM memo structure without any depth bound. When the memo encodes an `MofN` node, the method recurses once per member, and each member's memo can itself encode another `MofN` node, so the recursion depth is controlled entirely by the attacker-supplied memo. This is called on data taken directly from a `CREATE_COIN` condition's memo produced by a coin spend that the wallet is asked to interpret (e.g. `plotnft_drivers.py` calls `PuzzleWithRestrictions.from_memo(singleton_create_coin.memo_blob.rest())` while reconstructing PlotNFT/custody state from an observed coin spend).

### Finding Description
`from_memo()` is a straightforward recursive-descent parser over a `Program` (CLVM s-expression) tree: [1](#0-0) 

For the `MofN` branch it builds `member_memos` from the parsed hint and calls itself once per member:

```
puzzle: MIPSComponent = MofN(
    m=m_of_n_hint.m, members=[PuzzleWithRestrictions.from_memo(memo) for memo in m_of_n_hint.member_memos]
)
```

There is no limit on `m_of_n_hint.member_memos` length or on how deeply nested a member's own memo can be (each member memo can again encode an `MofN` node). Because CLVM list/cons construction is very cheap in terms of CLVM cost relative to the number of nested atoms it produces, an attacker who controls the puzzle/solution of a spend (e.g. a pool-adjacent singleton spend or any spend whose `CREATE_COIN` memo is later interpreted by `PuzzleWithRestrictions.from_memo`) can cheaply build an arbitrarily deep chain of nested `MofN` memos within normal mempool/block cost limits.

This memo is consumed by wallet-side logic that reconstructs custody/PlotNFT state from an observed coin spend, without first bounding recursion depth: [2](#0-1) 

Because Python has a finite call-stack (`sys.getrecursionlimit()`, no `sys.setrecursionlimit` override exists anywhere in this repository), a sufficiently deep memo triggers a `RecursionError` inside `from_memo()`. This directly parallels the GPP CVE-2018-17076 bug class: a crafted input drives an unbounded recursive parser past available stack space, producing a fault (here, a Python-level stack/recursion exhaustion) instead of a controlled parse failure.

### Impact Explanation
`RecursionError` is a `BaseException`-adjacent condition that is easy to miss with a narrow `except ValueError`/`except Exception` handler, and even where caught, repeated triggering of deep-recursion parsing during automatic wallet sync (which processes every incoming coin state without user interaction) can degrade or halt wallet transaction-processing for any wallet that observes such a coin (a pool participant tracking their PlotNFT singleton lineage, or a wallet reconstructing custody-architecture state from chain data). This matches the "spend-triggered transaction-processing halt" impact category: an unprivileged party can construct a coin/spend whose memo, once observed and parsed by a wallet performing routine sync, causes a crash or unhandled exception in the wallet process handling that logic path.

### Likelihood Explanation
Likelihood is moderate: the attacker needs only to construct a coin (e.g. spend of a singleton with a crafted `CREATE_COIN` memo, or any code path that will later call `PuzzleWithRestrictions.from_memo` on attacker-influenced data) that is then observed/synced by a victim wallet performing `PlotNFT`/custody-state reconstruction. No signature or special privilege is required beyond submitting or having a target wallet observe a normal, validly-costed spend bundle — this is reachable purely from crafting puzzle/solution/memo content, which any spend-bundle submitter controls.

### Recommendation
Add an explicit maximum recursion/nesting depth check in `PuzzleWithRestrictions.from_memo()` (and any other memo/PuzzleInfo recursive parsers reachable from untrusted chain data) before recursing into `MofN` members, raising a normal `ValueError` instead of allowing Python's native recursion limit to be hit. Alternatively, convert the parser to an iterative work-stack implementation (as already done for `sha256_treehash` in `chia/types/blockchain_format/tree_hash.py`) to avoid dependence on Python's call stack entirely, and ensure calling sites (`plotnft_drivers.py`, custody sync code) catch `RecursionError` defensively as a parse failure rather than allowing it to propagate as an unhandled fault.

### Proof of Concept
1. Construct a memo `Program` value of the form used by `MofNHint.to_program()`: `[m, member_memos]` where each entry in `member_memos` is itself a valid `PuzzleWithRestrictions.memo()`-shaped tuple whose `further_branching` flag is set and whose own `member_memos` list contains another such nested `MofN` memo.
2. Nest this construction N times (e.g. N = 5,000), which is cheap to build in CLVM (a `CREATE_COIN` condition can carry this as its memo with modest additional CLVM cost from `c`/`cons` operations, well under `MAX_BLOCK_COST_CLVM`).
3. Include this `CREATE_COIN` condition (with the deep memo) in a normal spend of a singleton/plotnft-style coin, and submit it as an ordinary spend bundle (no special privilege needed).
4. When a wallet observes the resulting coin creation and calls `PlotNFT.get_next_from_coin_spend()` → `PuzzleWithRestrictions.from_memo(singleton_create_coin.memo_blob.rest())` (`chia/pools/plotnft_drivers.py:481`), the recursive parser recurses N times, exceeding Python's default recursion limit and raising `RecursionError`, disrupting the wallet's state-reconstruction/sync flow for that coin.

Note: I was not able to fully trace every call path that invokes `PlotNFT.get_next_from_coin_spend()` (e.g. exact wallet-sync entry points in `chia/wallet/wallet_state_manager.py`) within the available search iterations; this should be verified in a live session to confirm the exact automatic-sync trigger and whether any surrounding exception handling already mitigates the crash before treating this as a fully confirmed halt condition.

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

**File:** chia/pools/plotnft_drivers.py (L477-486)
```python
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
