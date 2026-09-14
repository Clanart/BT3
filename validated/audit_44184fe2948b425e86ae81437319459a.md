## Analog Found

### Title
Unbounded recursion in `PuzzleWithRestrictions.from_memo` via crafted nested M-of-N coin memo causes wallet DoS - (File: `chia/wallet/puzzles/custody/custody_architecture.py`)

### Summary
The Poppler bug class is a crafted, deeply-nested input structure driving unbounded recursive parsing until the process stack is exhausted. The Chia analog is `PuzzleWithRestrictions.from_memo()`, which recursively parses an on-chain coin memo describing a custody/PlotNFT M-of-N structure, with no depth limit on nesting.

### Finding Description
`PuzzleWithRestrictions.from_memo()` decodes a `Program` memo attached to a created coin. When the memo hints at an `MofN` puzzle, it recurses into itself once per member, and that recursion is itself unbounded because each member can again be an `MofN`: [1](#0-0) 

Specifically the recursive call: [2](#0-1) 

This memo is fully attacker-controlled: `CREATE_COIN` memos are not validated by consensus or by the custody puzzles themselves — the puzzle logic only enforces spend authorization, not memo contents. Any party able to author a valid spend of a shared/multi-party custody singleton (e.g., one signer in an M-of-N PlotNFT/custody arrangement, or a malicious pool/co-owner) can set an arbitrarily deeply nested `MofNHint` memo on the coin it creates.

The reachable trigger point is `PlotNFT.get_next_from_coin_spend()`, which is the code path a wallet uses to sync/track its own PlotNFT singleton lineage from on-chain coin spends: [3](#0-2) 

When the fast-path checks (previous puzzle reuse, exit/rejoin puzzle hash matches) don't match, this code falls back to `PuzzleWithRestrictions.from_memo(singleton_create_coin.memo_blob.rest())`, feeding the attacker-controlled memo directly into the unbounded recursive parser.

### Impact Explanation
A malicious co-owner of a shared-custody PlotNFT (or any singleton using this custody framework) can, via a validly authorized spend, create a coin whose memo encodes deeply nested `MofN` structures. When any wallet (including the honest co-owner's) later syncs/parses that coin's lineage through `get_next_from_coin_spend`/`from_memo`, Python's call stack recursion is driven arbitrarily deep, raising `RecursionError` (or crashing the interpreter) and halting wallet transaction/state processing for that PlotNFT — a spend-triggered processing halt, matching the CVSS 6.5 Medium DoS class of the reference issue.

### Likelihood Explanation
Constructing deeply nested memo data is cheap (linear in nesting depth, no CLVM execution cost gate on memo bytes themselves since memos aren't run), and only requires an authorized spend of a shared-custody singleton, which is a normal, permitted action for any qualifying M-of-N member. No special privilege beyond legitimate co-ownership of the singleton (a supported use case of this exact custody framework) is required.

### Recommendation
Add an explicit recursion/nesting depth limit (or convert to an iterative parser, mirroring the pattern already used for `sha256_treehash` specifically to avoid Python recursion limits) in `PuzzleWithRestrictions.from_memo()` and reject memos exceeding the limit before parsing them.

### Proof of Concept
1. Construct a `MofNHint` memo program with `member_memos` containing nested `MofNHint` structures many thousands of levels deep (linear-size CLVM program, e.g. repeated `(1 [[m, [nested_hint]]])` wrapping).
2. As one signer of an M-of-N (or 1-of-N) PlotNFT/custody arrangement, spend the singleton with a delegated puzzle whose `CREATE_COIN` sets this crafted program as the coin's memo.
3. Once the spend is confirmed, any wallet calling `PlotNFT.get_next_from_coin_spend()` (or otherwise calling `PuzzleWithRestrictions.from_memo()`) on that coin's memo triggers unbounded recursion in `from_memo`/`MofNHint.from_program`, raising `RecursionError` and halting PlotNFT sync processing for that wallet.

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

**File:** chia/pools/plotnft_drivers.py (L477-483)
```python
        if plotnft_puzzle is None:
            if singleton_create_coin.memo_blob is None:
                raise GetNextPlotNFTError("Invalid memoization of PlotNFT")
            try:
                unknown_inner_puzzle = PuzzleWithRestrictions.from_memo(singleton_create_coin.memo_blob.rest())
            except ValueError:
                raise GetNextPlotNFTError("Invalid memoization of PlotNFT")
```
