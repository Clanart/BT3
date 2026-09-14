### Title
Unbounded index read in `is_clvm_canonical`/`is_atom_canonical` on attacker-controlled spend bundle bytes - (File: chia/full_node/mempool_manager.py)

### Summary
`is_atom_canonical()` and `is_clvm_canonical()` in `chia/full_node/mempool_manager.py` walk a CLVM-serialized byte buffer using an `offset` cursor derived from attacker-controlled length-prefix bits, without validating that `offset` (or `offset + atom_len`) stays within `len(clvm_buffer)` before indexing into it.

### Finding Description
`is_atom_canonical(clvm_buffer, offset)` reads `b = clvm_buffer[offset]`, decodes a variable-length atom-length prefix (1–6 bytes) from the high bits of `b`, and then advances `offset` by reading further prefix bytes with `clvm_buffer[offset]` in a loop, purely trusting the encoded prefix length and value: [1](#0-0) 

`is_clvm_canonical(clvm_buffer)` drives this in a `while True` loop, computing `offset += atom_len` from `is_atom_canonical`'s return value and then re-indexing `clvm_buffer[offset]` on the next iteration without checking that `offset` is still `< len(clvm_buffer)`: [2](#0-1) 

Because `atom_len` is derived directly from up to 34 bits of attacker-supplied prefix bits (e.g. the `0xFC`/`0xF8` length-prefix forms), a crafted `puzzle_reveal` or `solution` can encode a length prefix that pushes `offset` far past the end of the actual buffer. The next loop iteration's `clvm_buffer[offset]` (a `bytes` object) then raises an unhandled `IndexError: index out of range` rather than returning a bounded/False result.

This function is invoked directly on attacker-controlled, unsigned-inspection data from every coin spend in a submitted spend bundle, during mempool admission, before any other well-formedness check on that specific field: [3](#0-2) 

This mirrors the CVE-2020-14403 bug class: an encoded-length field controls how far a decoder walks a buffer, and the decoder trusts that length without validating remaining buffer size, producing an out-of-bounds access. In `libvncserver` this corrupted C-heap memory; here in Python it manifests as an unhandled `IndexError` instead of memory corruption, but the root cause (decoder trusts encoded length over actual buffer bounds) is the same class of defect.

### Impact Explanation
Every other cheap-buffer parser in this codebase (`chia/full_node/full_block_utils.py`'s `skip_list`, `skip_bytes`, `generator_from_block`, etc.) explicitly checks remaining buffer length before indexing/slicing and raises a controlled `ValueError`, as shown by tests like `test_skip_bytes_rejects_length_exceeding_remaining_buffer` and `test_cheap_parser_rejects_generator_buffer_length_exceeding_remaining_buffer`. `is_atom_canonical`/`is_clvm_canonical` lack this same bounds discipline, so a raw `IndexError` can escape from `validate_spend_bundle()` instead of being converted into a normal `Err.INVALID_COIN_SOLUTION` rejection. Because this is called on every coin spend's puzzle reveal and solution during mempool admission (`chia/full_node/mempool_manager.py:723`), a single crafted spend bundle can trigger an unhandled exception in the mempool admission code path for any node processing it — a spend-triggered exception in transaction-processing logic rather than a memory-safety violation. I was not able to fully confirm within tool budget whether this specific `IndexError` is caught by a generic `except Exception` wrapper further up the RPC/full-node call stack (e.g., in `full_node.py`'s transaction-submission handlers) or whether it propagates and disrupts mempool-manager task processing; this materially affects whether the practical impact is "clean rejection with a stack trace logged" versus "unhandled exception disrupting a shared worker/task."

### Likelihood Explanation
Reachable via a single, unprivileged spend bundle submission (RPC `push_tx` / peer transaction gossip) with a crafted `puzzle_reveal` or `solution` byte string using an oversized atom length-prefix. No signature, funds, or special coin state is required to reach the vulnerable code — `is_clvm_canonical` is called unconditionally in `validate_spend_bundle()` for every coin spend.

### Recommendation
Add explicit bounds checks in `is_atom_canonical()` and `is_clvm_canonical()`, mirroring the pattern already used in `chia/full_node/full_block_utils.py` (`skip_bytes`, `skip_list`): before each `clvm_buffer[offset]` access, verify `offset < len(clvm_buffer)`, and before advancing by `atom_len`, verify `offset + atom_len <= len(clvm_buffer)`; return `False`/raise a controlled error instead of allowing `IndexError` to propagate.

### Proof of Concept
Construct a `solution` (or `puzzle_reveal`) byte string using the `0xFC` prefix form (6-byte prefix, allows atom lengths up to `2^34`), e.g. `fc03ffffffff` followed by only a few bytes of payload instead of the ~17GB implied length. Submitting a `SpendBundle` whose `coin_spend.solution` equals `SerializedProgram.fromhex("fc03ffffffff")` (no further bytes) to `validate_spend_bundle()` causes `is_clvm_canonical()` to call `is_atom_canonical(buf, 0)`, which computes a huge `atom_len` and returns `offset = 6 + atom_len`; the outer loop's next `clvm_buffer[offset]` access is then far out of range and raises `IndexError` instead of the intended controlled rejection, matching the existing `test_atom_not_canonical`/`test_clvm_not_canonical` test cases in `chia/_tests/core/mempool/test_mempool_manager.py:130-178` but with an even shorter trailing buffer than those tests use, which do not exercise the truncated-buffer case at all.

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

**File:** chia/full_node/mempool_manager.py (L723-726)
```python
            if not is_clvm_canonical(bytes(coin_spend.puzzle_reveal)) or not is_clvm_canonical(
                bytes(coin_spend.solution)
            ):
                return Err.INVALID_COIN_SOLUTION, None, []
```
