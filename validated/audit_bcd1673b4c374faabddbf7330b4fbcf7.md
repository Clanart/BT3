### Title
Unbounded byte read past buffer end in CLVM canonical-encoding parser causes spend-triggered crash - (File: chia/full_node/mempool_manager.py)

### Summary
`is_atom_canonical()` in `chia/full_node/mempool_manager.py` decodes a CLVM atom length prefix by indexing into the caller-supplied `clvm_buffer` for `prefix_len` additional bytes without ever checking that `offset` stays within the buffer bounds. This mirrors the FRR `bgp_label.c` bug class in CVE-2023-38407: a length-prefixed field is parsed by walking forward through a byte stream while trusting the encoded length rather than validating remaining buffer size, so a truncated/malformed prefix causes the parser to read past the end of the buffer.

### Finding Description
`is_atom_canonical()` decodes the CLVM sized-atom prefix format: [1](#0-0) 

The function branches on the high bits of the first byte to determine `prefix_len` (0–5 extra bytes to read), then loops:

```
atom_len = b & mask
for i in range(prefix_len):
    atom_len <<= 8
    offset += 1
    atom_len |= clvm_buffer[offset]
```

There is no check that `offset` (after incrementing) is `< len(clvm_buffer)` before `clvm_buffer[offset]` is read. If the buffer ends immediately after the leading prefix byte (or in the middle of the multi-byte length field), this raises an uncaught `IndexError`, i.e. an attempt to read beyond the end of the buffer — the same root-cause shape as FRR's stream over-read during labeled-unicast parsing.

`is_clvm_canonical()`, the caller, similarly assumes `clvm_buffer[offset]` is always valid at each iteration of its token loop and does not pre-validate remaining length before calling `is_atom_canonical`: [2](#0-1) 

This canonical-encoding check is invoked from mempool bundle processing to decide DEDUP/fast-forward eligibility for spends (`ELIGIBLE_FOR_DEDUP`), i.e. on `puzzle_reveal`/`solution` bytes supplied directly by an unprivileged spend-bundle submitter. The existing test suite only exercises complete-but-non-canonical buffers (e.g. `"c000"`, `"fe"`): [3](#0-2) 

It does not exercise a *truncated* multi-byte prefix (e.g. `b"\xfc"` alone, or `b"\xfc\x00\x00"` with the trailing bytes missing), which is exactly the input shape that would trigger the unguarded `clvm_buffer[offset]` read.

### Impact Explanation
If this canonical-check code path is reached without a broad exception handler around it, a single submitted spend bundle containing a malformed/truncated atom-length prefix in its puzzle reveal or solution could raise an unhandled `IndexError` inside mempool processing, halting or crashing the transaction-processing task for that node — a spend-triggered transaction-processing halt, one of the explicitly accepted impact categories.

### Likelihood Explanation
I could not fully verify, within the available tooling, whether the call sites that invoke `is_clvm_canonical`/`is_atom_canonical` during spend bundle admission wrap the call in a catch-all exception handler that converts `IndexError` into a normal `Err.*` mempool rejection (the visible tests only show `ValueError`/assertion-style failures converted to `Err.INVALID_COIN_SOLUTION`). This is a genuine gap in my analysis: I found the definition and its unguarded bounds check, and confirmed it's used in mempool eligibility computation reachable by unprivileged submitters, but I did not locate and read the exact call site wrapping code (only its name via grep) to confirm whether `IndexError` specifically is caught there. This uncertainty should be resolved by a Devin session with full file access before treating this as fully proven.

### Recommendation
Add an explicit bounds check in `is_atom_canonical()` before each `clvm_buffer[offset]` read (e.g. `if offset >= len(clvm_buffer): raise ValueError(...)` or return non-canonical), and add the same guard in `is_clvm_canonical()`'s main token loop before dereferencing `clvm_buffer[offset]`. Add regression tests with truncated multi-byte length prefixes (1–5 bytes short) to confirm a normal `ValueError`/`Err` result rather than an `IndexError`.

### Proof of Concept
```python
from chia.full_node.mempool_manager import is_clvm_canonical

# 0xFC signals a 5-extra-byte length prefix, but the buffer ends right after
# the leading byte -- is_atom_canonical() will try to read clvm_buffer[1],
# which does not exist.
is_clvm_canonical(b"\xfc")
```
Expected (defensive): a clean `ValueError`/`False` return.
Actual (as currently written): raises `IndexError: index out of range`, an unhandled exception surfaced from parsing attacker-controlled puzzle/solution bytes.

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

**File:** chia/_tests/core/mempool/test_mempool_manager.py (L136-156)
```python
@pytest.mark.parametrize(
    "clvm_hex",
    [
        "fffe80",
        "c000",
        "c03f",
        "e00000",
        "e01fff",
        "f0000000",
        "f00fffff",
        "f800000000",
        "f807ffffff",
        "fc0000000000",
        "fc03ffffffff",
        "fe",
        "ff808080",
    ],
)
def test_clvm_not_canonical(clvm_hex: str) -> None:
    clvm_buf = bytes.fromhex(clvm_hex)
    assert not is_clvm_canonical(clvm_buf)
```
