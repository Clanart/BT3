### Title
Out-of-bounds index / unhandled `IndexError` in CLVM canonical-encoding length-prefix parsing - ([File: chia/full_node/mempool_manager.py])

### Summary
`is_atom_canonical()` in the mempool's CLVM-canonicalization check decodes a multi-byte atom length prefix by repeatedly indexing into the raw `clvm_buffer` without ever verifying that the buffer actually contains as many bytes as the prefix claims to need. This is the same bug class as CVE-2017-14227: a length value taken from attacker-supplied serialized data is used to walk/read a buffer without validating it against the buffer's actual remaining size, producing an out-of-bounds read.

### Finding Description
`is_atom_canonical` computes `prefix_len` (0–5) purely from the leading byte pattern of `clvm_buffer[offset]`, then loops `prefix_len` times reading `clvm_buffer[offset]` after incrementing `offset` each time, with no bounds check against `len(clvm_buffer)`: [1](#0-0) 

If the buffer is truncated so that fewer than `prefix_len` bytes remain after the leading byte (e.g., the multi-byte length-prefix marker `0b11111100` appears as the last byte, or near the end, of `clvm_buffer`), the loop indexes past the end of `clvm_buffer`, which in Python raises an unhandled `IndexError`. This mirrors the MongoDB `bson_iter_codewscope`/`bson_utf8_validate` flaw: a length taken from the wire format is trusted and used to read/scan memory beyond what the buffer contains, because there is no upfront comparison of the declared length against the remaining buffer size (contrast with the bounds checks elsewhere in the codebase, e.g. `skip_bytes`/`skip_list` in `chia/full_node/full_block_utils.py`, which explicitly check `n > len(buf)` before consuming): [2](#0-1) 

`is_atom_canonical` is invoked from `is_clvm_canonical`, which walks an entire CLVM-serialized buffer atom-by-atom to determine whether it uses canonical (minimal) length encodings, rejecting non-canonical or back-reference-containing serializations: [3](#0-2) 

This canonicalization check operates directly on the raw byte serialization of CLVM data associated with a spend bundle (puzzle reveal / solution) as part of mempool processing, i.e., on attacker-controlled input submitted by any unprivileged spend-bundle submitter.

### Impact Explanation
A malformed but otherwise structurally-plausible CLVM buffer (crafted so that a multi-byte atom length-prefix marker appears with insufficient trailing bytes) causes an unhandled `IndexError` inside `is_atom_canonical`/`is_clvm_canonical`. If this exception propagates unhandled through mempool spend-bundle admission, it can abort or crash the processing of that request/task, constituting a spend-triggered transaction-processing halt — the same "availability-only" impact class as the original CVE (CVSS `C:N/I:N/A:H`).

### Likelihood Explanation
Any unprivileged party who can submit a spend bundle to a full node's mempool can supply a puzzle reveal/solution whose serialized CLVM bytes are truncated at a multi-byte atom length prefix, directly triggering the code path shown above with no special privileges required. The exact call site that feeds untrusted spend-bundle bytes into `is_clvm_canonical` during mempool admission could not be fully traced within the available indexed content (only the definition and test references were located, not every consuming call site), so full end-to-end reachability from `MempoolManager` request handling should be confirmed by a source-level review of `mempool_manager.py` in a live checkout.

### Recommendation
Add explicit bounds checks in `is_atom_canonical` before each buffer read, e.g., verify `offset + prefix_len < len(clvm_buffer)` (or check remaining length before entering the loop) and return a safe "not canonical" / raise a well-defined validation error instead of allowing Python's raw `IndexError` to propagate. Ensure any caller of `is_clvm_canonical` during mempool admission catches and converts such errors into a standard spend-bundle validation failure (e.g., `Err.INVALID_SPEND_BUNDLE`) rather than letting an unhandled exception affect request processing.

### Proof of Concept
```python
from chia.full_node.mempool_manager import is_clvm_canonical

# Marker byte 0b11111100 declares a 5-byte length prefix (prefix_len = 5),
# but the buffer ends immediately after it.
malformed_buf = bytes([0b11111100])

is_clvm_canonical(malformed_buf)  # raises IndexError instead of returning False/handling gracefully
```

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
