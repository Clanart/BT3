### Title
Unbounded buffer read in `is_atom_canonical()` on attacker-controlled spend-bundle CLVM bytes - ([File: chia/full_node/mempool_manager.py])

### Summary
`is_atom_canonical()` reads a variable-length prefix out of a caller-supplied `clvm_buffer` and walks forward `prefix_len` bytes (up to 5) using an `offset` value that is *never checked against `len(clvm_buffer)`* before each dereference — the same bug class as the NTFS `ntfs_attr_find()` CVE, where an on-disk `attrs_offset` field was used to index into a buffer without first confirming it was within bounds. Here the "offset" is derived directly from the length-prefix nibble of untrusted CLVM bytes taken from a submitted spend bundle's `puzzle_reveal`/`solution`.

### Finding Description
`is_atom_canonical(clvm_buffer, offset)` decodes the atom length-prefix encoding used by CLVM serialization: [1](#0-0) 

For prefix forms with `prefix_len` up to 5 (the `0xFC`-class byte), the function loops:
```
for i in range(prefix_len):
    atom_len <<= 8
    offset += 1
    atom_len |= clvm_buffer[offset]
```
There is no check that `offset < len(clvm_buffer)` before each `clvm_buffer[offset]` access. If the supplied buffer ends right after a multi-byte length-prefix marker (e.g. a single `0xFC` byte with no trailing continuation bytes), the very first loop iteration indexes past the end of `clvm_buffer`, analogous to the NTFS code dereferencing `ATTR_RECORD` fields before validating `attrs_offset` is within the allocated record.

This function is invoked from `is_clvm_canonical()`, which is documented (`.cursor/context/clvm-execution.md`) as being required for DEDUP-eligible spend admission — i.e., it runs against `bytes(spend.puzzle_reveal)` / `bytes(spend.solution)` taken directly from an untrusted, network-submitted `SpendBundle` during mempool admission: [2](#0-1) 

Because Python `bytes`/`memoryview` indexing is bounds-checked, an out-of-range read raises `IndexError` rather than causing memory corruption like the C use-after-free in the original report — so the "use-after-free" primitive itself does not transfer. What does transfer is the root-cause pattern: **an untrusted length/offset field is used to walk further into a buffer without a bounds check before the dereference.**

### Impact Explanation
In Python this degrades from memory corruption to an unhandled `IndexError`. If that exception is not caught somewhere in the mempool admission call chain (e.g. in `pre_validate_spendbundle()` / dedup-eligibility checks), a crafted spend bundle with a truncated multi-byte atom length prefix could raise an uncaught exception during spend-bundle processing, which could disrupt mempool/transaction processing for the node handling it (a spend-triggered processing halt for that request/connection). This is a much weaker impact than the original CVE and is capped by Python's memory safety.

### Likelihood Explanation
Reachability is plausible — `puzzle_reveal`/`solution` bytes are fully attacker-controlled inputs to any submitted spend bundle, and the length-prefix decoding loop is easy to trigger with a short/truncated atom. However, I could not fully confirm from the available context whether the exact call site wraps `is_clvm_canonical()`/`is_atom_canonical()` in a broad `try/except`, which would downgrade this from a processing-halt DoS to a harmless rejected spend. This uncertainty should be resolved by inspecting the exact caller in `pre_validate_spendbundle()`/dedup-eligibility code before treating this as confirmed-exploitable.

### Recommendation
Add an explicit bounds check in `is_atom_canonical()` before each `clvm_buffer[offset]` access (e.g., `if offset >= len(clvm_buffer): return <sentinel>, False` or raise a handled `ValueError`), mirroring the bounds checks already present in sibling parsers like `skip_bytes()`/`skip_list()` in `chia/full_node/full_block_utils.py`, which explicitly validate lengths against the remaining buffer before consuming it: [3](#0-2) 

### Proof of Concept
```python
from chia.full_node.mempool_manager import is_clvm_canonical

# A single 0xFC byte signals a 5-byte trailing length prefix,
# but no trailing bytes are present.
clvm_buf = bytes.fromhex("fc")
is_clvm_canonical(clvm_buf)  # raises IndexError instead of returning False
```
This buffer, if used as (or embedded as an atom within) a spend bundle's `puzzle_reveal`/`solution` during DEDUP-eligibility canonical-form checking, drives `is_atom_canonical()`'s unbounded loop to index past the end of the buffer.

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
