### Title
Out-of-bounds buffer read in CLVM atom-length-prefix parser during mempool dedup canonicalization check - (File: `chia/full_node/mempool_manager.py`)

### Summary
`is_atom_canonical()` and its caller `is_clvm_canonical()` in `chia/full_node/mempool_manager.py` parse variable-length CLVM atom length prefixes directly against attacker-supplied spend-bundle solution bytes without validating that the computed offsets stay within the buffer before indexing it, mirroring the CVE-2018-11433 bug class (a length-prefix parser that reads past the end of an attacker-controlled buffer).

### Finding Description
`is_atom_canonical(clvm_buffer, offset)` reads `b = clvm_buffer[offset]` [1](#0-0)  and then, based on the high bits of `b`, determines a variable `prefix_len` (0–5 additional bytes) and loops reading `clvm_buffer[offset]` for each additional prefix byte with no check that `offset < len(clvm_buffer)` at any point: [2](#0-1) 

The caller `is_clvm_canonical()` walks a whole CLVM-serialized buffer token by token, calling `is_atom_canonical()` for any byte greater than `0x80`, and also indexes `clvm_buffer[offset]` directly at the top of its loop with no length check either: [3](#0-2) . If the buffer is truncated so that a multi-byte length-prefix atom header starts near the very end of the buffer (e.g., a `0xFC`-class marker with 5 prefix bytes but only 1–4 bytes actually remaining), the prefix-reading loop walks `offset` past `len(clvm_buffer) - 1` and indexes out of range.

In C (the original CVE-2018-11433 context) this class of bug is a heap-based buffer over-read that discloses adjacent heap memory. In this Python implementation, out-of-range `bytes` indexing raises an `IndexError` instead of reading adjacent memory, so the practical failure mode here is an unhandled exception rather than a memory-disclosure read — but the root cause (insufficient bounds validation before indexing a length-prefixed buffer with attacker-controlled length fields) is the same defect class.

Per the architecture notes, this function is invoked as part of the DEDUP-eligibility determination for mempool spends: “Required for DEDUP-eligible spends. Without canonical form, identical solutions could have different serializations, breaking dedup.” [4](#0-3) . This buffer is derived from spend-bundle solution bytes that are fully controlled by whoever submits a spend bundle to the mempool.

### Impact Explanation
If `is_clvm_canonical()`/`is_atom_canonical()` is called with a crafted, truncated CLVM solution buffer whose trailing bytes look like the start of a long length-prefix atom, it raises an `IndexError` while mid-parsing. Whether this constitutes a real network-facing DoS depends on whether the call site in the mempool-item/dedup-eligibility path catches generic exceptions around this canonicalization check; I was not able to fully trace every call site and its exception handling within the available context/tool budget. At minimum this is a bounds-validation gap in a parser that operates directly on unauthenticated, attacker-supplied bytes at the mempool-admission boundary — exactly the class of defect the CVE describes, even though Python's memory safety changes the concrete consequence from information disclosure to an exception path. I flag this with reduced confidence because I could not conclusively confirm (within this session) that the exception is unhandled all the way up to a spend-triggered mempool halt.

### Likelihood Explanation
The inputs (spend solution bytes) are entirely attacker-controlled by any unprivileged spend-bundle submitter, and the affected code path performs no length validation before variable-depth indexing based on attacker-supplied high bits, so triggering the malformed-prefix condition is straightforward to construct deterministically.

### Recommendation
Add explicit bounds checks in `is_atom_canonical()` before each `clvm_buffer[offset]` access in the prefix-reading loop (verify `offset < len(clvm_buffer)` before every increment/index), and add the same check at the top of `is_clvm_canonical()`'s main loop before dereferencing `clvm_buffer[offset]`. Raise a well-defined error (or return `False`/non-canonical) on truncation instead of allowing an `IndexError` to propagate, and add regression tests with truncated multi-byte atom length prefixes.

### Proof of Concept
Construct a `clvm_buffer` ending in a length-prefix marker byte that requires 5 additional prefix bytes but is truncated to leave 0 trailing bytes, e.g. `bytes([0b11111101])` (the 1+40-bit prefix marker, `min_value = 1 << 40`) as the *last* byte of the buffer, with `tokens_left` still requiring evaluation of this atom. Calling `is_clvm_canonical(buffer)` on such a buffer drives `is_atom_canonical()` into its `for i in range(prefix_len)` loop where `offset` is incremented past the end of `clvm_buffer` and `clvm_buffer[offset]` raises `IndexError` [5](#0-4) .

### Citations

**File:** chia/full_node/mempool_manager.py (L144-145)
```python
def is_atom_canonical(clvm_buffer: bytes, offset: int) -> tuple[int, bool]:
    b = clvm_buffer[offset]
```

**File:** chia/full_node/mempool_manager.py (L177-183)
```python
    atom_len = b & mask
    for i in range(prefix_len):
        atom_len <<= 8
        offset += 1
        atom_len |= clvm_buffer[offset]

    return 1 + prefix_len + atom_len, atom_len >= min_value
```

**File:** chia/full_node/mempool_manager.py (L196-221)
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
```

**File:** .cursor/context/clvm-execution.md (L108-111)
```markdown
### When enforced

Required for DEDUP-eligible spends. Without canonical form, identical
solutions could have different serializations, breaking dedup.
```
