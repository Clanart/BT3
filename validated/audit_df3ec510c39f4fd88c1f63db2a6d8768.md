### Title
Out-of-bounds read / unhandled crash in `is_atom_canonical` when parsing attacker-controlled CLVM buffer length prefixes - (File: chia/full_node/mempool_manager.py)

### Summary
`is_atom_canonical()` and its caller `is_clvm_canonical()` in `chia/full_node/mempool_manager.py` parse a variable-length CLVM atom length-prefix encoding (1–6 possible header byte-widths) directly from a submitted spend bundle's puzzle/solution byte buffer, without ever checking that the number of prefix bytes it reads (`prefix_len`, up to 5 extra bytes) actually fits inside the remaining `clvm_buffer`. This is structurally the same bug class as CVE-2024-41038: a variable-length header is parsed by reading a sequence of fields whose total length depends on earlier bytes, but the code never validates that those fields are still within the buffer bounds before indexing into it.

### Finding Description
`is_atom_canonical(clvm_buffer, offset)` decodes the CLVM atom size prefix by inspecting the top bits of `clvm_buffer[offset]` to determine `prefix_len` (0 to 5 additional bytes), then loops `for i in range(prefix_len): ... clvm_buffer[offset]` to accumulate the length value: [1](#0-0) 

Unlike `full_node/full_block_utils.py`'s `skip_bytes`/`skip_list` helpers, which explicitly check `if len(buf) < 4: raise ValueError(...)` and `if n > len(buf): raise ValueError(...)` before consuming length-prefixed data: [2](#0-1) 

`is_atom_canonical` and its driver `is_clvm_canonical` never validate `offset + prefix_len < len(clvm_buffer)` before dereferencing `clvm_buffer[offset]` inside the prefix-decoding loop, nor before consuming the returned `atom_len` bytes in the caller: [3](#0-2) 

A submitted spend bundle whose coin-spend puzzle reveal or solution ends with a truncated multi-byte atom length-prefix byte (e.g. a single trailing `0xFC` byte, which claims a 5-byte-wide length header, with fewer than 5 bytes actually remaining) causes `clvm_buffer[offset]` to raise an unhandled `IndexError` when the loop walks past the end of the buffer.

### Impact Explanation
`is_clvm_canonical()` is documented as being invoked to determine `eligible_for_dedup` status while processing an incoming, otherwise-valid spend bundle in the mempool admission path (per project context: "`chia/full_node/mempool_manager.py` — `pre_validate_spendbundle()`, `is_clvm_canonical()`"): [4](#0-3) 

If this call site does not wrap the parse in exception handling, an unprivileged spend-bundle submitter can trigger an unhandled `IndexError` deep in mempool item construction for a crafted-but-otherwise-CLVM-valid coin spend, causing a spend-triggered exception during mempool processing rather than a clean rejection (`MempoolInclusionStatus`/`Err`). This matches the "spend-triggered transaction-processing halt" impact class called out in scope. The severity depends on whether the surrounding call is guarded — this could not be fully confirmed since the exact call site (outside the two matches inside `mempool_manager.py` itself) that invokes `is_clvm_canonical()` was not located in the available index; only the definitions and their unit tests were found.

### Likelihood Explanation
Likelihood is high for reachability — the input (a coin's puzzle reveal or solution bytes inside a submitted `SpendBundle`) is fully attacker-controlled and requires no permissions beyond submitting a spend bundle to the mempool. Crafting a buffer ending in a partial length-prefix atom (e.g. `0xF8`, `0xFC` marker byte followed by fewer bytes than the declared `prefix_len`) is straightforward. The unit tests already exercise atom-boundary edge cases (`test_atom_not_canonical`) but do not include a case where the buffer is truncated mid-prefix: [5](#0-4) 

### Recommendation
Add explicit bounds checks in `is_atom_canonical` before each `clvm_buffer[offset]` access inside the prefix-decoding loop (and in `is_clvm_canonical`'s consumption of `atom_len`), raising a handled error (e.g., treating it as non-canonical / returning a clear `ValueError`) instead of allowing `IndexError` to propagate, mirroring the bounds-checked pattern already used in `chia/full_node/full_block_utils.py`'s `skip_bytes`/`skip_list`. Ensure any caller of `is_clvm_canonical()` treats parse failures as "not eligible for dedup" rather than propagating an unhandled exception into mempool-item processing.

### Proof of Concept
1. Construct a coin spend whose puzzle reveal or solution CLVM byte buffer ends with a single trailing byte in the `0xF8`–`0xFB` range (5-byte length prefix marker) or `0xFC`–`0xFD` range (6-byte length prefix marker), with no additional bytes following it.
2. Submit the resulting `SpendBundle` to the mempool via the normal wallet/RPC submission path.
3. When mempool processing reaches the dedup-eligibility canonical-serialization check, `is_atom_canonical()`'s loop `atom_len |= clvm_buffer[offset]` executes with `offset` past the end of `clvm_buffer`, raising an unhandled `IndexError`.

*Note: full confirmation that this exception is unhandled at the call site requires locating the exact caller of `is_clvm_canonical()` used during mempool item construction, which was not found in the indexed portion of `mempool_manager.py` available for this analysis — a Devin session with full repository access would be needed to trace and confirm the call site's exception handling.*

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

**File:** chia/_tests/core/mempool/test_mempool_manager.py (L159-178)
```python
@pytest.mark.parametrize(
    "clvm_hex, expect",
    [
        ("c000", 2 + 0),
        ("c03f", 2 + 0x3F),
        ("e00000", 3 + 0),
        ("e01fff", 3 + 0x1FFF),
        ("f0000000", 4 + 0),
        ("f00fffff", 4 + 0xFFFFF),
        ("f800000000", 5 + 0),
        ("f807ffffff", 5 + 0x7FFFFFF),
        ("fc0000000000", 6 + 0),
        ("fc03ffffffff", 6 + 0x3FFFFFFFF),
    ],
)
def test_atom_not_canonical(clvm_hex: str, expect: int) -> None:
    clvm_buf = bytes.fromhex(clvm_hex)
    atom_len, is_canonical = is_atom_canonical(clvm_buf, 0)
    assert atom_len == expect
    assert not is_canonical
```
