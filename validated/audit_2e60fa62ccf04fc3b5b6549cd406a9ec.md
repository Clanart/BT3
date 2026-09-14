### Title
Missing bounds checks in `is_atom_canonical`/`is_clvm_canonical` allow a crafted CLVM length-prefix offset to run past the end of an attacker-supplied spend-bundle buffer - (File: chia/full_node/mempool_manager.py)

### Summary
`MempoolManager.validate_spend_bundle()` runs `is_clvm_canonical()` on the raw, attacker-controlled `puzzle_reveal` and `solution` bytes of every coin spend in a submitted spend bundle, before the bundle is admitted to the mempool. `is_clvm_canonical()`/`is_atom_canonical()` walk this buffer using an `offset` index derived from a CLVM atom length-prefix that is itself read from the same untrusted buffer, and they never validate that `offset` (or the derived read positions) stay within `len(clvm_buffer)` before indexing into it.

### Finding Description
`is_atom_canonical()` decodes a variable-length (1–6 byte) atom length prefix directly out of `clvm_buffer` and increments `offset` for each prefix byte, reading `clvm_buffer[offset]` in a loop with no bound check against `len(clvm_buffer)`: [1](#0-0) 

`is_clvm_canonical()` similarly indexes `clvm_buffer[offset]` in its main loop, and after calling `is_atom_canonical()` advances `offset` by the (attacker-controlled) computed `atom_len` without ever re-checking that `offset` is still within bounds before the next loop iteration reads `clvm_buffer[offset]` again: [2](#0-1) 

This is exactly analogous to CVE-2016-4998's root cause: a length/offset value taken from untrusted input is used to index into a buffer without validating that it stays inside the buffer's boundary. In the kernel case this produced an out-of-bounds heap read; here, because Python `bytes`/`memoryview` indexing is bounds-checked, an out-of-range `offset` raises an unhandled `IndexError` instead of leaking heap memory, but the underlying defect — deriving an offset from untrusted data and indexing without a length check — is the same bug class.

Both functions are reached directly from spend-bundle admission, on data an unprivileged submitter fully controls: [3](#0-2) 

A crafted `puzzle_reveal` or `solution` whose final bytes are a truncated multi-byte atom-length prefix (e.g., ending right after a `0xF8`/`0xFC` prefix byte, or with an atom length that runs past the end of the buffer) causes `clvm_buffer[offset]` to be evaluated with `offset >= len(clvm_buffer)`, raising an `IndexError` that is not caught anywhere along this call path.

### Impact Explanation
`validate_spend_bundle()` is invoked synchronously (not inside a `try/except` around the canonicality check) as part of the standard mempool admission path for any submitted spend bundle. An unhandled `IndexError` here corresponds to the "spend-triggered transaction-processing halt" impact category: a single malformed but otherwise well-formed spend bundle (crafted `puzzle_reveal`/`solution` bytes) can throw an unhandled exception during mempool admission processing, rather than being cleanly rejected with `Err.INVALID_COIN_SOLUTION`. Depending on how the calling task/coroutine handles unexpected exceptions, this can disrupt spend-bundle processing for that request and, if not isolated, affect the full node's mempool-admission task.

### Likelihood Explanation
High from a reachability standpoint: `is_clvm_canonical()` is called unconditionally for every coin spend in every spend bundle submitted to the mempool, including via RPC `push_tx` or wallet transaction submission — no privileged access is required. The buffer content (`puzzle_reveal`, `solution`) is fully attacker-controlled and only needs to be a bytes blob ending in (or containing) a truncated/oversized atom length-prefix sequence; no valid signature or successful CLVM execution is required to reach this code, since it runs during pre-execution admission checks.

### Recommendation
Add explicit bounds checks in both `is_atom_canonical()` and `is_clvm_canonical()`: before each `clvm_buffer[offset]` access, verify `offset < len(clvm_buffer)`, and when accumulating the multi-byte atom length prefix, verify there are enough remaining bytes for the declared `prefix_len` before reading them. Similarly, after computing `atom_len`, verify `1 + prefix_len + atom_len <= len(clvm_buffer) - start_offset` before treating it as valid, returning "not canonical" (or raising a caught, well-defined `ValueError`) instead of allowing an `IndexError` to propagate.

### Proof of Concept
1. Construct a `puzzle_reveal` or `solution` byte buffer whose last byte is `0xFC` (indicating a 6-byte, i.e., `prefix_len = 5`, atom-length prefix) with no further bytes following it (or fewer than 5 following bytes).
2. Submit a spend bundle (e.g., via the wallet RPC `push_tx`) whose `CoinSpend.puzzle_reveal` (or `.solution`) is this crafted buffer.
3. In `MempoolManager.validate_spend_bundle()`, the call `is_clvm_canonical(bytes(coin_spend.puzzle_reveal))` reaches `is_atom_canonical()`, which attempts `atom_len |= clvm_buffer[offset]` for `offset` values beyond `len(clvm_buffer) - 1`, raising an unhandled `IndexError` instead of a controlled rejection. [4](#0-3) [5](#0-4)

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

**File:** chia/full_node/mempool_manager.py (L196-227)
```python
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

**File:** chia/full_node/mempool_manager.py (L715-726)
```python
        for coin_spend in new_spend.coin_spends:
            coin_id = coin_spend.coin.name()
            removal_names.add(coin_id)

            # if this coin_id isn't found, the SpendBundle doesn't match the
            # SpendBundleConditions.
            spend_conds = spend_conditions.pop(coin_id)

            if not is_clvm_canonical(bytes(coin_spend.puzzle_reveal)) or not is_clvm_canonical(
                bytes(coin_spend.solution)
            ):
                return Err.INVALID_COIN_SOLUTION, None, []
```
