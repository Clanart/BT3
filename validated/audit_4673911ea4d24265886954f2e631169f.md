### Title
Out-of-bounds index read in `is_atom_canonical()` when parsing CLVM atom length-prefix during mempool DEDUP-eligibility check - (File: `chia/full_node/mempool_manager.py`)

### Summary
`is_atom_canonical()` in `chia/full_node/mempool_manager.py` reads a variable-length (1–6 byte) atom-length prefix out of an attacker-supplied CLVM buffer without ever checking that the buffer actually contains enough remaining bytes to satisfy the declared prefix length. This mirrors the libgphoto2 pattern (`ptp_unpack_Sony_DPD()`), which read a 2-byte enumeration count via `dtoh16o(data, *poffset)` without checking that 2 bytes remained, while the sibling function `ptp_unpack_DPD()` had the correct check. Here, `is_atom_canonical()` is the "sibling function missing the bounds check" analog of the properly-guarded length-prefixed parsers that exist elsewhere in the same codebase (e.g. `skip_bytes()`/`skip_list()` in `chia/full_node/full_block_utils.py`, which explicitly validate `len(buf) < 4` and `n > len(buf)` before consuming).

### Finding Description
`is_atom_canonical(clvm_buffer, offset)`: [1](#0-0) 

reads `b = clvm_buffer[offset]` to determine the prefix length (`prefix_len`, up to 5), then loops `prefix_len` times incrementing `offset` and reading `clvm_buffer[offset]` again to accumulate `atom_len`, with **no check that `offset + prefix_len < len(clvm_buffer)`** before doing so. This is called from `is_clvm_canonical()`: [2](#0-1) 

which walks an entire CLVM-serialized buffer (a spend's `puzzle_reveal` or `solution` bytes) token-by-token to determine whether it uses canonical (shortest-form) serialization — a check the codebase's own documentation states is "Required for DEDUP-eligible spends" so that identical solutions dedupe correctly in the mempool.

Contrast this with the properly bounds-checked analogs in the same codebase that handle equivalent length-prefixed untrusted data: [3](#0-2) 

which explicitly raises `ValueError` if the declared length exceeds the remaining buffer — the same defensive check that `ptp_unpack_DPD()` has and `ptp_unpack_Sony_DPD()` lacks in the reported libgphoto2 CVE.

An attacker who submits a `SpendBundle` whose `puzzle_reveal` or `solution` ends with a truncated multi-byte atom length-prefix byte (e.g., a final byte in the `0xF8`–`0xFF` range indicating a 4–5 byte length field that isn't actually present) will cause `is_atom_canonical()` to index past the end of `clvm_buffer`.

### Impact Explanation
Unlike the C implementation in libgphoto2 where this class of bug causes an actual out-of-bounds memory read (information disclosure/crash), Python's `bytes`/`memoryview` indexing is memory-safe: an out-of-range index raises `IndexError` rather than reading adjacent heap memory. The practical consequence in this codebase is therefore not memory disclosure but an **unhandled exception** during mempool admission processing of a single, attacker-submitted spend bundle. If this exception is not caught by a sufficiently broad handler in the mempool-item admission path (`is_clvm_canonical`/DEDUP-eligibility check runs as part of processing an incoming `SpendBundle`), a malicious spend bundle crafted with a truncated canonical-length prefix could trigger a crash/unhandled exception in the code path that inspects the spend's CLVM bytes, i.e. a spend-triggered halt of transaction processing for that bundle (denial-of-service style impact on mempool admission), rather than any coin-movement or supply issue.

I was not able to fully trace, within the available tool budget, the exact caller site of `is_clvm_canonical()` further down in `mempool_manager.py` (beyond its definition) or confirm whether a generic `try/except` there catches `IndexError` specifically (as opposed to `ValueError`/`ConsensusError`/`EvalError`). This is a real gap in verification and should be checked directly against `chia/full_node/mempool_manager.py` before treating this as conclusively exploitable to the reported severity.

### Likelihood Explanation
Any unprivileged party able to submit a spend bundle to the mempool (RPC caller, wallet user, or peer-submitted transaction reaching mempool admission) can construct a `puzzle_reveal`/`solution` byte string with a truncated atom-length prefix at the very end of the buffer, so the trigger condition is trivially reachable and requires no privileged access.

### Recommendation
Add the same defensive bound check used elsewhere in the codebase (e.g. `skip_bytes()` in `chia/full_node/full_block_utils.py`) to `is_atom_canonical()`: before entering the `for i in range(prefix_len)` loop, verify `offset + prefix_len < len(clvm_buffer)` (and that the initial `clvm_buffer[offset]` access itself is in-bounds), returning a non-canonical/error result instead of indexing past the buffer end.

### Proof of Concept
Construct a CLVM buffer whose only atom is a single byte such as `0xFC` (which declares a 5-byte length field to follow) with no subsequent bytes, e.g. `bytes([0xFC])`, and pass it to `is_clvm_canonical(...)`/`is_atom_canonical(buf, 0)`:
```python
from chia.full_node.mempool_manager import is_atom_canonical
is_atom_canonical(bytes([0xFC]), 0)  # raises IndexError: index out of range
```
Submitting a `SpendBundle` whose `puzzle_reveal` or `solution` ends in this truncated multi-byte length prefix reaches this code path during the DEDUP-eligibility canonical-serialization check in `chia/full_node/mempool_manager.py`.

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
