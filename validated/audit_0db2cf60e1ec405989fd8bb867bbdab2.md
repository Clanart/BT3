### Title
Unbounded recursive parsing of untrusted on-chain memo data in `PuzzleWithRestrictions.from_memo()` allows attacker-triggered `RecursionError` DoS during wallet sync - (File: `chia/wallet/puzzles/custody/custody_architecture.py`)

### Summary
`PuzzleWithRestrictions.from_memo()` reconstructs a custody/PlotNFT puzzle tree from an untrusted CLVM `Program` memo taken directly from an on-chain coin's `CREATE_COIN` memo. For `MofN` branches it recurses into itself once per member with no depth limit, mirroring the protobuf-python "unbounded recursive parsing of untrusted data" bug class (CVE-2025-4565): a Python-level recursive parser applied directly to attacker-supplied bytes with no depth cap can be driven past `sys.getrecursionlimit()`.

### Finding Description
`PuzzleWithRestrictions.from_memo()` parses a CLVM `Program` memo and, when the puzzle is an `MofN`, recursively calls itself for every member without any depth or size bound: [1](#0-0) 

This is reachable from a completely untrusted on-chain coin: `PlotNFT.get_next_from_coin_spend()` calls `PuzzleWithRestrictions.from_memo(singleton_create_coin.memo_blob.rest())` on the `memo_blob` of a `CreateCoin` condition parsed straight out of a coin spend's solution: [2](#0-1) 

`get_next_from_coin_spend()` is itself invoked automatically during ordinary wallet sync, whenever `determine_coin_type()` sees any singleton whose mod matches the `PlotNFT` singleton mod — this is not restricted to coins owned by the local wallet: [3](#0-2) [4](#0-3) 

Because `MofN` members are themselves `PuzzleWithRestrictions` objects that can again wrap an `MofN`, an attacker can construct a spend bundle that creates a coin with the correct singleton puzzle shape and a memo encoding thousands of nested `MofN` layers. When any wallet syncs past that coin, `from_memo()`'s recursive descent consumes one Python stack frame per nesting layer with no cap, eventually raising `RecursionError`.

Unlike other recursive/DoS-sensitive paths in this codebase — `sha256_treehash()` (iterative by design), `create_graph_util`'s explicit stack-based traversal, and the `chia_rs.datalayer.RecursionDepthExceededError` guard in the Rust Data Layer tree code — `from_memo()` has no analogous depth guard, and it operates directly on attacker-controlled `Program` data rather than Rust-validated bytes.

### Impact Explanation
An unprivileged party can craft and broadcast a single spend bundle that creates a coin whose puzzle uncurries to the `PlotNFT` singleton mod and whose `CREATE_COIN` memo encodes a deeply nested `MofN` custody structure. Any wallet (not just the coin owner) that syncs to a block containing this spend will call `PlotNFT2Wallet.identify()` → `get_next_from_coin_spend()` → `PuzzleWithRestrictions.from_memo()` and crash with an unhandled `RecursionError` inside the wallet sync loop (`determine_coin_type()`/`_add_coin_state()`), halting further sync/transaction processing for that wallet. This is a transaction/coin-processing halt triggered by a single spend, consistent with the "spend-triggered transaction-processing halt" impact class.

### Likelihood Explanation
Likelihood is high: the attacker only needs to submit one crafted spend bundle to the mempool that gets included in a block (no elevated privileges, no wallet ownership, and no signature requirements on the constructed `MofN`/memo shape are checked before parsing). Every wallet performing normal chain sync that observes this coin is affected, since `determine_coin_type()` unconditionally attempts to identify any singleton matching the `PlotNFT` mod.

### Recommendation
Add an explicit recursion/nesting depth limit (and/or convert to an iterative worklist algorithm, similar to `sha256_treehash()` or `create_graph_util()`) in `PuzzleWithRestrictions.from_memo()` before recursing into `MofN` members, and raise a catchable `ValueError`/`GetNextPlotNFTError` instead of allowing `RecursionError` to propagate. Ensure the caller (`PlotNFT.get_next_from_coin_spend()`) also catches the new bounded error the same way it already catches `ValueError`.

### Proof of Concept
1. Construct a `PuzzleWithRestrictions` whose `puzzle` is an `MofN` with `m=1` and a single member that is itself an `MofN` wrapping another `MofN`, repeated N times (N ≈ Python recursion limit, e.g. 2000+).
2. Serialize it via `.memo()` to get a `Program` and take `.rest()` as the raw memo the way `plotnft_drivers.py` does.
3. Call `PuzzleWithRestrictions.from_memo(memo)` directly, or embed the memo bytes into a `CreateCoin` condition emitted by a spend whose puzzle uncurries to the PlotNFT singleton mod, and push that spend bundle to a simulator/mempool.
4. Observe `RecursionError` raised from within `from_memo()`'s recursive call chain when any wallet processes/syncs that coin via `PlotNFT.get_next_from_coin_spend()`.

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

**File:** chia/wallet/wallet_state_manager.py (L1014-1019)
```python
        # Check if the coin is a PlotNFT
        if uncurried.mod == PlotNFT.singleton_puzzles.singleton_mod:
            plotnft_result = await PlotNFT2Wallet.identify(
                self, uncurried, coin_spend, coin_state.created_height, sync_scope
            )
            if plotnft_result is not None:
```

**File:** chia/wallet/plotnft_wallet/plotnft_wallet.py (L538-544)
```python
                    previous_plotnft = None
            next_plot_nft = PlotNFT.get_next_from_coin_spend(
                coin_spend=coin_spend,
                genesis_challenge=wallet_state_manager.constants.GENESIS_CHALLENGE,
                pre_uncurry=uncurried,
                previous_plotnft_puzzle=previous_plotnft,
            )
```
