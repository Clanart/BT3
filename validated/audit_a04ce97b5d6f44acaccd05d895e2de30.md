Based on my investigation, I found `PuzzleWithRestrictions.from_memo()` — a genuine unbounded-recursion parser that reconstructs a custody-puzzle structure from attacker/counterparty-supplied memo data, directly analogous to the HAProxy `dns.c` bug class (self-referential/chained compressed pointers causing unbounded recursion → stack exhaustion).

### Title
Unbounded recursion in `PuzzleWithRestrictions.from_memo()` parsing untrusted MofN custody memos causes stack exhaustion - (File: `chia/wallet/puzzles/custody/custody_architecture.py`)

### Summary
`PuzzleWithRestrictions.from_memo()` recursively parses a CLVM `Program` memo describing a MIPS custody puzzle (member/restriction hints, and nested `MofN` structures) with no depth limit, mirroring the CVE-2018-20103 bug class where a compressed pointer/parser follows attacker-controlled nesting indefinitely, exhausting the call stack.

### Finding Description
`from_memo()` reads a memo `Program`, and when `further_branching` is true it recurses once per member: `MofN(m=m_of_n_hint.m, members=[PuzzleWithRestrictions.from_memo(memo) for memo in m_of_n_hint.member_memos])` [1](#0-0) . Each `MofNHint.from_program()` call simply iterates the attacker-controlled `member_memos` list from the CLVM program without any bound on nesting depth [2](#0-1) . Since `Program` here is parsed via the fast Rust CLVM path with no recursion-depth restriction imposed at this layer (`Program.from_bytes()` / `.at()` / `.as_iter()`) [3](#0-2) , a memo can encode thousands of nested single-member `MofN` wrappers (`further_branching=1` chained indefinitely), each contributing one Python stack frame to `from_memo()`. This is architecturally identical to the DNS compressed-pointer chain in the CVE: a compact, attacker-controlled structure that forces the parser into deep/unbounded recursion.

Other CLVM-adjacent code in this repo explicitly defends against this exact bug class — e.g., `sha256_treehash()` is deliberately implemented iteratively "so we don't have to worry about blowing out the python stack" [4](#0-3) , and the DataLayer merkle-tree ingestion path (`insert_into_data_store_from_file`) is explicitly guarded by Rust-side `RecursionDepthExceededError`/`CycleFoundError` checks, as proven by tests parametrized up to depth 100,000 [5](#0-4) . No equivalent guard exists for `from_memo()`.

### Impact Explanation
`from_memo()` is reachable wherever custody puzzle hints are received from an untrusted counterparty and reconstructed client-side — e.g., a wallet receiving a coin with a custody-architecture puzzle hint/memo, or during an offer/trade involving a MIPS-custody-controlled coin. A crafted memo can drive the recursive call stack to exhaustion, crashing the wallet process handling that memo (denial of service for the local wallet process). This matches the report's accepted impact category of "a spend-triggered transaction-processing halt" since parsing untrusted hints/memos as part of normal coin-state processing is on the reachable path from a spend a counterparty controls.

### Likelihood Explanation
Likelihood is moderate to high for any wallet code path that calls `PuzzleWithRestrictions.from_memo()` on hints derived from received coins/offers without a depth or size cap; the memo is fully attacker-constructed CLVM data, and constructing a deeply nested single-member `MofN` chain (`further_branching=1` at every level) is cheap and byte-efficient (analogous to DNS pointer chains being compact yet triggering large recursion).

### Recommendation
Add an explicit maximum-depth (or maximum-node-count) check inside `from_memo()`/`MofNHint.from_program()` before recursing, and/or convert the recursive walk into an iterative one using an explicit stack, similar to the existing iterative `sha256_treehash()` pattern and the Rust `RecursionDepthExceededError` guard used in DataLayer ingestion.

### Proof of Concept
Construct a memo `Program` of the form:
```
(spec_namespace [nonce, [], 1, [1, [<memo_(n-1)>]]])
```
where `<memo_(n-1)>` recursively nests another single-member `MofNHint` (`further_branching=1`, `m=1`, one member memo) N times, for N in the tens-of-thousands range (each level adds only a few bytes). Calling `PuzzleWithRestrictions.from_memo(deeply_nested_memo)` will recurse N times through `from_memo()` → `MofN(...)` → list-comprehension recursive call, exhausting the Python call stack and raising `RecursionError`, crashing whatever wallet process/task is processing that memo.

**Note on confidence**: I was unable to fully trace every call site that invokes `from_memo()` with externally-supplied data (e.g., exact wallet RPC/offer code path that feeds coin hints into this function) within the available indexed context; a Devin session with full repository access would be needed to confirm the exact externally-reachable trigger point and any existing depth guard elsewhere in the call chain.

### Citations

**File:** chia/wallet/puzzles/custody/custody_architecture.py (L147-161)
```python
@dataclass(kw_only=True, frozen=True)
class MofNHint:
    m: int
    member_memos: list[Program]

    def to_program(self) -> Program:
        return Program.to([self.m, self.member_memos])

    @classmethod
    def from_program(cls, prog: Program) -> MofNHint:
        m, member_memos = prog.as_iter()
        return MofNHint(
            m=m.as_int(),
            member_memos=list(member_memos.as_iter()),
        )
```

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

**File:** chia/types/blockchain_format/tree_hash.py (L1-7)
```python
"""
This is an implementation of `sha256_treehash`, used to calculate
puzzle hashes in clvm.

This implementation goes to great pains to be non-recursive so we don't
have to worry about blowing out the python stack.
"""
```

**File:** chia/_tests/core/data_layer/test_data_store.py (L1726-1765)
```python
@pytest.mark.anyio
@pytest.mark.parametrize("depth", [1000, 100_000])
async def test_insert_into_data_store_from_file_line_graph_depth(
    data_store: DataStore,
    store_id: bytes32,
    tmp_path: Path,
    depth: int,
) -> None:
    # Binary "line graph":
    #   1 -> leaf_1, 2
    #   2 -> leaf_2, 3
    #   ...
    #   depth -> leaf_depth, leaf_end
    #
    # This stresses depth/stack handling while staying strictly binary.
    #
    # Node IDs:
    # - internal nodes: 1..depth
    # - per-level leaf nodes: (depth+1)..(2*depth)
    # - final leaf: (2*depth+1)
    internal_first = 1
    internal_last = depth
    leaf_base = internal_last + 1
    leaf_end = (2 * depth) + 1

    edges: list[tuple[int, int]] = []
    for i in range(internal_first, internal_last):
        # Order matters: first edge is left child, second edge is right child.
        edges.append((i, leaf_base + (i - internal_first)))  # unique leaf for this internal node
        edges.append((i, i + 1))  # next internal node

    # last internal points to its leaf and a final leaf
    edges.append((internal_last, leaf_base + (internal_last - internal_first)))
    edges.append((internal_last, leaf_end))

    filename = tmp_path / f"line_graph_depth_{depth}.dat"
    root_hash = create_graph_util(filename, edges)

    with pytest.raises(chia_rs.datalayer.RecursionDepthExceededError):
        await data_store.insert_into_data_store_from_file(store_id, root_hash, filename)
```
