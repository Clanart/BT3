## Title
Unbounded Recursive Memo Parsing in `PuzzleWithRestrictions.from_memo()` Enables Wallet-Crash Denial of Service — ([File: chia/wallet/puzzles/custody/custody_architecture.py])

### Summary
`PuzzleWithRestrictions.from_memo()` recursively reconstructs a custody puzzle tree from an on-chain coin's memo `Program` with no depth or size bound. This mirrors the MongoDB bug class: attacker-controlled structured input (there JSON, here a CLVM `Program` memo) is parsed with naive recursion, letting a hostile input crash the parser via stack exhaustion.

### Finding Description
`from_memo()` is a classmethod that parses a `Program` memo into a `PuzzleWithRestrictions`/`MofN` tree. For an `MofN` node it recurses once per member: [1](#0-0) 

Specifically, `further_branching` triggers:
```
puzzle = MofN(m=m_of_n_hint.m, members=[PuzzleWithRestrictions.from_memo(memo) for memo in m_of_n_hint.member_memos])
```
Each `member_memo` is itself an attacker-supplied CLVM sub-program with no restriction on nesting depth — an adversary can construct a coin whose `CREATE_COIN` memo encodes an `MofN` structure nested to an arbitrary depth (e.g., thousands of levels), and every level triggers another Python-level recursive call into `from_memo()`.

This memo is consumed by `PlotNFT.get_next_from_coin_spend()` when reconstructing PlotNFT/pool state from an observed singleton coin spend: [2](#0-1) 

That code path is driven by ordinary wallet sync of pool/plotNFT singleton coins — data that originates from the chain (or from a peer serving wallet sync responses) and is not otherwise validated before this parse. No `sys.setrecursionlimit` protection or depth cap exists anywhere in this parsing path (confirmed by an empty search for recursion-limit handling in this area of the codebase), unlike other parts of the codebase that deliberately avoid recursion for this exact class of risk, e.g. `sha256_treehash()` in `chia/types/blockchain_format/tree_hash.py` is explicitly implemented iteratively "to avoid Python recursion limits on deeply nested CLVM": [3](#0-2) 

### Impact Explanation
A sufficiently deep memo triggers Python's recursion limit (`RecursionError`) or, depending on CPython/interpreter internals, a native stack overflow that can crash the wallet process outright rather than raise a catchable exception. Because this is invoked while syncing/observing chain data for a plotNFT/pool singleton, an attacker who can get a crafted coin with a malicious memo observed by a victim's wallet (e.g., by spending a pool-related singleton, or by controlling data that flows into `get_next_from_coin_spend`) can remotely trigger a wallet crash/denial of service without needing prior authentication to the wallet RPC — analogous to the pre-auth MongoDB stack-overflow-via-recursion bug class. This is a availability/DoS impact against wallet sync, not a fund-theft primitive.

### Likelihood Explanation
Medium-High. Constructing an arbitrarily deep `MofN` memo structure is inexpensive for an attacker (pure CLVM/Program construction, no signature or fee requirements beyond a normal spend to create the observed coin/memo). The only constraint is getting the wallet to observe/process the coin spend through the plotNFT/pool sync path, which is a normal wallet behavior for any tracked pool/plotNFT singleton lineage.

### Recommendation
- Convert `PuzzleWithRestrictions.from_memo()` (and the corresponding `MofNHint`/`RestrictionHint` recursive parsing) to an iterative, stack-based traversal similar to `sha256_treehash()`, or enforce an explicit maximum nesting depth/size before recursing.
- Add an upper bound on the number of `MofN` members and nesting depth accepted from untrusted memos, rejecting malformed/oversized structures early with a normal exception rather than allowing unbounded recursion.
- Wrap recursive memo parsing in `get_next_from_coin_spend()` with defensive limits so malformed on-chain data degrades to a handled parse error instead of crashing the process.

### Proof of Concept
Conceptually (cannot be executed here, but the shape is deterministic from the code):
1. Build a nested `Program` memo: start with a leaf `MemberHint` memo, then repeatedly wrap it in an `MofNHint(m=1, member_memos=[prev])` `to_program()` call thousands of times, each layer marked as `further_branching=True` per the `PuzzleWithRestrictions.memo()` format (`chia/wallet/puzzles/custody/custody_architecture.py:236-269`).
2. Encode this as the memo (`memo_blob.rest()`) of a `CREATE_COIN` condition in a singleton coin spend that a victim wallet's `PlotNFT.get_next_from_coin_spend()` will process during pool/plotNFT sync (`chia/pools/plotnft_drivers.py:477-486`).
3. When the victim wallet calls `PuzzleWithRestrictions.from_memo()` on this memo, it recurses one Python stack frame per nesting layer, exhausting the recursion limit / stack and crashing or hanging the wallet process (`chia/wallet/puzzles/custody/custody_architecture.py:271-296`).

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

**File:** chia/types/blockchain_format/tree_hash.py (L1-7)
```python
"""
This is an implementation of `sha256_treehash`, used to calculate
puzzle hashes in clvm.

This implementation goes to great pains to be non-recursive so we don't
have to worry about blowing out the python stack.
"""
```
