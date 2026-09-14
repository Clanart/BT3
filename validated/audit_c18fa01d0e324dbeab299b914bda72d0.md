### Title
Out-of-bounds `IndexError` in `is_atom_canonical`/`is_clvm_canonical` when parsing an attacker-supplied spend bundle's CLVM buffer - (File: `chia/full_node/mempool_manager.py`)

### Summary
`is_atom_canonical` and `is_clvm_canonical` in `chia/full_node/mempool_manager.py` parse a CLVM-serialized buffer (originating from a submitted spend bundle's puzzle reveal/solution) byte-by-byte to decide DEDUP eligibility, but unlike the sibling parser `chia/full_node/full_block_utils.py`, they never validate that the declared atom-length prefix or atom length actually fits within the remaining buffer before indexing into it.

### Finding Description
`is_atom_canonical(clvm_buffer, offset)` reads a length-prefix byte at `clvm_buffer[offset]`, determines a variable `prefix_len` (0-5 additional bytes) based on the top bits of that byte, and then loops `prefix_len` times doing: [1](#0-0) 

There is no check that `offset + prefix_len` stays within `len(clvm_buffer)` before this loop runs, and no check afterward that the computed `atom_len` (which can encode up to 5 bytes / ~1TB) fits in the remaining buffer before the caller advances `offset` by `1 + prefix_len + atom_len`: [2](#0-1) 

`is_clvm_canonical` drives this loop over the whole buffer, repeatedly calling `is_atom_canonical` and advancing `offset` by the (unvalidated) computed atom length, with only a final check that `offset == len(clvm_buffer)`: [3](#0-2) 

This is the direct analog of the `SetMacAddrAction` bug class: a length-prefixed field is read and its declared length is trusted to advance a cursor without first bounds-checking against the remaining buffer. Contrast this with the project's own defensive pattern used elsewhere for parsing untrusted serialized buffers, e.g. `skip_bytes`/`skip_list` in `chia/full_node/full_block_utils.py`, which explicitly validate `n > len(buf)` and raise `ValueError` before slicing: [4](#0-3) 

Because `is_atom_canonical`/`is_clvm_canonical` lack this guard, a crafted (truncated or malformed) puzzle-reveal/solution buffer from a spend bundle can cause `clvm_buffer[offset]` to be indexed past the end of the buffer.

### Impact Explanation
In Python, indexing a `bytes`/`bytearray` past its end raises `IndexError` rather than reading adjacent heap memory (unlike the C++ `dnsdist` case, which could leak uninitialized memory). The reachable impact here is therefore a crash/exception rather than memory disclosure: an unhandled `IndexError` raised while the mempool manager evaluates DEDUP eligibility for an incoming spend bundle can propagate out of the intended `ValueError`/`ConsensusError` handling paths used elsewhere in spend-bundle validation, causing the transaction-processing path for that spend (and potentially the request handling coroutine) to fail unexpectedly. This falls into the "spend-triggered transaction-processing halt" impact category — a single unprivileged submitter can trigger it just by broadcasting a malformed spend bundle for DEDUP-eligibility evaluation.

### Likelihood Explanation
Reachability requires only that an attacker submit a spend bundle whose puzzle reveal/solution CLVM buffer contains a truncated multi-byte atom-length prefix positioned such that `is_atom_canonical`'s prefix-reading loop or the subsequent atom-length slice runs past the buffer boundary — something a wallet-level submitter fully controls when constructing puzzle reveals/solutions. No special privileges, peer trust, or non-standard puzzles are needed; only crafted serialization bytes. This makes the likelihood moderate: the trigger condition (mismatched declared length vs. actual remaining bytes in a still-otherwise-parseable CLVM structure) is a narrow but attacker-constructible edge case in a DEDUP-eligibility optimization path, not a core consensus validation path.

### Recommendation
Add explicit bounds checks in `is_atom_canonical` before reading `clvm_buffer[offset]` for each prefix byte and before trusting the derived `atom_len` to advance the parser, mirroring the pattern already used in `chia/full_node/full_block_utils.py` (`skip_bytes`, `skip_list`), and have `is_clvm_canonical` treat any out-of-range read as "not canonical" (return `False`) instead of letting an `IndexError` escape.

### Proof of Concept
Construct a spend bundle whose puzzle-reveal/solution CLVM serialization ends with a large-length-prefix marker byte (e.g. `0xF8`, indicating a 4-byte trailing length field) but omit some or all of the following length bytes, then submit it so the mempool manager evaluates DEDUP eligibility via `is_clvm_canonical`:
```python
# byte 0xF8 declares a 4-byte length-prefix atom (per is_atom_canonical's bit patterns),
# but only 1 of the 4 required trailing bytes is present.
truncated_buf = bytes([0xF8, 0x00])
is_clvm_canonical(truncated_buf)  # raises IndexError instead of returning False
```
Exact confirmation of the surrounding call site's exception handling in `mempool_manager.py` (i.e., whether `IndexError` is caught anywhere before reaching this function) could not be fully verified within the available tool budget; this should be checked directly in the file to confirm whether the exception is currently swallowed or propagates to crash transaction processing.

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
