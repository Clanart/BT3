### Title
Unbounded recursive parsing of attacker-controlled coin memo causes stack-overflow DoS during PlotNFT/custody sync - (File: chia/wallet/puzzles/custody/custody_architecture.py)

### Summary
`PuzzleWithRestrictions.from_memo()` recursively reconstructs a custody tree from a CLVM `Program` memo without any depth or size bound. The memo is untrusted data that any coin creator can embed in a `CREATE_COIN` condition's memo field, and it is parsed by client code (`chia/pools/plotnft_drivers.py`'s `PlotNFT.get_next_from_coin_spend`, and wallet custody-sync code) when following singleton/plotNFT lineage. This mirrors the ImageMagick MVG bug class: recursive re-entry into a parser without a cycle/depth guard, driven entirely by attacker-supplied nested structure, leading to unbounded native/interpreter stack growth and a crash.

### Finding Description
`PuzzleWithRestrictions.from_memo()` in `chia/wallet/puzzles/custody/custody_architecture.py` (lines 271-296) parses a `Program` memo of the form `(CHIP-0043 [nonce, restriction_hints, further_branching, puzzle_hint, ...])`. When `further_branching` is truthy, it decodes an `MofNHint` and recurses:

```
if further_branching:
    m_of_n_hint = MofNHint.from_program(puzzle_hint_prog)
    puzzle: MIPSComponent = MofN(
        m=m_of_n_hint.m, members=[PuzzleWithRestrictions.from_memo(memo) for memo in m_of_n_hint.member_memos]
    )
``` [1](#0-0) 

Each `MofNHint.member_memos` entry can itself be another `(CHIP-0043 ...)` memo with `further_branching=True`, allowing the attacker to nest this structure to an arbitrary depth purely inside CLVM memo bytes attached to a coin (a `Program`, not gated by CLVM execution cost once it reaches this Python-side driver). There is no depth limit, node count limit, or iterative rewrite (unlike `sha256_treehash`, which the codebase explicitly made iterative specifically to avoid Python recursion-limit crashes — see `chia/types/blockchain_format/tree_hash.py`) [2](#0-1) .

This memo-parsing path is exercised by production PlotNFT/pool-transition code: `chia/pools/plotnft_drivers.py` imports `PuzzleWithRestrictions` from `custody_architecture` and reconstructs `PlotNFT` state from `CreateCoin` memo blobs found in a parent coin spend's output conditions [3](#0-2) . A test fixture confirms the memo blob shape consumed directly from a `CreateCoin` condition memo during `PlotNFT.get_next_from_coin_spend()`: [4](#0-3) 

Because `memo_blob` on `CreateCoin` is fully attacker-controlled (any spend can create a coin with an arbitrary memo), an attacker can craft a coin whose memo encodes a deeply nested `MofN` custody tree (e.g., tens of thousands of nested `further_branching=True` levels). When a victim's wallet, pool-tracking client, or PlotNFT-following code processes that coin (a routine, automatic sync action, not requiring any special privilege from the attacker beyond broadcasting one transaction/coin), `from_memo()` recurses once per nesting level. Because Python's default recursion limit (and, more importantly, the C stack backing CPython's interpreter loop) is finite, sufficiently deep nesting triggers a `RecursionError`/stack overflow, crashing or hanging the wallet/plotnft-tracking process that processes the coin — directly analogous to Magick's unmet-depth-check MVG recursion leading to `AddressSanitizer: DEADLYSIGNAL`/stack overflow.

### Impact Explanation
This is a spend-triggered processing-halt: a single attacker-crafted coin (cheap to create, requiring only a standard spend bundle with a `CREATE_COIN` condition carrying a malicious memo) can crash any client-side component that walks custody/plotNFT memo trees during normal sync (wallet sync, PlotNFT/pool-transition tracking). Because this hits wallet/pool client code rather than full-node consensus, it does not directly corrupt consensus state, but it can reliably deny service to affected wallets/pool trackers that must process the coin to keep following their singleton/plotnft lineage, matching the "spend-triggered transaction-processing halt" acceptance criterion.

### Likelihood Explanation
Likelihood is straightforward: constructing a deeply nested memo is pure Python/CLVM data construction (no signature or special permission needed) and can be attached to any coin the attacker creates and sends to, or that becomes visible to, a victim's syncing wallet or plotnft-tracking logic. No cost-metering gate exists in this Python-side parsing path since it operates purely on already-decoded CLVM `Program` structures rather than through the cost-limited CLVM interpreter.

### Recommendation
Add an explicit maximum recursion/nesting depth (and/or total node count) check in `PuzzleWithRestrictions.from_memo()` and `MofNHint.from_program()`, raising a `ValueError` when exceeded, mirroring the iterative/bounded approach already used for `sha256_treehash()`. Alternatively, convert `from_memo()` to an iterative, explicit-stack-based traversal so a crafted memo cannot exhaust the process/interpreter stack, and validate memo depth/size before beginning traversal in all call sites that ingest untrusted `CreateCoin.memo_blob` data (including `chia/pools/plotnft_drivers.py`).

### Proof of Concept
Construct a `CreateCoin` condition whose `memo_blob` is a deeply right-nested CHIP-0043 memo where each level sets `further_branching=1` and supplies a single `MofNHint.member_memos` entry that is itself another full CHIP-0043 memo (repeat N times, e.g. N = 50,000). Broadcast a spend bundle that creates a coin with this memo. When a victim wallet/pool client later calls `PuzzleWithRestrictions.from_memo()` (directly, or transitively via `PlotNFT.get_next_from_coin_spend()` in `chia/pools/plotnft_drivers.py`) on this coin's memo, the recursive rebuild in `custody_architecture.py:284-286` recurses N times, exceeding the process stack and crashing (or raising an uncaught `RecursionError` in) the syncing client — exactly reproducing the "circular/deeply-nested-structure causes stack overflow" bug class from GHSA-7rvh-xqp3-pr8j, adapted to Chia's memo-parsing code path.

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

**File:** chia/types/blockchain_format/tree_hash.py (L1-8)
```python
"""
This is an implementation of `sha256_treehash`, used to calculate
puzzle hashes in clvm.

This implementation goes to great pains to be non-recursive so we don't
have to worry about blowing out the python stack.
"""

```

**File:** chia/pools/plotnft_drivers.py (L14-34)
```python
from chia.types.blockchain_format.program import Program, run
from chia.types.coin_spend import make_spend
from chia.wallet.conditions import (
    AssertCoinAnnouncement,
    AssertHeightRelative,
    Condition,
    CreateCoin,
    CreateCoinAnnouncement,
    MessageParticipant,
    Remark,
    SendMessage,
    UnknownCondition,
    parse_conditions_non_consensus,
)
from chia.wallet.lineage_proof import LineageProof
from chia.wallet.puzzles.custody.custody_architecture import (
    DelegatedPuzzleAndSolution,
    MofN,
    ProvenSpend,
    PuzzleWithRestrictions,
)
```

**File:** chia/_tests/pools/test_plotnft_v2_drivers.py (L534-566)
```python
    with pytest.raises(GetNextPlotNFTError, match=re.escape("Invalid memoization of PlotNFT")):
        PlotNFT.get_next_from_coin_spend(
            coin_spend=FAUX_SPEND,
            pre_uncurry=wrap_inner_puz(
                Program.to(
                    (
                        1,
                        [
                            CreateCoin(
                                puzzle_hash=bytes32.zeros,
                                amount=uint64(1),
                                memo_blob=Program.to(
                                    (
                                        bytes32.zeros,
                                        (
                                            PuzzleWithRestrictions.spec_namespace,
                                            [
                                                None,
                                                [[None, bytes32.zeros, None]],
                                                None,
                                                [bytes32.zeros, None],
                                                [G1Element()],
                                            ],
                                        ),
                                    )
                                ),
                            ).to_program()
                        ],
                    )
                )
            ),
            genesis_challenge=bytes32.zeros,
        )
```
