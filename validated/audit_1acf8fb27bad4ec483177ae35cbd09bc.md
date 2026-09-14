### Title
Unbounded recursion in `PuzzleWithRestrictions.from_memo()` allows a crafted on-chain memo to stack-overflow / crash a syncing wallet - ([File: chia/wallet/puzzles/custody/custody_architecture.py])

### Summary
The Ox `sax_parse` bug class (CVE-2017-16229) is a native stack-based buffer over-read triggered by recursively parsing attacker-supplied, deeply-nested/crafted input with no depth bound. The closest reachable analog in this codebase is `PuzzleWithRestrictions.from_memo()`, a Python parser that reconstructs the "MIPS" custody architecture (used by wallet custody arrangements and the plotnft/pool custody layer) from an on-chain `Program` memo. It recurses into itself for every `MofN` branch with no depth or size limit, so a coin creator fully controls the recursion depth of a wallet client that later tries to sync that coin.

### Finding Description
`PuzzleWithRestrictions.memo()`/`from_memo()` is the documented "on-chain/exported synchronization contract" used to let a wallet reconstruct an unknown custody puzzle purely from the memo attached to a coin (e.g., via `CREATE_COIN` memos), as described in `.cursor/context/testing/clvm.md` ("`PuzzleWithRestrictions.memo()` is the on-chain/exported synchronization contract. `from_memo()` must reconstruct unknown members/restrictions, including recursive `MofN`"). [1](#0-0) 

```
@classmethod
def from_memo(cls, memo: Program) -> PuzzleWithRestrictions:
    ...
    further_branching = further_branching_prog != Program.to(None)
    if further_branching:
        m_of_n_hint = MofNHint.from_program(puzzle_hint_prog)
        puzzle: MIPSComponent = MofN(
            m=m_of_n_hint.m, members=[PuzzleWithRestrictions.from_memo(memo) for memo in m_of_n_hint.member_memos]
        )
    ...
```

`from_memo` decodes an attacker-controlled `Program` (the memo) and, whenever the encoded structure signals "further branching" (an `MofN` node), recursively calls itself once per member. Because the memo is just CLVM data attached to a coin and is not size- or depth-limited before this function is called, an attacker can encode a `MofN` tree nested to a depth of many hundreds/thousands of levels. Each level of nesting only costs a few bytes of CLVM atoms/list cons cells, so a compact memo can encode extremely deep recursion. When any wallet (or the plotnft/pool driver code, which also constructs/consumes this structure — see `chia/pools/plotnft_drivers.py`) calls `PuzzleWithRestrictions.from_memo()` on such a coin's memo while syncing, the unbounded Python-level recursion will exhaust the interpreter's call stack, raising `RecursionError` (or, depending on interpreter/environment, causing a native stack overflow), and crash or halt the wallet's transaction/coin-sync processing path — the same abstract bug class as the Ox `sax_parse` stack-based overflow triggered by unbounded recursive parsing of untrusted input.

This is distinct from the Rust CLVM deserializer (`chia_rs.run_chia_program`, used by `Program.from_bytes`) and from `sha256_treehash`, both of which the project explicitly hardened to be non-recursive/iterative specifically to avoid "blowing out the python stack" [2](#0-1) . `from_memo()` was not given the same treatment.

### Impact Explanation
Any user, pool participant, or offer counterparty who can cause a coin (with a crafted memo) to be created and later observed by a wallet that uses the custody/MIPS architecture (custody wallets, plotnft/pool wallets built on `PuzzleWithRestrictions`) can trigger unbounded recursion purely from parsing coin data, with no CLVM execution/cost limit involved (this happens entirely in Python, outside mempool cost accounting). This is a spend/coin-triggered transaction-processing halt on the victim wallet: syncing crashes or hangs when it encounters the coin, denying that wallet the ability to process transactions/coins normally. Severity is Medium, consistent with the CVSS of the analogous Ox advisory (local, no confidentiality/integrity impact, but availability impact is high for the affected process).

### Likelihood Explanation
Likelihood is moderate: the attacker only needs to get a coin with a maliciously deep memo observed by a victim's wallet (e.g., via an offer, direct payment, or pool/plotnft interaction) — no special privileges are required, and constructing the deeply nested `MofNHint`/`Program` memo is straightforward. The primary gating factor is that the victim must be running wallet code that actually calls `from_memo()` on unknown custody puzzles (custody/MIPS or plotnft-based wallets), rather than every chia wallet.

### Recommendation
Convert `PuzzleWithRestrictions.from_memo()` (and its `fill_in_unknown_puzzles`/other recursive helpers over `MofN` trees) to an iterative, explicit-stack algorithm similar to `sha256_treehash`'s non-recursive design, and/or enforce an explicit maximum nesting depth (and a maximum total memo size/node count) before or during parsing, rejecting memos that exceed the bound instead of recursing arbitrarily deep.

### Proof of Concept
Conceptually (cannot be executed here, but derivable from the code path):
1. Build a `MofNHint` memo whose `member_memos` list contains a single `PuzzleWithRestrictions` memo that is itself an `MofN` memo, and repeat this nesting N times (N large, e.g. 5,000), using `PuzzleWithRestrictions.memo()` to serialize each level.
2. Place the resulting `Program` as the memo of a `CREATE_COIN` condition in a spend bundle, creating a coin owned by/observed by the victim wallet.
3. When the victim wallet syncs and calls `PuzzleWithRestrictions.from_memo()` on this coin's memo (as documented as the intended on-chain sync mechanism), the recursive calls in `from_memo` (chia/wallet/puzzles/custody/custody_architecture.py:283-286) exceed Python's recursion limit, raising `RecursionError` and halting that wallet's sync/spend processing for the affected coin path.

Note: I was not able to execute this PoC or trace every call site that automatically invokes `from_memo()` during wallet sync (only test and driver call sites were located via search), so the exact reachability from unmodified stock wallet sync code (versus explicit custody-wallet code paths) could not be fully confirmed within the available indexed context.

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

**File:** chia/types/blockchain_format/tree_hash.py (L1-7)
```python
"""
This is an implementation of `sha256_treehash`, used to calculate
puzzle hashes in clvm.

This implementation goes to great pains to be non-recursive so we don't
have to worry about blowing out the python stack.
"""
```
