Confirmed: `is_clvm_canonical()` is called from `MempoolManager.validate_spend_bundle()` (called `pre_validate_spendbundle()` → `validate_spend_bundle()`) on both `coin_spend.puzzle_reveal` and `coin_spend.solution` for every coin spend in a submitted `SpendBundle`, which is directly reachable by an unprivileged spend-bundle submitter through the mempool admission path. [1](#0-0) 

### Title
Unbounded buffer read / crash in CLVM canonical-encoding parser via crafted atom length prefix - (File: chia/full_node/mempool_manager.py)

### Summary
`is_atom_canonical()` walks a raw CLVM-serialized buffer using an attacker-supplied atom length-prefix field, incrementing `offset` and indexing `clvm_buffer[offset]` for up to 5 additional bytes based on the top bits of the first byte, with no check that `offset` stays within `len(clvm_buffer)` before each index. This mirrors the FreeRADIUS `data2vp_wimax()` pattern in CVE-2017-10984: a length/type field taken from untrusted network/wallet input drives sequential buffer reads/writes without validating remaining buffer length, leading to an out-of-bounds access.

### Finding Description
`is_atom_canonical(clvm_buffer, offset)` reads the first byte to determine `prefix_len` (0–5) purely from the top bits of that byte, then loops `prefix_len` times doing `offset += 1; atom_len |= clvm_buffer[offset]` with no bounds check against `len(clvm_buffer)`. [2](#0-1) 

The caller `is_clvm_canonical()` similarly advances `offset` through the buffer in a `while True` loop based solely on decoded lengths, checking only at the very end that `offset == len(clvm_buffer)`; it does not bound-check `clvm_buffer[offset]` before each read either. [3](#0-2) 

This function is invoked on attacker-controlled bytes: `coin_spend.puzzle_reveal` and `coin_spend.solution` from every coin spend in a `SpendBundle` submitted to the mempool, inside `MempoolManager.validate_spend_bundle()`, which runs during normal spend-bundle admission (reachable by any wallet/user/RPC caller submitting a transaction). [4](#0-3) 

Because a puzzle reveal/solution ending in a truncated multi-byte atom-length prefix (e.g., a final byte like `0xFC` claiming 5 more length bytes that don't exist in the buffer) causes `clvm_buffer[offset]` to be indexed past the end of the bytes object, Python raises an uncaught `IndexError` rather than a `ConsensusError`/`ValidationError` that the mempool code is designed to catch.

### Impact Explanation
`validate_spend_bundle()` runs synchronously inside the full node's mempool/blockchain-lock-protected transaction-processing path with no `try/except` around the `is_clvm_canonical()` calls. An uncaught `IndexError` propagating from this path is not one of the handled exception types (`ValueError`/`ConsensusError`) elsewhere in the pipeline, so it can crash or halt request processing for the calling coroutine/task, and depending on the caller (e.g. `full_node.add_transaction`), can escape as an unhandled exception, causing a spend-triggered denial of service on the transaction-processing path for a single submitted spend bundle — consistent with the CVE's "denial of service (daemon crash)" characterization for an unbounded/under-checked length-prefix parse.

### Likelihood Explanation
Any unprivileged party who can submit a `SpendBundle` (wallet user, RPC caller, or arbitrary peer forwarding a transaction) can trivially construct a `puzzle_reveal` or `solution` whose final atom has a truncated multi-byte length prefix (an atom marker byte such as `0xF8`/`0xFC` at the very end of the buffer with no following length bytes). No signature validity or coin ownership is required to reach this code, since `is_clvm_canonical` is called before/independent of full puzzle execution success gating in `validate_spend_bundle`. This makes the trigger low-effort and highly likely to be reachable by a remote unauthenticated submitter.

### Recommendation
Add explicit bounds checks in `is_atom_canonical()` and `is_clvm_canonical()` before every `clvm_buffer[offset]` access (e.g., raise a caught `ValueError`/return non-canonical if `offset >= len(clvm_buffer)` at each step, mirroring the bounds checks already present in `chia/full_node/full_block_utils.py`'s `skip_bytes`/`skip_list` functions). Ensure `validate_spend_bundle()` (or its caller) wraps `is_clvm_canonical()` invocations so any parsing exception is converted into `Err.INVALID_COIN_SOLUTION` instead of propagating as an unhandled exception.

### Proof of Concept
1. Craft a `puzzle_reveal` or `solution` byte buffer whose last byte is `0xFC` (indicating the "5-byte length prefix" atom-length encoding per `is_atom_canonical`'s `0b11111100`/`0b11111110` branch) with no subsequent bytes in the buffer.
2. Wrap it into a `CoinSpend`/`SpendBundle` and submit via `add_transaction`/mempool RPC so it reaches `MempoolManager.validate_spend_bundle()`.
3. `is_clvm_canonical(bytes(coin_spend.puzzle_reveal))` calls `is_atom_canonical(clvm_buffer, offset)`, which attempts `clvm_buffer[offset]` past the buffer end, raising an uncaught `IndexError` inside the mempool validation path, rather than the intended `Err.INVALID_COIN_SOLUTION`. [5](#0-4) [1](#0-0)

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

**File:** chia/full_node/mempool_manager.py (L713-726)
```python

        bundle_coin_spends: dict[bytes32, BundleCoinSpend] = {}
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
