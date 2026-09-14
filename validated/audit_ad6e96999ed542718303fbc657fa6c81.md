### Title
Unbounded Recursive Memo Parsing in `PuzzleWithRestrictions.from_memo()` Enables Attacker-Triggered Wallet Crash (DoS) During PlotNFT/Custody Sync - ([File: chia/wallet/puzzles/custody/custody_architecture.py])

### Summary
`PlotNFT.identify()` / `PlotNFT.get_next_from_coin_spend()` calls `PuzzleWithRestrictions.from_memo()` on the memo of any observed `CREATE_COIN` output that matches the PlotNFT/custody singleton shape, in order to reconstruct wallet state from chain data during normal sync. `from_memo()` recurses into itself once per `MofN` member with no depth or size bound, mirroring the unsanitized/uncontrolled-input pattern in the original LDAP-injection DoS report (attacker-controlled input directly drives unbounded work in a trusted service). A malicious spender can craft an arbitrarily deeply nested `MofN` memo attached to an odd-amount singleton coin and get a victim's syncing wallet to recurse until Python's recursion limit is hit, raising an uncaught `RecursionError` that is not one of the handled exceptions, crashing the wallet sync path.

### Finding Description
`PuzzleWithRestrictions.from_memo()` in `chia/wallet/puzzles/custody/custody_architecture.py` (around lines 271-296) parses an on-chain memo `Program` and, when the `further_branching` flag is set, recursively calls `PuzzleWithRestrictions.from_memo(memo)` for every entry in `MofNHint.member_memos`: [1](#0-0) 

There is no limit on nesting depth, on the number of members processed per level, or on total memo size before recursing. This function is reachable from consensus data that an unprivileged party fully controls: any coin spend's `CREATE_COIN` memo is attacker-chosen CLVM data.

The production call path is:
- `PlotNFT.get_next_from_coin_spend()` in `chia/pools/plotnft_drivers.py` calls `PuzzleWithRestrictions.from_memo(singleton_create_coin.memo_blob.rest())` whenever it cannot otherwise identify the PlotNFT state transition from puzzle-hash matching: [2](#0-1) 

- This is invoked from wallet sync via `PlotNFT2Wallet.identify()` in `chia/wallet/plotnft_wallet/plotnft_wallet.py`, which runs for every coin the wallet sees that could plausibly be a PlotNFT/pool singleton: [3](#0-2) 

`identify()` only catches `GetNextPlotNFTError`: [4](#0-3) 

A `RecursionError` raised deep inside `PuzzleWithRestrictions.from_memo()` is not a `GetNextPlotNFTError` and will propagate uncaught out of `identify()`, into the wallet's coin-processing/sync loop.

This directly parallels the reported LDAP-injection bug class: an external, low-cost, attacker-controlled input (there, a crafted LDAP filter string; here, a crafted nested `MofN` memo) is fed into unsanitized/unbounded processing logic in the victim service, producing catastrophic resource consumption/exhaustion and a service crash rather than a controlled rejection.

### Impact Explanation
Any user who runs a PlotNFT-aware wallet and is subscribed/hinted to a malicious singleton coin (e.g., by simply being sent one, or by observing it via existing PlotNFT hint subscriptions) can have their wallet's sync path crash on an uncaught `RecursionError` when it attempts to identify/reconstruct the PlotNFT state from the malicious memo. This is a spend-triggered transaction-processing halt affecting wallet clients that track PlotNFT/pool state — a legitimate Medium/High-severity DoS class matching the "spend-triggered transaction-processing halt" impact category permitted by the rules. It does not require any privileged access; the attacker only needs to publish one ordinary spend bundle with the malicious memo.

### Likelihood Explanation
Likelihood is Medium: constructing a deeply nested `MofN` memo program is cheap for the attacker (CLVM cons-pair construction cost scales linearly, not exponentially, and there's no evidence of a CLVM/mempool cost bound specifically preventing deeply nested memo structures from being included in a `CREATE_COIN` memo). The victim wallet must be tracking/hinted to the coin for `identify()` to be invoked, which is the normal operating mode for PlotNFT-wallet users following pool singletons. I was unable to fully verify from static reading alone the exact number of nesting levels needed to exceed Python's default recursion limit relative to consensus size/cost limits on solutions/memos, so the precise minimum payload size is uncertain and should be confirmed empirically.

### Recommendation
Add an explicit depth (and/or node-count) limit to `PuzzleWithRestrictions.from_memo()`'s recursive `MofN` handling, raising a caught, well-typed exception (e.g., a subclass already handled by callers such as `GetNextPlotNFTError`) once a maximum depth is exceeded, instead of allowing unbounded Python recursion. Additionally, wrap `from_memo()` invocations in wallet sync paths (`PlotNFT.get_next_from_coin_spend`, `PlotNFT2Wallet.identify`) to catch `RecursionError` defensively and convert it into a handled parse failure.

### Proof of Concept
1. Attacker builds a `CREATE_COIN` condition memo shaped as a `PuzzleWithRestrictions` memo (`spec_namespace = "CHIP-0043"`) whose `MofNHint` recursively nests thousands of `MofN` levels, each with a single trivial member (cheap to construct: `Program.to((namespace, [nonce, [], 1, MofNHint(m=1, member_memos=[<next nested memo>]).to_program()]))` repeated N times).
2. Attacker creates a coin (e.g., odd-amount singleton matching the PlotNFT/custody singleton shape) whose spend's `CREATE_COIN` uses this memo, and gets it accepted into a block (ordinary spend, no privilege required).
3. A victim wallet with PlotNFT support processes the resulting coin during sync; `PlotNFT.identify()` → `PlotNFT.get_next_from_coin_spend()` → `PuzzleWithRestrictions.from_memo()` recurses N times.
4. At sufficient N (bounded only by Python's default recursion limit, ~1000, not by any consensus-level nesting check), Python raises `RecursionError`, which is not caught by `identify()`'s `except GetNextPlotNFTError` handler, propagating up and disrupting the wallet's coin-processing/sync flow — a spend-triggered denial of service against the victim's wallet client.

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

**File:** chia/pools/plotnft_drivers.py (L476-485)
```python
        # Finally, we try to look for the memos
        if plotnft_puzzle is None:
            if singleton_create_coin.memo_blob is None:
                raise GetNextPlotNFTError("Invalid memoization of PlotNFT")
            try:
                unknown_inner_puzzle = PuzzleWithRestrictions.from_memo(singleton_create_coin.memo_blob.rest())
            except ValueError:
                raise GetNextPlotNFTError("Invalid memoization of PlotNFT")
            if unknown_inner_puzzle.additional_memos is None:
                raise GetNextPlotNFTError("Invalid memoization of PlotNFT")
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

**File:** chia/wallet/plotnft_wallet/plotnft_wallet.py (L591-593)
```python
        except GetNextPlotNFTError:
            pass
        return None
```
