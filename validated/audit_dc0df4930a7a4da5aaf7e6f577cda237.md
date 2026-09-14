## Analysis

The CVE describes a stack exhaustion (unbounded recursive descent) in a metadata parser (`printIFDStructure`) that processes untrusted nested/tree-structured input without a depth limit, causing a crash. The chia-blockchain codebase is explicitly aware of this exact bug class: `chia/types/blockchain_format/tree_hash.py` states its `sha256_treehash()` "goes to great pains to be non-recursive so we don't have to worry about blowing out the python stack" [1](#0-0) , and is implemented with an explicit stack/op-stack loop [2](#0-1) .

However, `PuzzleWithRestrictions.from_memo()` in the custody/MIPS puzzle framework does **not** follow this discipline — it recurses directly over attacker-suppliable, on-chain memo data with no depth limit.

### Title
Unbounded recursion in `PuzzleWithRestrictions.from_memo()` allows attacker-controlled memo data to exhaust the Python call stack - (File: `chia/wallet/puzzles/custody/custody_architecture.py`)

### Summary
`PuzzleWithRestrictions.from_memo()` reconstructs a custody/MIPS puzzle tree (used by plotnft and generic custody-architecture puzzles) from the `memo` blob attached to a `CREATE_COIN` condition. When the puzzle hint indicates an `MofN` node, the function recurses once per member, and each member's memo can itself encode another `MofN` node, allowing arbitrarily deep nesting with no recursion-depth check [3](#0-2) .

### Finding Description
`from_memo()` parses the namespace/nonce/restriction-hints/puzzle-hint tuple from an arbitrary `Program`. If `further_branching_prog` is truthy, it decodes an `MofNHint` and recurses:

```python
puzzle: MIPSComponent = MofN(
    m=m_of_n_hint.m, members=[PuzzleWithRestrictions.from_memo(memo) for memo in m_of_n_hint.member_memos]
)
``` [4](#0-3) 

Neither `from_memo()` nor `MofNHint.from_program()` [5](#0-4)  bounds the recursion depth. The `memo` data originates from the `memo_blob` attached to any `CREATE_COIN` condition, which is fully attacker-controlled CLVM data emitted by any spend bundle — it is not the puzzle reveal itself and is not validated by consensus beyond being a list of atoms/CLVM structure.

This function is invoked from wallet/plotnft sync code when a `CREATE_COIN`'s memo is treated as a custody-architecture memo, e.g. `PlotNFT.get_next_from_coin_spend()`:

```python
unknown_inner_puzzle = PuzzleWithRestrictions.from_memo(singleton_create_coin.memo_blob.rest())
``` [6](#0-5) 

An attacker can create a coin (e.g., disguised as, or actually being, a singleton output) whose `CREATE_COIN` memo encodes a deeply nested `MofN` hint chain. When a wallet or plotnft-sync client that walks singleton lineage encounters this coin and calls `from_memo()` on the memo, the unbounded Python recursion will raise `RecursionError` (or, on some platforms/thread stacks, crash the interpreter/thread), rather than a graceful decode failure.

This mirrors the CVE-2020-18898 bug class exactly: a tree/structure parser recursing directly over attacker-controlled nested input with no depth cap, unlike the codebase's own iterative `sha256_treehash()` which was deliberately hardened against this.

### Impact Explanation
This is a spend-triggered, transaction-processing/wallet-sync halt (denial of service) reachable by any unprivileged actor who can get a `CREATE_COIN` with a crafted memo observed by a victim wallet or plotnft-sync routine (e.g. by directly spending to the victim's known plotnft/custody-architecture puzzle hash, or via a bundle the victim's node will process). It does not directly enable coin theft or supply inflation, but it can crash or hang a wallet or full-node worker process handling that data, denying service to the affected component.

### Likelihood Explanation
Likelihood is moderate: the attacker needs only to craft a `CREATE_COIN` output containing a deeply nested memo Program and have it processed by code paths that call `PuzzleWithRestrictions.from_memo()` (plotnft/custody puzzle-recognition code). No signature or special privilege is needed to submit such a spend to the mempool. Because Python's default recursion limit (~1000) is far lower than practically constructible CLVM-encoded nesting depth, achieving the crash condition is straightforward.

### Recommendation
Rewrite `PuzzleWithRestrictions.from_memo()` (and `MofNHint.from_program()`'s consumer) to use an explicit iterative worklist/stack, mirroring the pattern already used in `sha256_treehash()`, or add and enforce a maximum nesting-depth limit before recursing, raising a clean `ValueError` when exceeded instead of allowing unbounded Python recursion.

### Proof of Concept
Conceptually:
1. Construct a `Program` memo where `further_branching_prog` is non-nil and `MofNHint.member_memos` contains another `PuzzleWithRestrictions`-shaped memo that is itself an `MofN` hint, nested N times (N ~ 2000–5000, comfortably exceeding Python's default recursion limit while remaining well within CLVM cost/size limits for a `CREATE_COIN` memo).
2. Attach this memo to a `CREATE_COIN` condition in a spend bundle sent to, or spent from, a coin the victim's plotnft/custody-sync logic will inspect (e.g., matching the singleton pattern checked in `PlotNFTPuzzle.get_next_from_coin_spend`).
3. When the victim calls `PuzzleWithRestrictions.from_memo(memo)` on this data, the recursive call chain in `from_memo` → `MofN` → `from_memo` ... exceeds the Python recursion limit and raises `RecursionError`, crashing or hanging the wallet/plotnft-sync task processing it.

### Citations

**File:** chia/types/blockchain_format/tree_hash.py (L1-7)
```python
"""
This is an implementation of `sha256_treehash`, used to calculate
puzzle hashes in clvm.

This implementation goes to great pains to be non-recursive so we don't
have to worry about blowing out the python stack.
"""
```

**File:** chia/types/blockchain_format/tree_hash.py (L65-72)
```python
    sexp_stack: ValueStackType = [sexp]
    op_stack: list[Op] = [handle_sexp]
    while len(op_stack) > 0:
        op = op_stack.pop()
        op(sexp_stack, op_stack, precalculated)
    # just trusting it is right, otherwise we get some error, probably
    result: bytes = sexp_stack[0]  # type: ignore[assignment]
    return bytes32(result)
```

**File:** chia/wallet/puzzles/custody/custody_architecture.py (L155-161)
```python
    @classmethod
    def from_program(cls, prog: Program) -> MofNHint:
        m, member_memos = prog.as_iter()
        return MofNHint(
            m=m.as_int(),
            member_memos=list(member_memos.as_iter()),
        )
```

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

**File:** chia/pools/plotnft_drivers.py (L480-481)
```python
            try:
                unknown_inner_puzzle = PuzzleWithRestrictions.from_memo(singleton_create_coin.memo_blob.rest())
```
