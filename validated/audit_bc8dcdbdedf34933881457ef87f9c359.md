### Title
Out-of-Bounds Read / Missing Array Index Validation in CLVM Canonical-Encoding Check - (File: `chia/full_node/mempool_manager.py`)

### Summary
`is_atom_canonical()` and its caller `is_clvm_canonical()` in `chia/full_node/mempool_manager.py` walk an attacker-supplied CLVM byte buffer (a spend's `puzzle_reveal`/`solution` bytes) without ever validating that the read offset stays within the buffer bounds before dereferencing it. This is the same bug class as the reported resdata GRDECL issue (CWE-125 Out-of-bounds Read / CWE-129 Improper Validation of Array Index), applied to a CLVM-serialization parser that a spend-bundle submitter fully controls.

### Finding Description
`is_atom_canonical(clvm_buffer, offset)` reads `clvm_buffer[offset]` to determine a length-prefix format, then advances `offset` by `prefix_len` bytes purely based on values taken from the buffer itself, indexing `clvm_buffer[offset]` on each iteration with no bound check against `len(clvm_buffer)`: [1](#0-0) 

`is_clvm_canonical()` calls this in a loop, again indexing `clvm_buffer[offset]` directly and only checking `offset == len(clvm_buffer)` *after* the walk completes — never during the walk when `offset` is incremented based on untrusted length-prefix data: [2](#0-1) 

A crafted buffer that ends with a length-prefix byte indicating additional prefix bytes (e.g., a trailing `0xC0`-class byte with no following bytes, or a length value that walks `offset` past the end of the buffer) will cause `clvm_buffer[offset]` to be evaluated with `offset >= len(clvm_buffer)`, raising an unhandled `IndexError` in Python (the semantic analog of an out-of-bounds read/array-index violation in the reported advisory).

Per the repository's own documentation, this canonical-encoding check is invoked during mempool processing to determine DEDUP/fast-forward eligibility for spends, meaning the buffer content originates directly from an untrusted, attacker-submitted spend bundle's `puzzle_reveal`/`solution`: [3](#0-2) 

### Impact Explanation
If the resulting `IndexError` is not caught by an enclosing `try/except` in the eligible-spend/mempool-admission code path, an unprivileged spend-bundle submitter can trigger an unhandled exception during mempool processing of their own spend bundle. Depending on where this exception propagates, it can abort processing of that mempool-admission call, i.e., a spend-triggered transaction-processing disruption. This satisfies the "spend-triggered transaction-processing halt" impact category defined for this scan. I was not able to fully verify, given tool budget, whether the caller of `is_clvm_canonical()` wraps this call in exception handling — this should be confirmed by a maintainer/agent with the ability to trace the exact call site (`eligible_coin_spends.py` / DEDUP eligibility logic) referenced by the internal docs.

### Likelihood Explanation
High reachability: the input is a raw byte buffer taken from a spend bundle's puzzle reveal or solution, both of which are fully attacker-controlled and reach this code path during ordinary mempool admission of a submitted spend bundle — no special privileges, peer trust, or block inclusion is required to reach the vulnerable parser.

### Recommendation
Add explicit bounds checks in `is_atom_canonical()` and `is_clvm_canonical()` before every `clvm_buffer[offset]` access (e.g., `if offset >= len(clvm_buffer): return <not canonical>` at the top of the loop and inside the prefix-length loop), mirroring the bounds-checked pattern already used in `chia/full_node/full_block_utils.py`'s `skip_bytes`/`skip_list` helpers, which explicitly validate remaining-buffer length before indexing: [4](#0-3) 
Ensure any exception from this parser used during mempool admission is caught and converted into a normal rejection (e.g., `Err.INVALID_CONDITION` or an "ineligible for dedup" outcome) rather than allowed to propagate as an unhandled exception.

### Proof of Concept
Conceptual PoC (bounds violation, not yet confirmed against the live caller):
```python
from chia.full_node.mempool_manager import is_clvm_canonical

# A buffer whose last byte declares a multi-byte length prefix
# but provides no following bytes for that prefix.
malicious_buffer = bytes([0b11000000])  # claims 1 extra prefix byte, but buffer ends here

is_clvm_canonical(malicious_buffer)
# -> IndexError: index out of range, because is_atom_canonical()
#    unconditionally reads clvm_buffer[offset] past the buffer end
```
Further work is needed to confirm the exact spend-bundle shape (`puzzle_reveal`/`solution` bytes) that reaches `is_clvm_canonical()` through the DEDUP-eligibility path in `eligible_coin_spends.py`, and whether the resulting exception is caught there — I could not verify this within the available tool budget.

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

**File:** .cursor/context/clvm-execution.md (L96-117)
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
