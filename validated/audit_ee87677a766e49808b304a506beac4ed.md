### Title
Missing bounds check in CLVM canonical-form atom length parsing causes unhandled crash on malformed spend bundle - (File: chia/full_node/mempool_manager.py)

### Summary
`is_atom_canonical()` and its caller `is_clvm_canonical()` in `chia/full_node/mempool_manager.py` parse CLVM atom length prefixes by walking `prefix_len` (up to 5) extra bytes past the current offset without ever validating that those bytes exist within `clvm_buffer`. This mirrors the libarchive RAR-header bug class (CVE-2017-14502): a variable-length-prefixed field is read using attacker-controlled length data with no upper-bound/EOF check against the actual buffer size, producing an out-of-bounds read.

### Finding Description
`is_atom_canonical(clvm_buffer, offset)` inspects the leading byte to determine how many additional length-prefix bytes to consume (0–5 bytes depending on the high bits), then loops: [1](#0-0) 
reading `clvm_buffer[offset]` for each of `prefix_len` iterations with no check that `offset < len(clvm_buffer)`. The full prefix-length dispatch (up to `prefix_len = 5`, i.e. reading 6 bytes total) is decided purely from the untrusted first byte: [2](#0-1) 

The caller, `is_clvm_canonical()`, drives this by iterating over the buffer and calling `is_atom_canonical` whenever a byte `> 0x80` is seen, without first ensuring enough trailing bytes remain for the indicated prefix length: [3](#0-2) 

If a puzzle reveal or solution ends with a byte whose top bits indicate a multi-byte length prefix (e.g. `0xFC`) but the buffer is truncated right after that byte, the indexing `clvm_buffer[offset]` inside the loop runs past the end of the buffer. In Python this raises an `IndexError` rather than reading adjacent process memory, but the effect is analogous to the libarchive bug: attacker-controlled, insufficiently-validated length-prefixed data drives an out-of-bounds access.

This routine is invoked from `is_clvm_canonical(bytes(spend.puzzle_reveal))` / `is_clvm_canonical(bytes(spend.solution))`, which is used to decide DEDUP/fast-forward eligibility for spends inside `pre_validate_spendbundle` / mempool item construction (confirmed via test coverage in `chia/_tests/core/mempool/test_mempool_manager.py`, e.g. `test_mempool_requires_canonical_clvm`, which exercises this exact code path with attacker-supplied puzzle/solution hex). This is reachable by any unprivileged party submitting a spend bundle to the mempool, since puzzle reveals and solutions are fully attacker-controlled bytes.

### Impact Explanation
An unhandled `IndexError` raised inside mempool eligibility processing, if not caught by an enclosing `try/except`, would propagate out of spend-bundle processing. Because this check runs during normal mempool admission for every candidate spend (to compute DEDUP/FF eligibility), a crafted spend bundle with a truncated/malformed puzzle_reveal or solution whose trailing byte implies a multi-byte atom length prefix could trigger a crash in that code path, constituting a spend-triggered transaction-processing halt (potential DoS against the full node's mempool logic) if the exception is not properly bounded by existing try/except wrapping in the caller.

### Likelihood Explanation
Reachable by any unprivileged spend-bundle submitter — no special privileges are required, only crafting a puzzle_reveal or solution byte sequence with a truncated non-canonical length prefix at the very end of the buffer. The severity in practice depends on whether callers of `is_clvm_canonical` wrap this call in exception handling; that could not be fully confirmed from the available call sites within this investigation.

### Recommendation
Add explicit bounds checks in `is_atom_canonical` before each `clvm_buffer[offset]` access (e.g., verify `offset + prefix_len < len(clvm_buffer)` before entering the loop, or catch `IndexError` in `is_clvm_canonical`/its callers and treat truncated atoms as non-canonical rather than allowing an exception to propagate). This aligns the Python-level parser with the same "prefix requires N bytes, remaining buffer M" validation pattern already used elsewhere in the codebase (e.g., `skip_bytes`/`skip_list` in `chia/full_node/full_block_utils.py`).

### Proof of Concept
Construct a spend bundle whose `puzzle_reveal` or `solution` (as raw CLVM bytes) ends with a byte such as `0xFC` (which requires 5 additional length-prefix bytes) but provide zero or fewer trailing bytes, e.g. a buffer ending in just `...\xfc`. Passing this buffer to `is_clvm_canonical()` (invoked on `bytes(spend.puzzle_reveal)`/`bytes(spend.solution)` during mempool item eligibility computation) causes `is_atom_canonical` to index past the end of `clvm_buffer`, raising an unhandled `IndexError`. [4](#0-3) [5](#0-4)

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
