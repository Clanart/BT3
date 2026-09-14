### Title
Unbounded buffer read in `is_atom_canonical`/`is_clvm_canonical` on attacker-controlled spend data — spend-triggered mempool processing crash - (`chia/full_node/mempool_manager.py`)

### Summary
The CVE describes an out-of-bounds read in monkey's `mk_ptr_to_buf` caused by insufficient bounds checking when parsing attacker-supplied buffer data from a crafted HTTP request, leading to a DoS. The Chia analog is the canonical-CLVM-serialization checker in the mempool admission path, which walks a `bytes` buffer using computed offsets without ever validating the offset against the buffer length before indexing into it.

### Finding Description
`is_atom_canonical()` reads `clvm_buffer[offset]` in a loop driven by `prefix_len` (up to 5 extra bytes) without checking that `offset` stays within `len(clvm_buffer)`: [1](#0-0) 

`is_clvm_canonical()` calls this in a `while True` loop, advancing `offset` by the returned `atom_len`/prefix length and re-indexing `clvm_buffer[offset]` on the next iteration, again with no bounds check before dereferencing: [2](#0-1) 

Both functions are invoked directly on attacker-controlled bytes — the raw `puzzle_reveal` and `solution` of every `coin_spend` in a submitted `SpendBundle` — inside `MempoolManager.validate_spend_bundle()`, which runs during ordinary mempool admission of a spend bundle from any unprivileged submitter: [3](#0-2) 

A puzzle reveal or solution can be crafted so a declared atom-length prefix (e.g. a 5-byte-prefix atom claiming a huge `atom_len`, or a truncated multi-byte prefix at the very end of the buffer) causes `offset` to run past `len(clvm_buffer) - 1` before the bounds-less indexing occurs, raising an unhandled `IndexError` inside pure-Python code that is not guarded by any `try/except` at the call site in `validate_spend_bundle()`.

### Impact Explanation
This is directly reachable from a single unauthenticated spend-bundle submission (mempool `push_tx` / gossip), matching the CVE's "crafted request causes OOB read → DoS" bug class. In Python, the "out-of-bounds read" manifests as an unhandled `IndexError` rather than memory corruption, but since `is_clvm_canonical()` is called unconditionally and without exception handling as part of every coin-spend's admission check, a crafted spend bundle can throw an exception mid-way through `validate_spend_bundle()`. If this exception is not caught further up the call chain (I was unable to fully confirm a blanket `except Exception` wrapper around the full-node's transaction-processing coroutine within the available investigation), it would abort processing of that spend bundle/task, representing a spend-triggered transaction-processing halt — one of the explicitly accepted impact categories.

### Likelihood Explanation
High reachability: any wallet/spend-bundle submitter can trigger `validate_spend_bundle()` with a crafted `puzzle_reveal`/`solution`, no special privileges or peer trust required, and the vulnerable functions are always invoked during standard canonical-CLVM checks before any cost-based execution gating.

### Recommendation
Add explicit bounds checks in `is_atom_canonical()`/`is_clvm_canonical()` before every `clvm_buffer[offset]` access (verifying `offset < len(clvm_buffer)` prior to reading the prefix-length bytes and prior to reading the next token byte), returning "not canonical" or raising a well-defined `ConsensusError`/`Err.INVALID_CONDITION` instead of allowing a raw `IndexError` to propagate, and ensure the call site in `validate_spend_bundle()` treats any parsing failure as `Err.INVALID_COIN_SOLUTION` rather than an uncaught exception.

### Proof of Concept
Construct a `CoinSpend` whose `puzzle_reveal` (or `solution`) bytes end with a truncated multi-byte atom-length prefix, e.g. a single trailing byte `0xFC` (which per `is_atom_canonical()`'s branch table declares a 5-byte-prefix atom, `prefix_len = 5`) with fewer than 5 following bytes actually present in the buffer:
```python
# buffer ends right after the 0xFC marker, with no length bytes following
solution_bytes = b"\xff\xfc"          # pair marker + truncated 5-byte-prefix atom marker
```
Submitting a `SpendBundle` containing this `coin_spend` causes `validate_spend_bundle()` to call `is_clvm_canonical(bytes(coin_spend.solution))`, which calls `is_atom_canonical()`; the loop `atom_len |= clvm_buffer[offset]` (`chia/full_node/mempool_manager.py:181`) indexes past the end of the 2-byte buffer, raising an unhandled `IndexError` during spend-bundle admission processing. This matches the "unable to confirm full exception-handling coverage" caveat noted above — a background Devin session with access to the running full node would be needed to confirm whether an outer handler currently prevents this from becoming a full processing-halt DoS in the deployed configuration.

### Citations

**File:** chia/full_node/mempool_manager.py (L144-183)
```python
def is_atom_canonical(clvm_buffer: bytes, offset: int) -> tuple[int, bool]:
    b = clvm_buffer[offset]
    if (b & 0b11000000) == 0b10000000:
        # 6 bits length prefix
        mask = 0b00111111
        prefix_len = 0
        min_value = 1
    elif (b & 0b11100000) == 0b11000000:
        # 5 + 8 bits length prefix
        mask = 0b00011111
        prefix_len = 1
        min_value = 1 << 6
    elif (b & 0b11110000) == 0b11100000:
        # 4 + 8 + 8 bits length prefix
        mask = 0b00001111
        prefix_len = 2
        min_value = 1 << (5 + 8)
    elif (b & 0b11111000) == 0b11110000:
        # 3 + 8 + 8 + 8 bits length prefix
        mask = 0b00000111
        prefix_len = 3
        min_value = 1 << (4 + 8 + 8)
    elif (b & 0b11111100) == 0b11111000:
        # 2 + 8 + 8 + 8 + 8 bits length prefix
        mask = 0b00000011
        prefix_len = 4
        min_value = 1 << (3 + 8 + 8 + 8)
    elif (b & 0b11111110) == 0b11111100:
        # 1 + 8 + 8 + 8 + 8 + 8 bits length prefix
        mask = 0b00000001
        prefix_len = 5
        min_value = 1 << (2 + 8 + 8 + 8 + 8)

    atom_len = b & mask
    for i in range(prefix_len):
        atom_len <<= 8
        offset += 1
        atom_len |= clvm_buffer[offset]

    return 1 + prefix_len + atom_len, atom_len >= min_value
```

**File:** chia/full_node/mempool_manager.py (L186-227)
```python
def is_clvm_canonical(clvm_buffer: bytes) -> bool:
    """
    checks whether the CLVM serialization is all canonical representation.
    atoms can be serialized in more than one way by using more bytes than
    necessary to encode the length prefix. This functions ensures that all atoms are
    encoded with the shortest representation. back-references are not allowed
    and will make this function return false
    """
    assert clvm_buffer != b""

    offset = 0
    tokens_left = 1
    while True:
        b = clvm_buffer[offset]

        # pair
        if b == 0xFF:
            tokens_left += 1
            offset += 1
            continue

        # back references cannot be considered canonical, since they may be
        # encoded in many different ways
        if b == 0xFE:
            return False

        # small atom or NIL
        if b <= 0x80:
            tokens_left -= 1
            offset += 1
        else:
            atom_len, canonical = is_atom_canonical(clvm_buffer, offset)
            if not canonical:
                return False
            tokens_left -= 1
            offset += atom_len

        if tokens_left == 0:
            break

    # if there's garbage at the end, it's not canonical
    return offset == len(clvm_buffer)
```

**File:** chia/full_node/mempool_manager.py (L723-726)
```python
            if not is_clvm_canonical(bytes(coin_spend.puzzle_reveal)) or not is_clvm_canonical(
                bytes(coin_spend.solution)
            ):
                return Err.INVALID_COIN_SOLUTION, None, []
```
