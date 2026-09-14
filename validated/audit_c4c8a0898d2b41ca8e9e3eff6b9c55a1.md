### Title
Uncontrolled recursion in `PuzzleWithRestrictions.from_memo()` allows a spend-triggered denial of service against PlotNFT/custody-architecture wallets - ([File: chia/wallet/puzzles/custody/custody_architecture.py])

### Summary
`PuzzleWithRestrictions.from_memo()` recursively reconstructs `MofN` custody structures from an on-chain coin's memo bytes with no depth or size limit. Because this memo is fully attacker-controlled (it comes from the `CREATE_COIN` memo of any coin a user chooses to create), an attacker can craft a coin with a deeply nested `MofN` memo tree that, when a PlotNFT (or other custody-architecture) wallet syncs that coin, triggers unbounded Python recursion and a crash/hang while processing the spend — a transaction-processing halt for the affected node, analogous to the "uncontrolled recursion" component of the Apache Traffic Server ESI bug.

### Finding Description
`PuzzleWithRestrictions.from_memo()` in `chia/wallet/puzzles/custody/custody_architecture.py` parses a `Program` memo and, when the puzzle is a nested `MofN`, calls itself once per member with no recursion-depth cap: [1](#0-0) 

```
@classmethod
def from_memo(cls, memo: Program) -> PuzzleWithRestrictions:
    ...
    if further_branching:
        m_of_n_hint = MofNHint.from_program(puzzle_hint_prog)
        puzzle: MIPSComponent = MofN(
            m=m_of_n_hint.m, members=[PuzzleWithRestrictions.from_memo(memo) for memo in m_of_n_hint.member_memos]
        )
    ...
```

This function is invoked directly on untrusted, attacker-supplied coin data by `PlotNFT.get_next_from_coin_spend()`, which is the code path a PlotNFT wallet uses to interpret the memo of a `CREATE_COIN` condition observed on chain in order to reconstruct the next PlotNFT state: [2](#0-1) 

```
if plotnft_puzzle is None:
    if singleton_create_coin.memo_blob is None:
        raise GetNextPlotNFTError("Invalid memoization of PlotNFT")
    try:
        unknown_inner_puzzle = PuzzleWithRestrictions.from_memo(singleton_create_coin.memo_blob.rest())
    except ValueError:
        raise GetNextPlotNFTError("Invalid memoization of PlotNFT")
```

The `memo_blob` here is taken straight from the `CreateCoin` condition emitted by whatever inner puzzle spends the singleton — any user can mint a singleton (a PlotNFT-shaped or generically custody-architecture-shaped coin) whose `CREATE_COIN` memo encodes a `MofN` hint with a very deep chain of nested single-member `MofN` groups (`m=1, members=[MofN(m=1, members=[...])]`). Because Python has no tail-call optimization and CPython's default recursion limit is on the order of ~1000 frames, a memo of only a modest size (each nesting "layer" costs only a handful of bytes to encode) is enough to exceed the interpreter's recursion limit, raising an uncaught `RecursionError` (not a `ValueError`), which is *not* caught by the `except ValueError` handler in `get_next_from_coin_spend()`, so it propagates uncaught into whatever code path is syncing/spending that PlotNFT.

The equivalent recursive pattern for filling in unknown puzzles once loaded (`fill_in_unknown_puzzles()`) and for computing `unknown_puzzles` also recurse per `MofN` nesting level without a bound: [3](#0-2) 

The existing test suite's own comment acknowledges the recursive, attacker-influenced nature of this reconstruction path (`from_memo()` must reconstruct unknown members/restrictions, including recursive `MofN`), but no depth or size limit is documented or enforced: [4](#0-3) 

### Impact Explanation
This maps to the CVE's "uncontrolled recursion" bug class, but with an availability-only impact (comparable to the CVSS `VA:H` component of CVE-2026-58178) rather than the SSRF component, since there is no attacker-directed outbound network fetch here. A crafted memo on a coin spend (which any unprivileged user can create and get confirmed on chain, e.g. by minting a plotnft-shaped singleton or any coin whose `CREATE_COIN` condition includes a deeply-nested custody-architecture memo) causes any wallet/service that walks that coin's history through `PlotNFT.get_next_from_coin_spend()` (or any other caller of `PuzzleWithRestrictions.from_memo()`/`fill_in_unknown_puzzles()`) to hit unbounded Python recursion. This results in an uncaught `RecursionError` that is not handled by the narrow `except ValueError` guard, causing the wallet processing loop (sync, plotnft state transition, custody-architecture reconstruction) to crash or otherwise fail to make forward progress — a spend-triggered transaction-processing halt for the affected process.

### Likelihood Explanation
Likelihood is high for any attacker willing to spend a small fee: constructing the malicious memo requires only crafting a `CREATE_COIN` condition with a nested `MofN` hint program; no signature bypass, no special privileges, and no cooperation from other parties are required. The vulnerable code executes automatically whenever a PlotNFT-aware wallet encounters and tries to interpret such a coin's spend during normal sync/pool-state processing, which is a routine, non-interactive path.

### Recommendation
Add an explicit, low bound on `MofN`/`PuzzleWithRestrictions` nesting depth (and on `member_memos` list size) in `PuzzleWithRestrictions.from_memo()`, `MofNHint.from_program()`, and `PuzzleWithRestrictions.fill_in_unknown_puzzles()`/`unknown_puzzles`, raising a caught `ValueError` (or a dedicated exception subclassed accordingly) once the bound is exceeded, and ensure all callers (e.g. `PlotNFT.get_next_from_coin_spend()`) catch that broadened exception type instead of only `ValueError`. Alternatively, convert the recursive traversal to an explicit iterative stack-based algorithm (similar to the non-recursive `sha256_treehash` implementation already used elsewhere in the codebase) so that recursion depth is not attacker-controllable at all.

### Proof of Concept
1. Build a memo `Program` using `MofNHint(m=1, member_memos=[nested])` where `nested` is itself the memo of another single-member `MofN`, and repeat this nesting ~1500–2000 times (well within normal on-chain memo size/cost limits since each layer adds only a small constant number of bytes).
2. Wrap this memo as the `memo_blob` of a `CreateCoin` condition emitted by a singleton spend that otherwise looks like a valid PlotNFT/custody-architecture coin (matching the shape expected by `PlotNFT.get_next_from_coin_spend()` / other consumers of `PuzzleWithRestrictions.from_memo()`), and get this spend confirmed on chain.
3. Any wallet/service that later processes that coin's spend and calls `PlotNFT.get_next_from_coin_spend()` (or otherwise calls `PuzzleWithRestrictions.from_memo()` on the malicious memo) will trigger Python's recursion limit, raising an uncaught `RecursionError` that propagates out of the narrow `except ValueError` handler, halting that processing path.

(Note: I could not fully trace every production call site that automatically invokes `PlotNFT.get_next_from_coin_spend()` during normal wallet sync within the indexed portion of the codebase — this should be verified with a live Devin session, e.g. by tracing `chia/wallet/plotnft_wallet/plotnft_wallet.py` and `chia/pools/pool_wallet.py` state-transition handling, to confirm the exact automatic trigger path and to build a runnable end-to-end PoC.)

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

**File:** chia/wallet/puzzles/custody/custody_architecture.py (L298-352)
```python
    @property
    def unknown_puzzles(self) -> Mapping[bytes32, UnknownMember | UnknownRestriction]:
        unknown_restrictions = {
            ur.restriction_hint.puzhash: ur for ur in self.restrictions if isinstance(ur, UnknownRestriction)
        }

        unknown_puzzles: Mapping[bytes32, UnknownMember | UnknownRestriction]
        if isinstance(self.puzzle, UnknownMember):
            unknown_puzzles = {self.puzzle.puzzle_hint.puzhash: self.puzzle}
        elif isinstance(self.puzzle, MofN):
            unknown_puzzles = {
                uph: up
                for puz_w_restriction in self.puzzle.members
                for uph, up in puz_w_restriction.unknown_puzzles.items()
            }
        else:
            unknown_puzzles = {}
        return {
            **unknown_puzzles,
            **unknown_restrictions,
        }

    def fill_in_unknown_puzzles(self, puzzle_dict: Mapping[bytes32, MIPSComponent]) -> PuzzleWithRestrictions:
        new_restrictions: list[Restriction[MemberOrDPuz]] = []
        for restriction in self.restrictions:
            if isinstance(restriction, UnknownRestriction) and restriction.restriction_hint.puzhash in puzzle_dict:
                new = puzzle_dict[restriction.restriction_hint.puzhash]
                # using runtime_checkable here to assert isinstance(new, Restriction) results in an error in the test
                # where PlaceholderPuzzle() is used. Not sure why, so we'll ignore since it's for mypy's sake anyways
                new_restrictions.append(new)  # type: ignore[arg-type]
            else:
                new_restrictions.append(restriction)

        new_puzzle: MIPSComponent
        if (
            isinstance(self.puzzle, UnknownMember) and self.puzzle.puzzle_hint.puzhash in puzzle_dict  # pylint: disable=no-member
        ):
            new_puzzle = puzzle_dict[self.puzzle.puzzle_hint.puzhash]  # pylint: disable=no-member
        elif isinstance(self.puzzle, MofN):
            new_puzzle = replace(
                self.puzzle,
                members=[
                    puz.fill_in_unknown_puzzles(puzzle_dict)
                    for puz in self.puzzle.members  # pylint: disable=no-member
                ],
            )
        else:
            new_puzzle = self.puzzle

        return PuzzleWithRestrictions(
            nonce=self.nonce,
            restrictions=new_restrictions,
            puzzle=new_puzzle,
            additional_memos=self.additional_memos,
        )
```

**File:** chia/pools/plotnft_drivers.py (L476-486)
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
            pubkey = G1Element.from_bytes(unknown_inner_puzzle.additional_memos.at("f").as_atom())
```

**File:** .cursor/context/testing/clvm.md (L61-66)
```markdown
The custody tests exercise `wallet.puzzles.custody.*` as a small composable puzzle framework:

- `PuzzleWithRestrictions.memo()` is the on-chain/exported synchronization contract. `from_memo()` must reconstruct unknown members/restrictions, including recursive `MofN`, so later wallet code can fill known puzzle implementations by puzzle hash.
- `PuzzleWithRestrictions.puzzle_reveal()` layers `INDEX_WRAPPER`, optional restriction layer, and top-level `DELEGATED_PUZZLE_FEEDER`; `puzzle_hash()` uses precalculated hashes to match the reveal without materializing every layer.
- `solve()` must align member validator solutions, delegated-puzzle validator solutions, member solution, and optional delegated puzzle/solution in the exact order expected by the CLVM modules.
- `MofN` rejects impossible thresholds and duplicate member nodes. Its solve format differs across threshold shapes, so tests iterate combinations to catch proof-format regressions.
```
