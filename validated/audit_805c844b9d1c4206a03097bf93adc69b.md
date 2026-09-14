### Title
Unbounded recursive parsing of `PuzzleWithRestrictions.from_memo()` allows stack overflow / crash when a wallet syncs an attacker-crafted coin memo - (File: chia/wallet/puzzles/custody/custody_architecture.py)

### Summary
`PuzzleWithRestrictions.from_memo()` recursively parses a CLVM `Program` memo that is fully attacker-controlled (it is derived from the `memo_blob` attached to a `CREATE_COIN` condition in an arbitrary, unprivileged spend). For nested `MofN` custody structures the function recurses once per nesting level with no depth limit, mirroring the `alloy-json-abi` `JsonAbi::parse` stack-overflow bug class: deeply-nested, attacker-supplied structured data drives unbounded Python-level recursion in a parser, which can exhaust the call stack and crash the consuming process.

### Finding Description
`PuzzleWithRestrictions.from_memo()` parses the on-chain custody memo format (`CHIP-0043`). When the memo encodes an `MofN` puzzle (`further_branching_prog != 0`), it recursively calls itself once per member: [1](#0-0) 

Each `MofNHint.from_program()` reads an arbitrary-length `member_memos` list from the same attacker-supplied program: [2](#0-1) 

There is no recursion-depth or nesting-depth check anywhere in this call chain, unlike other CLVM-facing code in the same module tree that is intentionally implemented iteratively to avoid exactly this class of bug (e.g. `sha256_treehash`, explicitly documented as "non-recursive so we don't have to worry about blowing out the python stack"): [3](#0-2) 

The memo itself originates from a `CREATE_COIN` condition's `memo_blob`, which is fully controlled by whoever creates the spend bundle (there is no consensus rule restricting memo *structure*, only overall transaction cost/size). A concrete production consumer of this exact recursive parser is the PlotNFT sync path, which calls `from_memo()` directly on an untrusted coin's memo when it cannot otherwise recognize the inner puzzle: [4](#0-3) 

Any wallet (or other Devin/full-node component) that walks a chain of coins and calls `PuzzleWithRestrictions.from_memo()`/`PlotNFTPuzzle.get_next_from_coin_spend()` on a coin created by an unprivileged party is exposed. The custody test suite even documents this as intended behavior ("`from_memo()` must reconstruct unknown members/restrictions, including recursive `MofN`") without mentioning a depth cap.

### Impact Explanation
A malicious spend bundle submitter can create a coin whose `CREATE_COIN` memo encodes a deeply nested `MofN` custody structure (e.g., thousands of levels of `m=1,n=1` "further branching"). Because parsing is a pure Python recursive function with no depth limit, any wallet client that later calls `from_memo()` on this memo (for example, while syncing PlotNFT transitions or generic custody-puzzle recognition) will recurse until Python's recursion limit is hit, raising `RecursionError`/causing a stack overflow and crashing or hanging the wallet's coin-processing loop. This is a spend-triggered denial-of-service against wallet clients that attempt to interpret arbitrary on-chain coins/memos, consistent with "Uncontrolled Resource Consumption" (CWE-674) from the reference advisory.

### Likelihood Explanation
Likelihood is moderate-to-high for any wallet or tool that proactively parses custody/PlotNFT memos for unknown coins: the attacker only needs to submit one ordinary, mempool-acceptable spend bundle creating a coin with a maliciously nested memo; no special privileges, signatures over victim funds, or protocol violations are required. The memo depth achievable is bounded only by the per-spend CLVM cost/size budget, which is generous enough to encode many recursion levels well beyond Python's default recursion limit (~1000).

### Recommendation
Rewrite `PuzzleWithRestrictions.from_memo()` (and `MofNHint.from_program()`) to use an explicit iterative worklist/stack instead of Python recursion, mirroring the pattern already used in `sha256_treehash`. Additionally, enforce an explicit maximum nesting depth for memo parsing and reject memos that exceed it, and consider bounding member-list length independent of overall byte size to reduce pathological nesting-to-size ratios.

### Proof of Concept
1. Construct a spend bundle whose `CREATE_COIN` condition carries a memo built as:
   - innermost: `PuzzleWithRestrictions(nonce=0, restrictions=[], puzzle=UnknownMember(...))`
   - wrap N times: `PuzzleWithRestrictions(nonce=k, restrictions=[], puzzle=MofN(m=1, members=[<previous>]))`, each producing `.memo()` output via `chia/wallet/puzzles/custody/custody_architecture.py` lines 236-269.
2. Submit this spend bundle normally; it is a valid, unprivileged transaction creating one coin with the crafted memo (size stays within normal cost limits for N in the thousands, since each wrapping level costs only a few bytes).
3. Have any wallet client call `PuzzleWithRestrictions.from_memo(memo)` on the resulting coin's memo (directly, or transitively through `PlotNFTPuzzle.get_next_from_coin_spend()` in `chia/pools/plotnft_drivers.py` lines 476-483 when it cannot resolve the inner puzzle by other means).
4. Observe `RecursionError` / native stack overflow in the wallet process while parsing the memo, crashing or hanging the client.

### Citations

**File:** chia/wallet/puzzles/custody/custody_architecture.py (L147-161)
```python
@dataclass(kw_only=True, frozen=True)
class MofNHint:
    m: int
    member_memos: list[Program]

    def to_program(self) -> Program:
        return Program.to([self.m, self.member_memos])

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

**File:** chia/types/blockchain_format/tree_hash.py (L1-7)
```python
"""
This is an implementation of `sha256_treehash`, used to calculate
puzzle hashes in clvm.

This implementation goes to great pains to be non-recursive so we don't
have to worry about blowing out the python stack.
"""
```

**File:** chia/pools/plotnft_drivers.py (L476-483)
```python
        # Finally, we try to look for the memos
        if plotnft_puzzle is None:
            if singleton_create_coin.memo_blob is None:
                raise GetNextPlotNFTError("Invalid memoization of PlotNFT")
            try:
                unknown_inner_puzzle = PuzzleWithRestrictions.from_memo(singleton_create_coin.memo_blob.rest())
            except ValueError:
                raise GetNextPlotNFTError("Invalid memoization of PlotNFT")
```
