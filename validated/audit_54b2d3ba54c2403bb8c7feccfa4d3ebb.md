### Title
Out-of-bounds read in `is_atom_canonical` / `is_clvm_canonical` via crafted spend bundle solution/generator bytes - (File: chia/full_node/mempool_manager.py)

### Summary
`is_atom_canonical` and `is_clvm_canonical` in `chia/full_node/mempool_manager.py` walk a raw `clvm_buffer` and index it (`clvm_buffer[offset]`) based on length-prefix values decoded from attacker-controlled bytes, without ever validating that `offset` stays within `len(clvm_buffer)`. This mirrors the CVE-2016-7524 pattern (parser reads a length/tag byte and advances a cursor derived from untrusted data without a bounds check, causing an out-of-bounds read on a crafted input).

### Finding Description [1](#0-0) 

`is_atom_canonical(clvm_buffer, offset)` reads `b = clvm_buffer[offset]` and then, depending on the high bits of `b`, decides how many additional prefix bytes (`prefix_len`, up to 5) to consume by looping `offset += 1; atom_len |= clvm_buffer[offset]` [2](#0-1) . Nothing checks that `offset + prefix_len < len(clvm_buffer)` before these reads.

`is_clvm_canonical(clvm_buffer)` drives this loop over the whole buffer, advancing `offset` by `atom_len` returned from `is_atom_canonical`, again with no check that `offset` remains within buffer bounds before the next `clvm_buffer[offset]` read on the next loop iteration [3](#0-2) .

A crafted CLVM-serialized buffer where a length-prefix byte near the end of the buffer indicates more prefix/atom bytes than actually remain (e.g. `0xFC` type byte with insufficient trailing bytes, or a long atom length that skips `offset` past the end of the buffer for the next iteration) triggers an `IndexError` when Python bytes indexing goes out of range.

### Impact Explanation
Because this is Python `bytes` indexing (not raw memory), the out-of-bounds access raises an uncaught `IndexError` rather than causing memory corruption or leaking process memory — the direct consequence in this codebase is a crash/exception (functionally a DoS on the code path that invokes canonical-form checking), not memory disclosure as in the original C `meta.c` OOB-read bug. Whether this rises to a "Medium" reachable-and-impactful bug depends on whether an uncaught `IndexError` from this helper can propagate out of a request-handling coroutine and halt mempool spend-bundle processing versus being caught by a broader error handler; I could not fully confirm the calling context (`is_clvm_canonical`'s only production call sites) within the available iterations, since search results only showed usages inside `chia/_tests/core/mempool/test_mempool_manager.py` and no confirmed production call site was located in the remaining search budget.

### Likelihood Explanation
Unable to fully verify given remaining tool budget: without confirming that `is_clvm_canonical`/`is_atom_canonical` are invoked on attacker-supplied `clvm_buffer` data during ordinary spend-bundle mempool admission (e.g., DEDUP eligibility checks referenced near this code, per `.cursor/context/clvm-execution.md`), I cannot state with confidence that this is reachable from a single unprivileged spend-bundle submission in production, only that the parsing logic itself lacks bounds checks.

### Recommendation
Add explicit bounds checks in `is_atom_canonical` (verify `offset + prefix_len < len(clvm_buffer)` before each indexed read) and in `is_clvm_canonical`'s main loop (verify `offset < len(clvm_buffer)` before each `clvm_buffer[offset]` read), raising a controlled `ValueError`/returning `False` instead of allowing an `IndexError` to propagate.

### Proof of Concept
Conceptual PoC (not confirmed against a live call path): call `is_clvm_canonical(bytes([0xFC]))` or a buffer whose final atom's declared multi-byte length prefix (e.g. `0xFC` requiring 5 more prefix bytes) is truncated — `is_atom_canonical` will attempt `clvm_buffer[offset]` past the buffer end and raise `IndexError`, e.g.:
```python
from chia.full_node.mempool_manager import is_clvm_canonical
is_clvm_canonical(bytes([0xFC, 0x00]))  # insufficient trailing bytes -> IndexError
```

**Note:** I was not able to confirm the exact production call site that feeds unsanitized, attacker-controlled bytes into `is_clvm_canonical`/`is_atom_canonical` within the available search iterations (only test references were surfaced besides the two internal call sites in `mempool_manager.py` itself). If a Devin session with full codebase access is available, this should be verified by tracing all callers of `is_clvm_canonical` in `chia/full_node/mempool_manager.py` to confirm whether it is invoked on the raw solution/generator bytes of an incoming `SpendBundle` before or during mempool admission.

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
