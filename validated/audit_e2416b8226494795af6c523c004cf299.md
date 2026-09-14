### Title
Out-of-bounds read / unhandled `IndexError` in mempool canonical-CLVM validation via crafted puzzle/solution bytes - ([File: chia/full_node/mempool_manager.py])

### Summary
`is_clvm_canonical()` and its helper `is_atom_canonical()` in `chia/full_node/mempool_manager.py` walk a raw `puzzle_reveal`/`solution` byte buffer using an `offset` counter that is advanced according to length-prefix bytes read from the buffer itself, without ever validating that `offset` (or `offset + prefix_len`) stays within `len(clvm_buffer)`. This mirrors the FontForge `readcffset` bug class in CVE-2017-11574: a length/offset value taken from attacker-controlled input is trusted to index into a buffer without a bounds check, leading to an out-of-bounds read. Since Python raises an `IndexError` on out-of-range byte access rather than corrupting memory, the practical effect here is an unhandled exception rather than code execution, but the reachable, attacker-controlled trigger path is directly analogous.

### Finding Description
`is_atom_canonical()` computes `prefix_len` from the leading atom byte and then loops: [1](#0-0) 
reading `clvm_buffer[offset]` for `prefix_len` additional bytes with no check that these offsets are still inside the buffer.

`is_clvm_canonical()`'s outer loop has the same problem at the top level: it repeatedly reads `b = clvm_buffer[offset]` and only terminates the walk based on a `tokens_left` counter derived from the bytes seen so far, never checking `offset < len(clvm_buffer)` before dereferencing: [2](#0-1) 

Both `puzzle_reveal` and `solution` for every coin spend in a submitted `SpendBundle` are passed unconditionally into `is_clvm_canonical()` during mempool admission, before any other structural validation of those buffers occurs in this function: [3](#0-2) 

A crafted buffer such as `b"\xff\xff\xff"` (three pair markers with no terminating atoms) or a multi-byte atom-length-prefix byte (e.g. `0xFC`) with insufficient trailing bytes will cause `offset` to walk past `len(clvm_buffer)`, and the next `clvm_buffer[offset]` access raises an uncaught Python `IndexError`.

### Impact Explanation
This function is invoked synchronously inside `MempoolManager.validate_spend_bundle()`, which runs on every incoming spend bundle during mempool admission — reachable by any unprivileged spend-bundle submitter. An uncaught `IndexError` propagating out of `validate_spend_bundle()` is not handled by an explicit `try/except` at this call site, so a single malformed but otherwise well-formed-looking spend bundle can throw an exception mid-validation instead of returning a controlled `Err`. Depending on how far up the call stack this propagates uncaught, this can disrupt processing of that mempool-admission call (a spend-triggered transaction-processing halt for that request), which is the class of impact the audit scope calls out as acceptable (mempool admission halt via unsigned/uncontrolled attacker input).

### Likelihood Explanation
High reachability: any spend bundle submitter fully controls the `puzzle_reveal` and `solution` bytes of a `CoinSpend`, and `is_clvm_canonical()` is called on both unconditionally, before cost/CLVM execution validates the structure. Crafting a buffer with a trailing pair marker (`0xff`) or a truncated multi-byte atom length prefix is trivial and requires no special privileges. The main uncertainty is whether some outer async/task boundary (e.g., a broad `try/except Exception` in the RPC or node message-handling layer) catches this and only logs it rather than causing a more disruptive effect — this could not be fully confirmed within the indexed context, so the exact blast radius (single-request failure vs. broader disruption) is not proven here.

### Recommendation
Add explicit bounds checks before each indexed read in both `is_atom_canonical()` and `is_clvm_canonical()`:
- In `is_atom_canonical()`, verify `offset + prefix_len < len(clvm_buffer)` before entering the byte-accumulation loop, and raise/return a normal validation failure instead of indexing out of range.
- In `is_clvm_canonical()`'s main loop, check `offset < len(clvm_buffer)` before each `clvm_buffer[offset]` read, and treat an out-of-range condition as "not canonical" (return `False`) rather than letting Python raise.
- Ensure `validate_spend_bundle()` (or its caller) treats any exception from this canonicality check as `Err.INVALID_COIN_SOLUTION` rather than letting it propagate as an unhandled exception.

### Proof of Concept
Submit a `SpendBundle` whose `CoinSpend.solution` (or `.puzzle_reveal`) serializes to a buffer such as:
```
b"\xff\xff\xff"
```
This is fed to `is_clvm_canonical()` via: [3](#0-2) 
Each `\xff` increments `tokens_left` and `offset` without ever encountering a terminating atom byte before `offset` reaches `len(clvm_buffer)`. On the next loop iteration, `b = clvm_buffer[offset]` (line ~199) raises `IndexError: index out of range` because `offset == len(clvm_buffer)`, which is not caught by `validate_spend_bundle()`.

### Citations

**File:** chia/full_node/mempool_manager.py (L177-183)
```python
    atom_len = b & mask
    for i in range(prefix_len):
        atom_len <<= 8
        offset += 1
        atom_len |= clvm_buffer[offset]

    return 1 + prefix_len + atom_len, atom_len >= min_value
```

**File:** chia/full_node/mempool_manager.py (L196-227)
```python
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
