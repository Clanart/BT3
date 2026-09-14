Given the available context, I found a genuine analog in `chia/full_node/mempool_manager.py`'s CLVM canonical-encoding check, which shares the exact root-cause pattern described in CVE-2015-1607 (unchecked bitwise length-prefix parsing that reads past the intended bounds of an attacker-controlled buffer).

### Title
Unbounded length-prefix parsing in `is_atom_canonical` can raise an out-of-range read on attacker-controlled CLVM bytes - (File: chia/full_node/mempool_manager.py)

### Summary
`is_atom_canonical()` decodes a CLVM atom's variable-length size prefix (1–6 bytes) directly out of a caller-supplied `clvm_buffer` without ever checking that `offset` stays within `len(clvm_buffer)` while consuming the multi-byte prefix, mirroring the GnuPG `keybox-search.c` flaw where bitwise shifts were applied to attacker-controlled length data without validating bounds before the subsequent memory access.

### Finding Description
`is_atom_canonical` reads the first byte to determine how many additional prefix bytes (`prefix_len`, up to 5) encode the atom length, then loops `prefix_len` times doing `atom_len <<= 8; offset += 1; atom_len |= clvm_buffer[offset]`, indexing into `clvm_buffer` at each step with no bounds check. [1](#0-0) 
This is invoked from `is_clvm_canonical`, which walks a full CLVM-serialized buffer token by token, calling `is_atom_canonical(clvm_buffer, offset)` whenever it encounters an atom marker, and also indexes `clvm_buffer[offset]` at the top of its own loop without checking `offset < len(clvm_buffer)`. [2](#0-1) 
Contrast this with the project's own hardened parsers in `chia/full_node/full_block_utils.py`, which explicitly validate that a declared length or count does not exceed the remaining buffer before consuming it (e.g., `skip_bytes`, `skip_list`), showing that the codebase is aware this class of untrusted-length parsing must be bounds-checked. [3](#0-2) 
If a crafted solution/atom encodes a multi-byte length prefix near the end of the buffer (e.g., a 5-byte prefix marker as the last byte, or with only some of the trailing length bytes present), `offset` will walk past the end of `clvm_buffer` and `clvm_buffer[offset]` raises `IndexError`, which is the functional analog of the "invalid read operation" in the CVE.

### Impact Explanation
`is_clvm_canonical`/`is_atom_canonical` are used by the mempool manager's CLVM dedup-eligibility path to decide whether a spend's solution bytes are in canonical form; this path is driven by CLVM byte content taken from unprivileged, attacker-submitted spend bundles. An unhandled `IndexError` raised while validating a single submitted spend bundle can propagate out of this check into the mempool add path, and if not caught, constitutes a spend-triggered exception during transaction-processing that halts handling of that call (and potentially destabilizes concurrent processing depending on exception propagation/task structure) — matching the "spend-triggered transaction-processing halt" impact class explicitly listed as in-scope.

### Likelihood Explanation
I could not fully confirm, within available tool calls, whether the caller of `is_clvm_canonical` wraps it in a try/except that would downgrade an `IndexError` to a benign "non-canonical" classification, nor could I confirm the exact call site and surrounding exception handling in `mempool_manager.py` (grep only surfaced the definitions and one additional reference each, and I was not able to read that call site before running out of tool budget). This materially limits confidence in the exploitability and blast radius of this finding — it is presented as a code-pattern match to the CVE's root cause, not a confirmed working crash.

### Recommendation
Add explicit bounds checks in `is_atom_canonical` (and the enclosing loop in `is_clvm_canonical`) so that `offset + prefix_len` is validated against `len(clvm_buffer)` before consuming prefix bytes, consistent with the defensive length checks already used in `chia/full_node/full_block_utils.py`, and ensure any exception from this canonical-check path is caught and treated as "not canonical" / rejected rather than propagating out of the mempool add path.

### Proof of Concept
Not confirmed end-to-end due to incomplete visibility into the exact call site and exception handling around `is_clvm_canonical` in `chia/full_node/mempool_manager.py`. Conceptually: submit a spend bundle whose solution CLVM bytes end with a multi-byte atom length-prefix marker (e.g., a byte matching `0b111110xx`, indicating a 5-byte trailing length) but with fewer than 5 bytes remaining in the buffer, so that the `for i in range(prefix_len): ... offset += 1; atom_len |= clvm_buffer[offset]` loop indexes past the end of `clvm_buffer` and raises `IndexError`.

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

**File:** chia/full_node/full_block_utils.py (L15-34)
```python
def skip_list(buf: memoryview, skip_item: Callable[[memoryview], memoryview]) -> memoryview:
    if len(buf) < 4:
        raise ValueError(f"list count prefix requires 4 bytes, remaining buffer {len(buf)}")
    n = int.from_bytes(buf[:4], "big", signed=False)
    buf = buf[4:]
    if n > len(buf):
        raise ValueError(f"list count {n} exceeds remaining buffer {len(buf)}")
    for _ in range(n):
        buf = skip_item(buf)
    return buf


def skip_bytes(buf: memoryview) -> memoryview:
    if len(buf) < 4:
        raise ValueError(f"byte length prefix requires 4 bytes, remaining buffer {len(buf)}")
    n = int.from_bytes(buf[:4], "big", signed=False)
    buf = buf[4:]
    if n > len(buf):
        raise ValueError(f"byte length {n} exceeds remaining buffer {len(buf)}")
    return buf[n:]
```
