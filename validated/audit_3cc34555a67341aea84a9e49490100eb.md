### Title
Unbounded buffer indexing in CLVM canonical-encoding check causes unhandled `IndexError` on malformed spend-bundle solutions - (File: `chia/full_node/mempool_manager.py`)

### Summary
`is_atom_canonical()` and `is_clvm_canonical()` in `chia/full_node/mempool_manager.py` decode attacker-controlled CLVM atom length prefixes taken directly from a submitted spend bundle's solution bytes and use them to advance a byte-buffer offset with **no bounds check against the buffer's actual length**, unlike every other untrusted-length parser in this codebase (e.g. `skip_bytes`/`skip_list` in `chia/full_node/full_block_utils.py`, which explicitly validate `n > len(buf)` before indexing). This mirrors the RIOT-OS IPHC bug class: a length field parsed from untrusted input is trusted to compute an offset/size used for further buffer access without validating it fits the actual buffer.

### Finding Description
`is_atom_canonical(clvm_buffer, offset)` reads a length-prefix byte at `clvm_buffer[offset]`, determines `prefix_len` (0-5 additional bytes) and then loops: [1](#0-0) 

```
atom_len = b & mask
for i in range(prefix_len):
    atom_len <<= 8
    offset += 1
    atom_len |= clvm_buffer[offset]
return 1 + prefix_len + atom_len, atom_len >= min_value
```

There is no check that `offset` (after incrementing) stays within `len(clvm_buffer)` before the `clvm_buffer[offset]` read, and no check that `atom_len` (fully attacker-controlled, up to 2^40) plus the current offset stays within the buffer before the caller advances past it.

The caller, `is_clvm_canonical()`, walks the whole buffer using the returned `atom_len` to jump the offset forward without validating that `offset + atom_len <= len(clvm_buffer)`: [2](#0-1) 

Contrast this with the project's own bounds-checked pattern used elsewhere for untrusted length prefixes: [3](#0-2) 

Because `clvm_buffer` here is a Python `bytes` object, an out-of-bounds index raises a Python `IndexError` rather than causing raw memory corruption (Python bounds-checks `bytes` indexing, unlike the C buffer in RIOT-OS). The analogous impact in this codebase is therefore not memory corruption but an **unhandled exception during spend-bundle canonical-form validation**, which per project documentation is invoked "for DEDUP-eligible spends": [4](#0-3) 

An attacker-crafted spend-bundle solution containing a truncated/oversized atom length prefix at the tail of the buffer can drive `offset` past `len(clvm_buffer) - 1` or make `atom_len` so large that any subsequent slicing/consumption logic operates on a length far exceeding the real buffer, triggering an unhandled `IndexError`/`ValueError` in the mempool add/dedup-eligibility path that a single unprivileged spend-bundle submitter can reach.

### Impact Explanation
If this exception is not caught somewhere up the mempool add call stack (I was unable to fully trace every caller of `is_clvm_canonical` within the available context to confirm whether a blanket `try/except` wraps this path), it would propagate out of spend-bundle processing, halting or crashing the mempool worker/task handling that submission — a spend-triggered transaction-processing halt, which is an explicitly in-scope, high-severity impact category for this analysis (denial of service against mempool admission, analogous to RIOT-OS's unhandled hard fault on malformed 6LoWPAN input).

### Likelihood Explanation
Likelihood is high for reachability (any unprivileged party can submit a spend bundle with a hand-crafted solution triggering DEDUP-eligibility checks), but I could not fully confirm from the available index whether an enclosing exception handler already neutralizes the crash before it affects node availability — this should be verified directly in the surrounding mempool-add call chain.

### Recommendation
Add explicit bounds validation in `is_atom_canonical()` (offset increments must stay `< len(clvm_buffer)`) and in `is_clvm_canonical()` (the returned `atom_len`/next offset must not exceed `len(clvm_buffer)`), returning `False`/raising a well-typed, caught validation error instead of relying on implicit Python `IndexError`s, mirroring the explicit remaining-buffer checks already used in `chia/full_node/full_block_utils.py`.

### Proof of Concept
Construct a spend bundle solution buffer ending in a large-atom length-prefix byte (e.g. `0xF8`-class, prefix_len=4) followed by fewer than 4 trailing bytes, then submit it as a coin spend solution that is eligible for DEDUP checking; `is_atom_canonical`/`is_clvm_canonical` will attempt to read past the end of `clvm_buffer`, raising an unhandled `IndexError` during mempool admission of that spend bundle.

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

**File:** chia/full_node/mempool_manager.py (L212-227)
```python
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

**File:** chia/full_node/full_block_utils.py (L27-34)
```python
def skip_bytes(buf: memoryview) -> memoryview:
    if len(buf) < 4:
        raise ValueError(f"byte length prefix requires 4 bytes, remaining buffer {len(buf)}")
    n = int.from_bytes(buf[:4], "big", signed=False)
    buf = buf[4:]
    if n > len(buf):
        raise ValueError(f"byte length {n} exceeds remaining buffer {len(buf)}")
    return buf[n:]
```

**File:** .cursor/context/clvm-execution.md (L96-118)
```markdown
## Canonical serialization

**Location**: `mempool_manager.py:185`

### `is_clvm_canonical(clvm_buffer)`

Checks that a CLVM program uses shortest-form atom encoding:

- No unnecessary length prefix bytes
- No back-references (`0xFE` byte)
- No trailing garbage

### When enforced

Required for DEDUP-eligible spends. Without canonical form, identical
solutions could have different serializations, breaking dedup.

### `is_atom_canonical(clvm_buffer, offset)`

Validates a single atom's length prefix encoding. The CLVM format uses
variable-length prefixes (1-6 bytes) based on atom size. Each prefix
length has a minimum atom size threshold.

```
