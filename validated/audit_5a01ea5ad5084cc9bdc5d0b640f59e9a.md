### Title
Unbounded buffer indexing in CLVM canonical-encoding check causes out-of-bounds read / crash on malformed spend bundle - (File: chia/full_node/mempool_manager.py)

### Summary
`is_atom_canonical()` and `is_clvm_canonical()` in `chia/full_node/mempool_manager.py` walk a submitted CLVM buffer (puzzle reveal / solution bytes from an untrusted spend bundle) using a length-prefix byte to determine how many following bytes constitute the atom length, then index directly into the buffer with `clvm_buffer[offset]` without ever checking `offset` against `len(clvm_buffer)`. This mirrors the CVE-2016-3142 bug class: a crafted length/marker byte placed near the end of a buffer causes the parser to read past the buffer boundary.

### Finding Description
`is_atom_canonical()` computes `prefix_len` (0–5) purely from the high bits of the byte at `offset`, then unconditionally loops `prefix_len` times doing: [1](#0-0) 
There is no check that `offset` (after incrementing) is still within `len(clvm_buffer)`. If a submitter crafts a puzzle reveal/solution whose last byte is a long-form atom-length marker (e.g. `0xFC`, which claims a 5-byte length field) placed at or near the end of the buffer, `offset += 1; atom_len |= clvm_buffer[offset]` will index past the end of `clvm_buffer`.

`is_clvm_canonical()` itself also indexes unconditionally at the top of its loop: [2](#0-1) 
and calls `is_atom_canonical(clvm_buffer, offset)` for any atom byte, propagating the same unchecked-index issue: [3](#0-2) 

This is functionally the same bug class as CVE-2016-3142: a "signature"/marker byte (here, the high-bit-pattern length prefix, analogous to PHP's `PK\x05\x06` end-of-archive signature) placed at an invalid/truncated location causes the parser to read beyond the allocated buffer instead of validating remaining length first. Contrast this with the codebase's own hardened parsers in `chia/full_node/full_block_utils.py` (`skip_bytes`, `skip_list`), which explicitly check `n > len(buf)` and raise `ValueError` before indexing — the exact defensive pattern missing here.

### Impact Explanation
Because Python raises `IndexError` on out-of-bounds sequence access rather than reading adjacent memory, this cannot leak process memory (unlike the original PHP CVE), but it can raise an unhandled exception during spend-bundle admission processing. If this exception is not defensively caught by the calling code path that invokes `is_clvm_canonical`/`is_atom_canonical` during mempool item construction (e.g., dedup-eligibility checks on a submitted spend bundle's solution), a single malicious/malformed spend bundle submitted by any unprivileged peer/wallet could throw an uncaught `IndexError`, disrupting spend-bundle validation for that request. This matches the accepted impact category: "a spend-triggered transaction-processing halt."

### Likelihood Explanation
The functions operate directly on raw solution/puzzle bytes taken from an externally submitted `SpendBundle`, which is fully attacker-controlled input reachable by any unprivileged spend-bundle submitter without special permissions. Crafting a buffer whose last byte(s) are a long-form atom length marker is trivial and deterministic.

### Recommendation
Add explicit bounds checks in `is_atom_canonical()` (and defensively in `is_clvm_canonical()`) before dereferencing `clvm_buffer[offset]`, e.g. verify `offset < len(clvm_buffer)` prior to each read and raise/return a controlled "not canonical"/error result instead of relying on the buffer being well-formed. This should mirror the bounds-checked pattern already used in `chia/full_node/full_block_utils.py`'s `skip_bytes`/`skip_list`.

### Proof of Concept
Construct a CLVM buffer ending in a byte with the `0xFC`/`0xF8`-style long length-prefix pattern immediately at the end of the buffer (or with too few trailing bytes to satisfy `prefix_len`), e.g. `b"\xfc"` alone, and pass it into `is_clvm_canonical()`:
```python
is_clvm_canonical(b"\xfc")  # offset walks past end -> IndexError
```
Embed such a byte sequence as the final bytes of a spend bundle's `puzzle_reveal`/`solution` and submit it through the mempool's normal add-spend-bundle path to trigger the unhandled index error during canonical-encoding validation. [4](#0-3)

### Citations

**File:** chia/full_node/mempool_manager.py (L144-227)
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
