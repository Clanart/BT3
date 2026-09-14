Based on my investigation, `add_spend_bundle` calls `validate_spend_bundle`, which is `await`ed directly with no surrounding `try/except` for arbitrary exceptions like `IndexError`. The `is_clvm_canonical`/`is_atom_canonical` check runs on raw, attacker-controlled bytes (`coin_spend.puzzle_reveal`, `coin_spend.solution`) submitted via an ordinary spend bundle, before any wrapping exception handling that would normalize it into a `ValidationError`/`Err`.

### Title
Unhandled IndexError in `is_atom_canonical`/`is_clvm_canonical` from malformed puzzle_reveal/solution length prefixes - (File: chia/full_node/mempool_manager.py)

### Summary
`is_atom_canonical` and `is_clvm_canonical` in `chia/full_node/mempool_manager.py` walk an untrusted CLVM byte buffer using length-prefix bytes taken directly from `coin_spend.puzzle_reveal`/`coin_spend.solution`, without validating that the declared prefix length or computed atom length actually fits within the remaining buffer before indexing into it, mirroring the dnsmasq `extract_name()`/`get_rdata()` pattern of trusting a length field before bounds-checking the buffer.

### Finding Description [1](#0-0) 
`is_atom_canonical(clvm_buffer, offset)` reads `clvm_buffer[offset]` to determine `prefix_len`, then loops `prefix_len` times doing `offset += 1; atom_len |= clvm_buffer[offset]` with no check that `offset < len(clvm_buffer)` at any point. It returns `1 + prefix_len + atom_len` as the consumed length, again without verifying that value is within the buffer. [2](#0-1) 
`is_clvm_canonical` drives this in a loop: it reads `clvm_buffer[offset]`, and for atom bytes it calls `is_atom_canonical` and then does `offset += atom_len` — trusting the attacker-supplied `atom_len` value (which can be up to `2**40`-ish per the 6-byte prefix format) to advance `offset`, then on the next loop iteration re-indexes `clvm_buffer[offset]` again with no bound check. Since `atom_len` is derived purely from attacker-controlled bytes and is never clamped to `len(clvm_buffer) - offset`, a crafted `puzzle_reveal` or `solution` can push `offset` past the end of the buffer, causing `clvm_buffer[offset]` to raise an `IndexError`.

This function is called directly on attacker-controlled data from a submitted `SpendBundle`: [3](#0-2) 
`validate_spend_bundle` invokes `is_clvm_canonical(bytes(coin_spend.puzzle_reveal))` / `is_clvm_canonical(bytes(coin_spend.solution))` for every coin spend in the bundle, before conflict/lineage checks.

`add_spend_bundle` calls `await self.validate_spend_bundle(...)` with no `try/except` around the call: [4](#0-3) 
An `IndexError` raised here is not caught and converted into an `Err`/`ValidationError` like the CLVM cost/signature validation path (which does have a dedicated `except ValueError` handler, see lines 560-567), so it propagates up uncaught through the mempool add path.

### Impact Explanation
This is analogous in bug-class to CVE-2020-25683: a length value taken from untrusted, length-prefixed input is used to advance a read cursor without verifying the buffer actually contains that many remaining bytes, leading to an out-of-bounds access. In dnsmasq this manifested as a heap overflow/crash; here in Python it manifests as an unhandled `IndexError` exception propagating out of the normal validated-error path of spend bundle processing. If this exception is not caught by an outer handler in the RPC/mempool-add call chain, it can crash or abort processing of that operation, effectively a transaction-processing halt triggerable by any unprivileged party submitting a single spend bundle with a crafted, non-canonical, truncated atom length prefix in `puzzle_reveal` or `solution`.

### Likelihood Explanation
High reachability: any wallet user, RPC caller, or peer that can submit a spend bundle (`push_tx`) controls `puzzle_reveal` and `solution` bytes directly, and this check runs unconditionally for every coin spend in `validate_spend_bundle`, prior to any CLVM execution cost gating. Crafting a truncated atom-length-prefix atom (e.g., `\xff\xff\xff\xff\xff` as the last few bytes of the buffer, using the 6-byte prefix form which allows a very large declared `atom_len`) is straightforward and requires no cryptographic material or privileged access.

### Recommendation
In `is_atom_canonical`, bounds-check `offset + prefix_len` against `len(clvm_buffer)` before the prefix-reading loop, and in `is_clvm_canonical`, verify `offset + atom_len <= len(clvm_buffer)` before advancing `offset` by `atom_len`, raising/returning a controlled "not canonical" result instead of allowing an out-of-bounds index. Additionally, wrap the call into `validate_spend_bundle`/`is_clvm_canonical` (or the whole `add_spend_bundle` path) so any unexpected exception is translated into a normal `Err`/`MempoolInclusionStatus.FAILED` result rather than propagating uncaught.

### Proof of Concept
1. Construct a `puzzle_reveal` (or `solution`) whose final byte is a non-canonical length-prefix marker with an insufficient number of trailing bytes to satisfy the declared prefix, e.g. bytes ending in `\xfc` (6-byte prefix format) followed by fewer than 5 remaining bytes, or a well-formed prefix whose decoded `atom_len` exceeds the actual remaining buffer length (e.g. `c0` followed by `3f` claiming 63 bytes of atom data but the buffer ends immediately after).
2. Submit a `SpendBundle` containing a `CoinSpend` with this `puzzle_reveal`/`solution` via `push_tx`/mempool submission.
3. `validate_spend_bundle` calls `is_clvm_canonical(bytes(coin_spend.puzzle_reveal))`, which calls `is_atom_canonical`, which indexes past the end of `clvm_buffer`, raising an unhandled `IndexError` that propagates out of `add_spend_bundle`.

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

**File:** chia/full_node/mempool_manager.py (L640-647)
```python
        err, item, remove_items = await self.validate_spend_bundle(
            new_spend,
            conds,
            spend_name,
            first_added_height,
            get_coin_records,
            get_unspent_lineage_info_for_puzzle_hash,
        )
```

**File:** chia/full_node/mempool_manager.py (L723-726)
```python
            if not is_clvm_canonical(bytes(coin_spend.puzzle_reveal)) or not is_clvm_canonical(
                bytes(coin_spend.solution)
            ):
                return Err.INVALID_COIN_SOLUTION, None, []
```
