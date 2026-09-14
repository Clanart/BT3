### Title
Unbounded recursive parsing of on-chain `MofN` memos in `PuzzleWithRestrictions.from_memo()` enables a spend-triggered wallet DoS - (File: `chia/wallet/puzzles/custody/custody_architecture.py`)

### Summary
`PuzzleWithRestrictions.from_memo()` recursively reconstructs a custody tree from an untrusted CLVM `Program` memo taken directly off-chain, with no bound on nesting depth. Any coin spend that creates a coin with a maliciously crafted, deeply nested `MofN` memo (e.g., chained "1-of-1" `MofN` wrappers) will drive this recursive parser into unbounded recursion when a wallet later processes that coin (e.g., PlotNFT sync), causing a Python recursion/stack exhaustion crash — the same bug class as the Multer/`append-field` advisory (CWE-400 via unbounded recursive parsing of attacker-controlled nested structure), just in Chia's custody-memo parser instead of a bracket-notation field-name parser.

### Finding Description
`PuzzleWithRestrictions.from_memo()` parses a `Program` memo and, whenever the encoded structure indicates `further_branching` (i.e. the puzzle is an `MofN`), recursively calls itself once per member: [1](#0-0) 

Specifically:
```
if further_branching:
    m_of_n_hint = MofNHint.from_program(puzzle_hint_prog)
    puzzle: MIPSComponent = MofN(
        m=m_of_n_hint.m, members=[PuzzleWithRestrictions.from_memo(memo) for memo in m_of_n_hint.member_memos]
    )
```
There is no depth counter, no recursion-depth cap, and no size/structure limit on `member_memos` before recursing — each member's memo can itself encode another `MofN` with its own members, allowing an attacker to nest arbitrarily deep (e.g., a chain of `MofN(m=1, members=[single nested MofN...])`).

This memo is untrusted, attacker-controlled data reachable through a normal spend: `CreateCoin` conditions carry an optional `memo_blob`, and any spend bundle submitter can set this to arbitrary bytes when creating a coin (`chia/wallet/conditions.py`). This memo is later fed straight into `PuzzleWithRestrictions.from_memo()` when a wallet resyncs a PlotNFT/pool singleton's coin history: [2](#0-1) 
```
if plotnft_puzzle is None:
    if singleton_create_coin.memo_blob is None:
        raise GetNextPlotNFTError("Invalid memoization of PlotNFT")
    try:
        unknown_inner_puzzle = PuzzleWithRestrictions.from_memo(singleton_create_coin.memo_blob.rest())
    except ValueError:
        raise GetNextPlotNFTError("Invalid memoization of PlotNFT")
```
`get_next_from_coin_spend()` is invoked from wallet-side plotnft/pool-transition sync code (`chia/wallet/plotnft_wallet/plotnft_wallet.py`, `chia/wallet/wallet_state_manager.py`), which processes coin spends observed on-chain, including spends the wallet did not itself create. Since anyone can submit a spend bundle that creates an odd-amount coin at the expected PlotNFT/pool singleton chain with a crafted memo, this attacker-controlled recursive-depth memo reaches `from_memo()` without any pre-validation of nesting depth.

### Impact Explanation
A crafted spend with a sufficiently deep (but CLVM-cheap, since memos are raw bytes, not executed CLVM) nested `MofN` memo will exhaust the Python call stack when `from_memo()` recurses, raising `RecursionError` or crashing the wallet process handling PlotNFT/pool-singleton sync. This is a spend-triggered processing halt on the wallet side: a single malicious spend (visible to any node/wallet tracking the affected singleton lineage, e.g. a pool operator's or user's plotnft wallet) can repeatedly deny service to that wallet's sync loop each time it processes the offending coin, matching the "spend-triggered transaction-processing halt" acceptance criterion. Because the memo is arbitrary bytes (not run through CLVM cost accounting), the attacker pays only ordinary spend/coin-creation costs while forcing unbounded recursive Python parsing on the victim's side — a cost asymmetry directly analogous to the Multer/`append-field` advisory.

### Likelihood Explanation
Likelihood is limited by the requirement that the malicious memo be attached to a coin that is actually recognized as being in a tracked PlotNFT/pool singleton lineage (i.e., matches the expected launcher/singleton puzzle shape) for `get_next_from_coin_spend()` to be invoked on it — but the memo content itself is not otherwise validated before being handed to the recursive parser, and constructing such a coin spend requires no special privilege beyond controlling the relevant singleton. Given how narrow the specific reachable call path is (PlotNFT pool-transition sync only), likelihood is moderate rather than trivially universal, but the underlying `from_memo()` recursion has no depth guard at all, so any future or existing caller that parses attacker-influenced memos through this API inherits the same unbounded-recursion exposure.

### Recommendation
Add an explicit maximum recursion/nesting depth (and a corresponding max encoded size check) to `PuzzleWithRestrictions.from_memo()`/`MofNHint.from_program()` before recursing into `member_memos`, raising a `ValueError` (already handled by callers such as `plotnft_drivers.py`) once the depth limit is exceeded. Alternatively, convert the recursive descent into an iterative, stack-based traversal (as already done for `sha256_treehash()` in `chia/types/blockchain_format/tree_hash.py`) so no attacker-controlled nesting can exhaust the Python call stack.

### Proof of Concept
1. Build a base leaf memo program using `UnknownMember(MemberHint(...)).memo(nonce)`-style encoding wrapped as a `PuzzleWithRestrictions` memo (as in `PuzzleWithRestrictions.memo()`).
2. Repeatedly wrap it: `outer = PuzzleWithRestrictions(nonce=i, restrictions=[], puzzle=MofN(m=1, members=[inner]))`, chaining N (e.g., a few thousand) levels deep, each `memo()` embedding the previous as its sole `MofN` member memo — this is cheap to construct since it's just nested list construction, no CLVM execution cost.
3. Attach the resulting deeply-nested memo as the `memo_blob` on a `CREATE_COIN` condition for a coin that matches the shape expected by `PlotNFT.get_next_from_coin_spend()` (odd-amount singleton child, correct launcher hash) and submit the spend bundle.
4. When a wallet tracking that singleton lineage processes the coin (via `get_next_from_coin_spend()` → `PuzzleWithRestrictions.from_memo()`), the recursive `from_memo()` calls exceed Python's recursion limit and raise `RecursionError`, halting/crashing that wallet processing path instead of the intended `ValueError`("Invalid memoization of PlotNFT") handling.

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
