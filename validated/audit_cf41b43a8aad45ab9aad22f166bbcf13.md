### Title
Unbounded recursive `MofN` memo parsing in `PuzzleWithRestrictions.from_memo()` allows a crafted on-chain spend to crash wallet clients via stack overflow - ([File: chia/wallet/puzzles/custody/custody_architecture.py])

### Summary
`PuzzleWithRestrictions.from_memo()` recursively reconstructs nested `MofN` custody structures from an on-chain `CREATE_COIN` memo with no depth limit, directly analogous to the Meridian advisory's HIGH-severity findings (missing `DefaultMaxDepth`/collection caps on public parsing entry points, CWE-674 uncontrolled recursion). A single attacker-controlled coin spend can embed a deeply nested `MofN` memo hint that, when parsed by any wallet client following this custody path (e.g. plotNFT/pool-participant sync), triggers unbounded Python recursion and a crash/hang of the wallet's coin-processing pipeline.

### Finding Description
`PuzzleWithRestrictions.from_memo()` parses the `spec_namespace`-tagged memo Program and, when `further_branching` is true, recurses into itself once per member of an `MofN` hint with no depth or member-count bound: [1](#0-0) 

Each recursive call trusts fully attacker-supplied `Program` data (`memo`) taken straight from chain state — there is no cap analogous to Meridian's `DefaultMaxDepth`/`DefaultMaxCollectionItems`, and no CLVM execution (hence no cost-based backstop) gates the number of recursive Python calls performed while unpacking the memo tree.

The only production call site currently reachable is `PlotNFT.get_next_from_coin_spend()`, which feeds the `memo_blob` taken directly from a `CREATE_COIN` condition parsed out of an on-chain, attacker-controlled inner-puzzle solution into `from_memo()`: [2](#0-1) 

Because the memo is arbitrary CLVM data controlled by whoever creates the coin (any pool participant / plotNFT owner performing a normal spend), an attacker can construct a `CREATE_COIN` memo encoding a `MofNHint` whose `member_memos` recursively nest thousands of levels deep. Building such a structure inside a spend bundle is cheap in CLVM cost terms (it is just nested list/cons data placed in a memo, not something that must be executed), so it easily fits well under `max_tx_clvm_cost`/`MAX_BLOCK_COST_CLVM`.

### Impact Explanation
Any wallet or service that calls `PlotNFTPuzzle`/`PlotNFT.get_next_from_coin_spend()` (or any future consumer of the shared `custody_architecture.PuzzleWithRestrictions.from_memo()` API) to sync a pool/plotNFT coin whose `CREATE_COIN` memo contains such a maliciously deep `MofN` hint will exceed Python's default recursion limit and raise `RecursionError`, aborting whatever coin-processing/sync task invoked it. This is a spend-triggered transaction/coin-processing halt reachable purely by submitting a normal, validly-accepted spend bundle containing crafted memo data — no malicious peer, node, or privileged access required.

### Likelihood Explanation
Likelihood is moderate-to-high for any deployment that exercises the custody/plotNFT memo-sync path: constructing a deeply nested memo requires no special capability beyond spending an owned coin with an attacker-chosen `CREATE_COIN` memo, and the resulting spend is a normal, consensus-valid transaction (memo depth/nesting is not consensus-checked). The blast radius is limited to wallets/services that actually parse `PuzzleWithRestrictions` memos (currently plotNFT-following code), not the full-node consensus path.

### Recommendation
Add an explicit depth/branching-factor cap to `PuzzleWithRestrictions.from_memo()` (and its `MofNHint`/`RestrictionHint` traversal) analogous to Meridian's `DefaultMaxDepth`, rejecting memos that exceed a documented safe recursion depth before recursing, and/or convert the recursive walk to an iterative one (as already done for `sha256_treehash` for the same reason, see `chia/types/blockchain_format/tree_hash.py`) so malformed/hostile memo data fails cleanly with a `ValueError` instead of crashing the process.

### Proof of Concept
1. Construct a `PuzzleWithRestrictions` whose `puzzle` is an `MofN` containing one member, whose own `puzzle` is again an `MofN` with one member, repeated N (e.g. 5,000+) times, terminating in a leaf `UnknownMember`.
2. Serialize this via `.memo()` to obtain the nested `Program` memo blob (cheap — just nested cons cells, no CLVM execution required).
3. Include this memo as the memo list of a `CREATE_COIN` condition produced by a plotNFT/custody-style inner puzzle spend and broadcast the spend bundle; it is a normal, validly-accepted spend.
4. When a wallet later calls `PlotNFT.get_next_from_coin_spend()` on this coin spend, it invokes `PuzzleWithRestrictions.from_memo(singleton_create_coin.memo_blob.rest())`, which recurses N times and raises `RecursionError`, crashing/halting the wallet's coin-processing task.

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
