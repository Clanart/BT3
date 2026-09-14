### Title
Unbounded-index CLVM atom-length-prefix parser in `is_atom_canonical()` can raise an uncaught `IndexError` on attacker-supplied spend-bundle CLVM, halting mempool spend-bundle processing - (File: `chia/full_node/mempool_manager.py`)

### Summary
`is_atom_canonical()` and its caller `is_clvm_canonical()` in `chia/full_node/mempool_manager.py` implement a hand-rolled CLVM atom-length-prefix parser that walks a raw `bytes` buffer with manual offset arithmetic and no bounds checks on the multi-byte length-prefix loop or on the post-atom offset advance, exactly the bug class described in CVE‑2020‑21831 (`read_2004_section_handles` reading a length/handle table with insufficient bounds checking). This code is invoked on every submitted spend bundle's `puzzle_reveal` and `solution` bytes during mempool admission, i.e. it is directly reachable by an unprivileged spend-bundle submitter. [1](#0-0) [2](#0-1) 

### Finding Description
`is_atom_canonical(clvm_buffer, offset)` classifies the current byte to determine `prefix_len` (0-5 extra length-prefix bytes), then loops `prefix_len` times, each iteration doing `offset += 1; atom_len |= clvm_buffer[offset]` with no check that `offset` remains `< len(clvm_buffer)`: [3](#0-2) 

The returned `1 + prefix_len + atom_len` is then used by the caller `is_clvm_canonical()` to advance `offset` by the *claimed* atom length, again without verifying that `offset` stays within the buffer, before looping back to the top of the `while True:` loop and re-indexing `clvm_buffer[offset]`: [4](#0-3) 

This is structurally the same bug class as the referenced LibreDWG issue: a length/handle value read from untrusted input is used to advance a cursor into a buffer with no bound check against the buffer's actual remaining size, and the next read uses that unchecked offset. In C this yields a heap OOB read/write; in this Python implementation it yields an unguarded `bytes.__getitem__` call that raises `IndexError` if the offset walks past the end of the buffer. Because the two length-accounting schemes (this ad-hoc FSM vs. the Rust CLVM deserializer that already accepted `coin_spend.puzzle_reveal`/`coin_spend.solution` as valid `SerializedProgram` bytes) are independently implemented, any byte pattern that the FSM misclassifies (e.g. a length-prefix mask boundary the FSM interprets differently, or a maliciously large-but-otherwise-"valid" nested atom/pair sequence that causes tokens_left/offset bookkeeping to drift) can push `offset` beyond `len(clvm_buffer)` before the final `offset == len(clvm_buffer)` sanity check is reached, raising an unhandled `IndexError`.

Reachability: `is_clvm_canonical()` is called unconditionally (not only for DEDUP-eligible spends) on every coin spend's puzzle reveal and solution during `MempoolManager.validate_spend_bundle()`, which is on the hot path for admitting any spend bundle submitted by any peer/wallet/RPC client: [5](#0-4) 

### Impact Explanation
An uncaught `IndexError` raised from `is_clvm_canonical()` during `validate_spend_bundle()` breaks out of the normal `Err.INVALID_COIN_SOLUTION` error-handling path with an unexpected exception type. Depending on how far up the call stack this propagates (mempool admission task, RPC `push_tx` handler), this can manifest as a spend-triggered failure of transaction processing for that admission call — matching the "spend-triggered transaction-processing halt" impact category. It does not by itself grant coin theft, forged assets, or consensus divergence; the primary risk is availability/DoS of the mempool-admission code path for a single submitter-crafted spend bundle.

### Likelihood Explanation
Medium. Triggering it requires constructing byte sequences that (a) pass Rust's CLVM deserializer as valid serialized programs (since `puzzle_reveal`/`solution` are already `SerializedProgram` objects by the time `is_clvm_canonical` runs) while (b) causing the independent, hand-rolled Python FSM in `is_atom_canonical`/`is_clvm_canonical` to miscount offsets/tokens and walk past the buffer end. I was not able to fully confirm, with the tools available, a concrete byte sequence that satisfies both constraints simultaneously, nor whether a generic `except Exception` wrapper exists further up the `add_spend_bundle`/RPC call chain that would downgrade this to a harmless logged error rather than a task-crashing exception. This uncertainty should be resolved by a Devin session with full repository/test access before treating this as confirmed-exploitable.

### Recommendation
- Add explicit bounds checks in `is_atom_canonical()` before each `clvm_buffer[offset]` access (raise a handled `ValueError`/return `False` instead of relying on `IndexError`).
- Add a bounds check in `is_clvm_canonical()` after advancing `offset` by the atom length, before looping back to re-index the buffer.
- Wrap `is_clvm_canonical()`'s call sites in `validate_spend_bundle()` (and any other callers) to explicitly catch `IndexError`/`ValueError` and convert to `Err.INVALID_COIN_SOLUTION`, so malformed/adversarial encodings fail gracefully instead of raising an unexpected exception type.

### Proof of Concept
Conceptual (not independently verified end-to-end): craft a `puzzle_reveal` or `solution` blob that is valid enough to be accepted as a `SerializedProgram` by the Rust CLVM parser but contains an atom/pair byte sequence where the naive byte classification in `is_atom_canonical`/`is_clvm_canonical` (e.g., `b <= 0x80` vs `0xFF`/`0xFE` vs multi-byte-prefix ranges) diverges from the Rust parser's structural accounting, causing `offset` in `is_clvm_canonical()`'s `while True` loop to exceed `len(clvm_buffer)` and triggering an uncaught `IndexError` inside `MempoolManager.validate_spend_bundle()` when the spend bundle is submitted to the mempool. Confirming a concrete working byte sequence requires deeper testing against the actual Rust deserializer's acceptance criteria, which should be done in a follow-up session with test execution access.

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

**File:** chia/full_node/mempool_manager.py (L714-727)
```python
        bundle_coin_spends: dict[bytes32, BundleCoinSpend] = {}
        for coin_spend in new_spend.coin_spends:
            coin_id = coin_spend.coin.name()
            removal_names.add(coin_id)

            # if this coin_id isn't found, the SpendBundle doesn't match the
            # SpendBundleConditions.
            spend_conds = spend_conditions.pop(coin_id)

            if not is_clvm_canonical(bytes(coin_spend.puzzle_reveal)) or not is_clvm_canonical(
                bytes(coin_spend.solution)
            ):
                return Err.INVALID_COIN_SOLUTION, None, []

```
