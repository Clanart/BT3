## Confirmed: Unbounded Python Recursion in `PuzzleWithRestrictions.from_memo()` Reachable from Wallet Sync of Untrusted Coin Data

### Title
Unbounded recursive `PuzzleWithRestrictions.from_memo()` parsing of attacker-controlled coin memos causes Python `RecursionError` / stack exhaustion (DoS) - (File: chia/wallet/puzzles/custody/custody_architecture.py)

### Summary
`PuzzleWithRestrictions.from_memo()` recursively reconstructs nested `MofN` custody structures from an on-chain memo `Program` with no depth limit, mirroring the ion-java bug class (`StackOverflowError`/CWE-770 from deserializing untrusted recursive data). Any coin creator can attach a deeply nested MofN memo to a `CREATE_COIN` output; when a wallet that tracks PlotNFT/custody puzzles encounters that coin during sync, this Python code recurses once per nesting level with no bound, causing a `RecursionError` that crashes/halts the wallet's coin-processing path.

### Finding Description
`PuzzleWithRestrictions.from_memo()` is truly recursive Python (not the Rust CLVM engine, and not the deliberately-iterative `sha256_treehash`/tree-hash helpers used elsewhere in the codebase): [1](#0-0) 

Specifically the recursive call:
```python
if further_branching:
    m_of_n_hint = MofNHint.from_program(puzzle_hint_prog)
    puzzle: MIPSComponent = MofN(
        m=m_of_n_hint.m, members=[PuzzleWithRestrictions.from_memo(memo) for memo in m_of_n_hint.member_memos]
    )
``` [2](#0-1) 

There is no depth or size cap on `member_memos`/nested `MofN` structures — the memo is an arbitrary `Program` supplied by whoever created the coin (a `CREATE_COIN` memo field), and `from_memo` blindly recurses into every nested member.

This function is reached from wallet sync of a `CREATE_COIN`'s memo without any trust check: [3](#0-2) 

```python
if plotnft_puzzle is None:
    if singleton_create_coin.memo_blob is None:
        raise GetNextPlotNFTError("Invalid memoization of PlotNFT")
    try:
        unknown_inner_puzzle = PuzzleWithRestrictions.from_memo(singleton_create_coin.memo_blob.rest())
    except ValueError:
        raise GetNextPlotNFTError("Invalid memoization of PlotNFT")
```
`PlotNFT.get_next_from_coin_spend()` (containing the above) is invoked automatically during wallet sync from `PlotNFT2Wallet.identify()` for every coin whose puzzle looks like a singleton with the PlotNFT/plotnft2 launcher hash, i.e. driven by coin data seen on chain, not by data the wallet's own owner constructs: [4](#0-3) 

This is a classic "deserialize untrusted recursive structure with an unbounded, non-iterative parser" bug — directly analogous to the `ion-java` `IonValue`/text deserialization `StackOverflowError` (GHSA-264p-99wq-f4j6): a crafted nested input drives Python's call stack past its limit, raising an uncaught `RecursionError` inside coin-processing code that a wallet runs automatically while syncing.

Contrast this with the codebase's established mitigation pattern elsewhere: `sha256_treehash()`/`_tree_hash()` in `chia/types/blockchain_format/tree_hash.py` is explicitly implemented as an iterative, stack-based traversal "so we don't have to worry about blowing out the python stack" [5](#0-4) , and CLVM program parsing itself is delegated to the Rust engine via `run_chia_program` in `Program.from_bytes()` [6](#0-5)  precisely to avoid this class of issue. `PuzzleWithRestrictions.from_memo()` does not follow either mitigation.

### Impact Explanation
A wallet that supports PlotNFT2/custody-architecture puzzles will process any coin that superficially matches the singleton/launcher shape (this check happens before the memo is trusted). An attacker can spend/create a coin with a puzzle mimicking the singleton pattern and a memo containing thousands of nested `MofN` branches. When `PlotNFT.get_next_from_coin_spend()` → `PuzzleWithRestrictions.from_memo()` processes it, Python recursion depth is exceeded, raising `RecursionError`. Depending on how the calling sync loop handles this exception (only `ValueError`/`GetNextPlotNFTError` are caught around this call — `RecursionError` is **not** a `ValueError`), the exception can propagate out of the coin-processing path and crash/halt wallet sync — a spend-triggered transaction-processing halt for any wallet client that encounters the malicious coin. This matches the required "spend-triggered transaction-processing halt" impact class.

### Likelihood Explanation
Likelihood is high for any wallet running PlotNFT2/custody-architecture support: the attacker needs only to create one on-chain coin (trivial cost, no privileged position) with a crafted memo; no cooperation from the victim wallet's owner is needed beyond normal chain syncing. The bug is purely a data-shape issue independent of puzzle validity/spendability — the coin doesn't even need to be spendable, since the vulnerable code path triggers on any coin whose puzzle superficially resembles the PlotNFT singleton and whose `CREATE_COIN` memo has this nested shape.

### Recommendation
- Enforce a maximum nesting depth (and/or maximum total node count) in `PuzzleWithRestrictions.from_memo()` before recursing into `MofNHint.member_memos`, raising a `ValueError`/`GetNextPlotNFTError` instead of recursing unbounded.
- Convert `from_memo()` to an iterative (explicit worklist/stack) implementation, consistent with the pattern already used in `sha256_treehash()`.
- Ensure the calling code (`PlotNFT.get_next_from_coin_spend()`, `PlotNFT2Wallet.identify()`) catches `RecursionError` (or, after the fix, the new bounded-depth `ValueError`) so a malformed/malicious memo cannot escape as an unhandled exception during sync.

### Proof of Concept
1. Construct a `Program` memo of the form accepted by `from_memo()`:
   `(spec_namespace [nonce, [], 1, [m, [child_memo_1, ..., child_memo_n]]])` where each `child_memo_i` is itself another `further_branching` MofN memo, nested to a depth of several thousand (well beyond Python's default recursion limit, ~1000).
2. Create/spend a coin whose puzzle matches the PlotNFT/custody singleton shape checked in `PlotNFT.get_next_from_coin_spend()` (`singleton.mod == singleton_mod` and launcher hash match), and set its `CREATE_COIN` memo to the crafted deeply-nested memo bytes (analogous to the existing test helper `PuzzleWithRestrictions.from_memo(...)` shown in `chia/_tests/clvm/test_custody_architecture.py:465-467`, but nested far deeper).
3. Have a victim wallet with a `PlotNFT2Wallet` sync the block containing this coin. `PlotNFT2Wallet.identify()` calls `PlotNFT.get_next_from_coin_spend()`, which calls `PuzzleWithRestrictions.from_memo(singleton_create_coin.memo_blob.rest())`, recursing once per nesting level and raising an uncaught `RecursionError`, which is not caught by the `except GetNextPlotNFTError` / `except ValueError` handlers surrounding the call.

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

**File:** chia/wallet/plotnft_wallet/plotnft_wallet.py (L539-544)
```python
            next_plot_nft = PlotNFT.get_next_from_coin_spend(
                coin_spend=coin_spend,
                genesis_challenge=wallet_state_manager.constants.GENESIS_CHALLENGE,
                pre_uncurry=uncurried,
                previous_plotnft_puzzle=previous_plotnft,
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

**File:** chia/types/blockchain_format/program.py (L56-68)
```python
    @classmethod
    def from_bytes(cls, blob: bytes) -> Self:
        # this runs the program "1", which just returns the first argument.
        # the first argument is the buffer we want to parse. This effectively
        # leverages the rust parser and LazyNode, making it a lot faster to
        # parse serialized programs into a python compatible structure
        _cost, ret = run_chia_program(
            b"\x01",
            blob,
            50,
            0,
        )
        return cls.to(ret)
```
