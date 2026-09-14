### Title
Unbounded index read in `is_atom_canonical` allows a spend-triggered `IndexError` crash during mempool admission - (File: chia/full_node/mempool_manager.py)

### Summary
The reported CVE-2023-6610 is an out-of-bounds read in a length-prefixed buffer parser (`smb2_dump_detail`) reachable by an unprivileged local actor. The analogous bug-class in this repo is `is_atom_canonical`, a hand-rolled variable-length CLVM atom-length-prefix parser in `chia/full_node/mempool_manager.py` that walks a `bytes` buffer based on an attacker-controlled length-prefix field without validating that the prefix bytes actually exist in the buffer.

### Finding Description
`is_atom_canonical(clvm_buffer, offset)` reads `clvm_buffer[offset]` to get a leading byte, determines `prefix_len` (0–5) from the top bits, and then unconditionally loops `prefix_len` times reading `clvm_buffer[offset]` after incrementing `offset` each time: [1](#0-0) 

There is no check that `offset` stays within `len(clvm_buffer)` before each indexed read. `is_clvm_canonical` (the caller) also indexes `clvm_buffer[offset]` in its main loop without any bounds check, relying entirely on the assumption that a valid CLVM discriminant byte is always followed by enough trailing bytes: [2](#0-1) 

Both `is_clvm_canonical` and (transitively) `is_atom_canonical` are invoked unconditionally on every coin spend's `puzzle_reveal` and `solution` during mempool admission, in `MempoolManager.validate_spend_bundle`, which is on the direct path from `add_spend_bundle` for any submitted spend bundle: [3](#0-2) 

Because `puzzle_reveal` and `solution` are fully attacker-controlled byte strings, an attacker can craft a coin spend whose CLVM byte stream ends with a length-prefix discriminant byte (e.g. `0xFC`, which claims a 5-byte length prefix) placed at or near the end of the buffer, with too few trailing bytes. In Python, unlike the C `smb2_dump_detail` case, this does not leak adjacent heap memory (Python `bytes` indexing is bounds-checked), but it does raise an unhandled `IndexError` instead of the intended `ValueError`/rejection path.

### Impact Explanation
This is reachable by any unprivileged spend-bundle submitter (wallet client, RPC caller, or network peer relaying a transaction) without needing a valid signature or successful CLVM execution first — the canonical-form check runs on every spend in `validate_spend_bundle`, which executes inside the full node's mempool-add code path under the blockchain lock. An `IndexError` raised here is not a `ValueError`/`ValidationError` that the surrounding code is designed to catch and convert into a rejection status (`Err.INVALID_COIN_SOLUTION`); it propagates as an unexpected exception out of `validate_spend_bundle`/`add_spend_bundle`. Depending on the caller context (full_node's mempool add path, RPC `push_tx`), this can surface as an unhandled exception in transaction processing, i.e. a spend-triggered processing halt for that call path, rather than a memory-safety compromise. This is a denial-of-service class issue, not silent coin-set divergence, forgery, or theft.

### Likelihood Explanation
High likelihood of triggering the crash: the input is a single crafted `puzzle_reveal`/`solution` byte sequence attached to any spend, requiring no special privileges, no valid signatures for reaching this check (the canonical check runs before/independent of signature validation in this function), and no coordination with other participants.

### Recommendation
Add explicit bounds checks in `is_atom_canonical` before each indexed read of `clvm_buffer[offset]` (raise a clean `ValueError`/return non-canonical instead of indexing past the buffer), and add an equivalent bounds check at the top of the `is_clvm_canonical` loop before reading `clvm_buffer[offset]`. Ensure `IndexError` from these helpers (or any residual case) is caught in `validate_spend_bundle` and converted to `Err.INVALID_COIN_SOLUTION` rather than allowed to propagate.

### Proof of Concept
1. Construct a `CoinSpend` whose `solution` (or `puzzle_reveal`) bytes are exactly `b"\xfc"` (a single byte). This byte has the top bits `11111100`, matching the `0b11111100 == 0b11111000` branch in `is_atom_canonical`, which sets `prefix_len = 5` and `mask = 0b00000011`.
2. Wrap it as `SerializedProgram.fromhex("fc")` and submit a `SpendBundle` containing this coin spend to a full node (e.g., via `push_tx` RPC or peer transaction relay).
3. During `validate_spend_bundle`, `is_clvm_canonical` calls `is_atom_canonical(buf, 0)` on this single-byte buffer; the loop attempts `offset += 1; atom_len |= clvm_buffer[offset]` with `offset = 1` against a 1-byte buffer, raising `IndexError: index out of range` instead of returning a canonical-check failure.
4. This exception is not a `ValueError`, so it is not converted to a `ValidationError`/`Err.INVALID_COIN_SOLUTION` result and propagates out of the mempool add path for that call.

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
