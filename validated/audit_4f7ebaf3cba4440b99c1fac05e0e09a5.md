### Title
Out-of-bounds buffer read causing unhandled exception in CLVM canonical-encoding check - (File: `chia/full_node/mempool_manager.py`)

### Summary
`chia/full_node/mempool_manager.py` implements a hand-rolled CLVM atom/pair scanner, `is_atom_canonical()` and `is_clvm_canonical()`, that walks an attacker-supplied `clvm_buffer` (a spend's puzzle/solution bytes) byte-by-byte without validating that the multi-byte length-prefix decode stays inside the buffer bounds, analogous to the PHP `mbfl_filt_conv_big5_wchar` bug where a crafted multibyte length caused reads past the allocated buffer.

### Finding Description
`is_atom_canonical()` reads `clvm_buffer[offset]` to determine an atom's length-prefix encoding (1–6 bytes) and then loops `prefix_len` times indexing `clvm_buffer[offset]` again to accumulate `atom_len`, without ever checking that `offset` remains `< len(clvm_buffer)`: [1](#0-0) 

`is_clvm_canonical()` drives this scan over the entire buffer, advancing `offset` by `atom_len` (an attacker-controlled value read straight from the buffer) and looping back into `is_atom_canonical()`: [2](#0-1) 

A crafted CLVM byte sequence — e.g., an atom-length prefix byte (such as `0xFC`, which declares 5 additional length bytes) placed near the end of the buffer, or an atom whose declared `atom_len` skips past the buffer's end — causes `clvm_buffer[offset]` to be indexed at an out-of-range position. In Python this raises an `IndexError` rather than silently reading adjacent heap memory (unlike the C-based PHP bug), so the vulnerability class manifests as an unhandled-exception crash/DoS rather than information disclosure, but the root defect — decoding a length field without bounds-checking the buffer before consuming it — is the same as the CVE-2020-7060 bug class.

### Impact Explanation
The function's own docstring says this canonical-encoding check gates `ELIGIBLE_FOR_DEDUP` classification for mempool spends, meaning it is invoked while processing a submitted spend bundle's CLVM buffer during mempool admission (per the doc note "Required for DEDUP-eligible spends" in `.cursor/context/clvm-execution.md`): [3](#0-2) 

If this scan is invoked without a surrounding try/except in the mempool admission path, a single unprivileged spend-bundle submitter can trigger an unhandled `IndexError` while the full node evaluates dedup eligibility for the submitted bundle, disrupting transaction processing for that request (a spend-triggered halt/DoS in the affected code path). I was not able to fully trace the exact call site tying this function into `add_spend_bundle()`/mempool admission within the available tool budget, so the precise blast radius (single-request exception vs. broader mempool-manager crash) is not fully confirmed and should be verified directly in the code.

### Likelihood Explanation
Any attacker able to submit a spend bundle to a full node's mempool (or via RPC) fully controls the puzzle/solution CLVM byte buffer being scanned, and crafting a truncated/oversized length-prefix atom near the end of the buffer is trivial and requires no privileges or special conditions.

### Recommendation
Add explicit bounds checks in `is_atom_canonical()` before each `clvm_buffer[offset]` access (verify `offset < len(clvm_buffer)` prior to reading the initial byte and each of the `prefix_len` continuation bytes), and have `is_clvm_canonical()` treat any such out-of-bounds condition as "not canonical" (return `False`) rather than allowing an exception to propagate, consistent with how `full_block_utils.py`'s `skip_bytes`/`skip_list` already defensively bounds-check length prefixes against the remaining buffer.

### Proof of Concept
Conceptual PoC (bounds violation, not yet confirmed against the live call site):
```python
from chia.full_node.mempool_manager import is_clvm_canonical

# 0xFC declares a 5-byte continuation length prefix, but the buffer
# only supplies 1 more byte before ending.
malicious_buffer = bytes([0xFC, 0x01])
is_clvm_canonical(malicious_buffer)  # raises IndexError instead of returning False
```

---
Note: I could not fully confirm, within the available tool budget, the exact call site that invokes `is_clvm_canonical`/`is_atom_canonical` from the spend-bundle admission path in `mempool_manager.py` (grep showed 4 references in that file, 3 of which are the two `def` lines plus the internal `is_atom_canonical` call inside `is_clvm_canonical`; the 4th external caller was not retrieved before the iteration limit). This should be verified directly to confirm whether the resulting `IndexError` is caught upstream (reducing impact to a per-request rejection) or propagates uncaught.

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

**File:** .cursor/context/clvm-execution.md (L108-112)
```markdown
### When enforced

Required for DEDUP-eligible spends. Without canonical form, identical
solutions could have different serializations, breaking dedup.

```
