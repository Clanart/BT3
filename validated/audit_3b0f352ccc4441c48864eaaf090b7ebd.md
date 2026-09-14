## Analog Found: Unbounded recursive parsing of CHIP-0043 custody memos causes wallet client crash (RecursionError)

### Title
Unbounded recursive `MofN` memo parsing in custody architecture allows a crafted coin memo to crash any wallet client syncing it - (File: `chia/wallet/puzzles/custody/custody_architecture.py`)

### Summary
`PuzzleWithRestrictions.from_memo()` reconstructs a CHIP-0043 custody puzzle tree from an on-chain memo `Program`. When the puzzle is an `MofN`, it recurses once per member by calling itself on each member's memo, with no depth or size limit. Because the memo is attacker-controlled data attached to a coin (a hint/memo any unprivileged spend-bundle submitter can put on any `CREATE_COIN`), an attacker can construct an arbitrarily deep nested `MofN` memo that, when a wallet or other client attempts to sync/parse the coin's custody puzzle from its memo, causes unbounded Python recursion and crashes the parsing process — mirroring the Mattermost bug class (missing length check on a nested/composite field leading to a crash of the processing component).

### Finding Description
`PuzzleWithRestrictions.memo()` is the documented on-chain synchronization contract for the CHIP-0043 custody architecture: "the on-chain/exported synchronization contract... `from_memo()` must reconstruct unknown members/restrictions, including recursive `MofN`, so later wallet code can fill known puzzle implementations by puzzle hash" (per `.cursor/context/testing/clvm.md`). The corresponding parser: [1](#0-0) 

recurses through `further_branching`/`MofNHint.from_program()` and calls `PuzzleWithRestrictions.from_memo(memo)` once per member with no cap on nesting depth or member count:

```
if further_branching:
    m_of_n_hint = MofNHint.from_program(puzzle_hint_prog)
    puzzle: MIPSComponent = MofN(
        m=m_of_n_hint.m, members=[PuzzleWithRestrictions.from_memo(memo) for memo in m_of_n_hint.member_memos]
    )
```

The test suite explicitly documents recursive `MofN` reconstruction as the intended, supported shape (`from_memo()` "must reconstruct unknown members/restrictions, including recursive `MofN`") and exercises nested `MofN(MofN(...))` structures: [2](#0-1) 

Unlike the deliberately non-recursive, stack-safe `sha256_treehash()` implementation used elsewhere in the codebase specifically to avoid "blowing out the python stack" for arbitrary CLVM trees: [3](#0-2) 

`from_memo()` has no equivalent protection. There is no length/depth check on `member_memos`, `restrictions`, or the overall memo `Program` before recursing, and no `MAX_RECURSION`/depth-limit constant exists anywhere in `chia/wallet/puzzles/custody/`. A memo is regular `CREATE_COIN` third-argument data that any unprivileged spend-bundle submitter fully controls (see `CreateCoin`/`compute_memos_for_spend`), and CHIP-0043 custody coins (including plotnft custody puzzles, which build `PuzzleWithRestrictions`/`MofN` trees via `chia/pools/plotnft_drivers.py`) are exactly the kind of coin whose memo a wallet client would attempt to parse via `from_memo()` to recover "unknown puzzle" state during sync, per the documented intent ("This is necessary functionality to sync an unknown inner puzzle from on chain.").

### Impact Explanation
An attacker can create a coin (via a single, otherwise-valid spend bundle) with a memo shaped as the CHIP-0043 namespace tag plus a deeply nested `MofN` hint tree (e.g., thousands of levels of single-member `MofN` wrapping). Any process that calls `PuzzleWithRestrictions.from_memo()` on that memo — a wallet client or other tool implementing custody-puzzle recognition/sync as documented — will recurse once per nesting level. Because Python's default recursion limit is far smaller than the depth an attacker can encode cheaply in a CLVM list, this raises an uncaught `RecursionError` (which can also occur mid-way through C-level stack frames, risking a hard interpreter crash rather than a catchable exception in some environments). This is a spend-triggered processing halt of the parsing component for any client that follows the documented custody-memo sync path — the same bug class as the reported Mattermost issue (missing length validation on an attacker-supplied nested field crashing the consuming component), scoped here to "Wallet state and key derivation" / "plotnft and pool transitions" reachable via a coin/memo an unprivileged spend-bundle submitter fully controls.

### Likelihood Explanation
Likelihood is high for any code path that adopts the documented `from_memo()` sync contract, since:
- The memo format and recursion behavior are attacker-controlled end-to-end (CREATE_COIN memo bytes).
- No existing check anywhere in `custody_architecture.py` bounds `MofN` nesting depth, member count, or overall memo size before recursing.
- The project already treats "blowing out the python stack" as a known, real risk class serious enough to have engineered a non-recursive alternative (`sha256_treehash`) for structurally similar attacker-controlled CLVM tree traversal, but this protection was not applied to `from_memo()`.
- `MofN` explicitly supports recursive members by construction/spec, so the vulnerable path is not a corner case but a core supported feature.

The main uncertainty is which specific production wallet call site currently exercises `from_memo()` against untrusted, unresolved coins during normal sync (the codebase index available to this analysis surfaces test usage and `plotnft_drivers.py`'s puzzle-construction side, but not a definitive production "coin arrives → call `from_memo()`" call site) — this should be confirmed by a full-repo review, since the index has size limits and some call sites may not be indexed.

### Recommendation
- Convert `PuzzleWithRestrictions.from_memo()` (and `MofNHint.from_program()`/the `MofN` member reconstruction loop) to an iterative, explicit-stack traversal, analogous to `sha256_treehash()`, so parsing untrusted memo data cannot exhaust the Python call stack.
- Add an explicit maximum nesting depth and/or maximum total member count for `MofN` reconstruction from memo, rejecting memos that exceed it with a clear `ValueError` rather than recursing further.
- Add a maximum total memo size/atom-count guard before beginning `from_memo()` parsing, consistent with other CLVM-parsing size limits used elsewhere in the codebase (e.g., `parse_list_limited`/list-limit patterns in `chia/util/streamable.py`).
- Add a regression test that constructs a memo with a very deep `MofN` nesting chain and asserts `from_memo()` raises a controlled `ValueError` instead of a `RecursionError`/crash.

### Proof of Concept
```python
from chia.types.blockchain_format.program import Program
from chia.wallet.puzzles.custody.custody_architecture import PuzzleWithRestrictions

def build_malicious_memo(depth: int) -> Program:
    # Build from the innermost leaf outward: a chain of "1 of 1" MofN wrappers
    inner = Program.to(
        (PuzzleWithRestrictions.spec_namespace,
         [0, [], None, [bytes([0] * 32), None]])  # leaf: UnknownMember hint
    )
    for i in range(depth):
        # Wrap as MofN(m=1, member_memos=[inner]) -> further_branching = 1 (True)
        inner = Program.to(
            (PuzzleWithRestrictions.spec_namespace,
             [i, [], 1, [1, [inner]]])
        )
    return inner

malicious_memo = build_malicious_memo(depth=100_000)  # depth cheaply attacker-chosen
# Any wallet/client that recognizes this coin and calls from_memo() on its memo:
PuzzleWithRestrictions.from_memo(malicious_memo)  # -> RecursionError / crash
```
The attacker only needs to get a coin with this memo attached (e.g., via `CreateCoin(puzzle_hash, amount, memos=[...])` in a normal, unprivileged spend bundle) recognized by a victim wallet's custody-puzzle sync path that calls `PuzzleWithRestrictions.from_memo()`, exactly mirroring how the Mattermost Playbooks plugin crashed on an unchecked, attacker-controlled field length/structure.

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

**File:** chia/_tests/clvm/test_custody_architecture.py (L83-116)
```python
        # 2 of 2 (further 1 of 1s)
        MofN(
            m=2,
            members=[
                PuzzleWithRestrictions(
                    nonce=1,
                    restrictions=[],
                    puzzle=MofN(
                        m=1,
                        members=[
                            PuzzleWithRestrictions(
                                nonce=3,
                                restrictions=[],
                                puzzle=UnknownMember(MemberHint(puzhash=BUNCH_OF_ZEROS, memo=ANY_PROGRAM)),
                            )
                        ],
                    ),
                ),
                PuzzleWithRestrictions(
                    nonce=4,
                    restrictions=[],
                    puzzle=MofN(
                        m=1,
                        members=[
                            PuzzleWithRestrictions(
                                nonce=5,
                                restrictions=[],
                                puzzle=UnknownMember(MemberHint(puzhash=BUNCH_OF_ONES, memo=ANY_PROGRAM)),
                            )
                        ],
                    ),
                ),
            ],
        ),
```

**File:** chia/types/blockchain_format/tree_hash.py (L1-8)
```python
"""
This is an implementation of `sha256_treehash`, used to calculate
puzzle hashes in clvm.

This implementation goes to great pains to be non-recursive so we don't
have to worry about blowing out the python stack.
"""

```
