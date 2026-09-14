### Title
Out-of-bounds index read in CLVM canonical-form check on unprivileged spend-bundle input - (File: `chia/full_node/mempool_manager.py`)

### Summary
`chia/full_node/mempool_manager.py`'s `is_atom_canonical()`/`is_clvm_canonical()` parse the raw, attacker-controlled CLVM bytes of a submitted spend bundle's puzzle reveal and solution with hand-rolled offset arithmetic and no bounds check against the buffer length, directly analogous to the HDF5 `H5L_extern_query` bug class (untrusted length-prefixed data parsed without validating the read offset stays inside the buffer).

### Finding Description
`is_atom_canonical()` reads a length-prefix byte and then walks `prefix_len` (0–5) additional bytes via `offset += 1; atom_len |= clvm_buffer[offset]` with no check that `offset < len(clvm_buffer)`: [1](#0-0) 

`is_clvm_canonical()` similarly loops `b = clvm_buffer[offset]` while `tokens_left > 0`, only validating trailing-garbage *after* the loop (`offset == len(clvm_buffer)`), so a truncated or malformed atom/pair sequence can drive `offset` past the buffer end before that check is ever reached: [2](#0-1) 

This function is invoked directly on every coin spend's `puzzle_reveal` and `solution` inside `MempoolManager.validate_spend_bundle()`, which is the transaction-admission authority for any spend bundle an unprivileged submitter sends to the node: [3](#0-2) 

Because CLVM allows length-prefixed atoms with up to 5 extra bytes describing the atom size, a submitter can craft a `puzzle_reveal`/`solution` buffer that ends exactly at (or a few bytes before) the end of a multi-byte length prefix, causing `clvm_buffer[offset]` to index past the end of the `bytes` object.

### Impact Explanation
In Python this produces an uncaught `IndexError` rather than a true memory-safety violation, but the effect is a crash of the code path handling the submitted spend bundle. `is_clvm_canonical()` is called unconditionally for every coin spend before any other validation gate in `validate_spend_bundle()`, so a single malicious/malformed spend bundle can raise an unhandled exception in the mempool's admission pipeline (`MempoolManager.add_spend_bundle` → `validate_spend_bundle`). If this exception is not caught by an outer handler, it results in a spend-triggered halt/crash of the transaction-processing task for that node, satisfying the "spend-triggered transaction-processing halt" impact category (mempool availability/DoS), reachable purely from an unprivileged spend-bundle submitter — no signature or special privilege is required to reach this code.

### Likelihood Explanation
Likelihood is high for reaching the vulnerable function: the check runs on every spend, on raw bytes taken straight from the submitted `SpendBundle` (`coin_spend.puzzle_reveal` / `coin_spend.solution`), with no length sanitization before the canonical check. I was not able to confirm, within tool limits, whether a top-level `try/except` around `add_spend_bundle()`/`validate_spend_bundle()` catches all generic exceptions (only `Err`-based control flow was visible in the reviewed code, not a blanket exception handler), so the exact blast radius (task failure vs. broader crash) is uncertain and would need to be confirmed by tracing every caller of `MempoolManager.add_spend_bundle()`.

### Recommendation
Add explicit bounds checks in `is_atom_canonical()` before each `clvm_buffer[offset]` access (raise/return a defined "not canonical"/error result instead of indexing past `len(clvm_buffer)`), and in `is_clvm_canonical()`'s main loop check `offset < len(clvm_buffer)` on every iteration, returning `False` for truncated input instead of relying on Python's exception behavior. Additionally, wrap `validate_spend_bundle()`'s per-spend canonical check (or the whole admission path) so any parsing exception is converted into a normal `Err.INVALID_COIN_SOLUTION` rejection rather than propagating as an unhandled exception.

### Proof of Concept
Construct a coin spend whose `puzzle_reveal` (or `solution`) is a single truncated multi-byte atom length prefix, e.g. hex `f8` (indicates a 5-byte length prefix per the `0b11111000` mask branch) with no following bytes. Submitting a `SpendBundle` containing this coin spend causes `MempoolManager.validate_spend_bundle()` to call `is_clvm_canonical(bytes(coin_spend.puzzle_reveal))` → `is_atom_canonical(clvm_buffer, 0)`, which executes `offset += 1; atom_len |= clvm_buffer[offset]` against an empty remaining buffer, raising `IndexError: index out of range` inside the mempool admission pipeline for that spend bundle. This mirrors the existing project test harness that already exercises `is_atom_canonical`/`is_clvm_canonical` with crafted byte sequences: [4](#0-3)

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

**File:** chia/_tests/core/mempool/test_mempool_manager.py (L181-203)
```python
@pytest.mark.parametrize(
    "clvm_hex, expect",
    [
        ("c040", 2 + 0x40),
        ("e02000", 3 + 0x2000),
        ("f0100000", 4 + 0x100000),
        ("f808000000", 5 + 0x8000000),
        ("fc0400000000", 6 + 0x400000000),
    ],
)
def test_atom_canonical(clvm_hex: str, expect: int) -> None:
    clvm_buf = bytes.fromhex(clvm_hex)
    atom_len, is_canonical = is_atom_canonical(clvm_buf, 0)
    assert atom_len == expect
    assert is_canonical


@pytest.mark.anyio
async def test_bundles_are_canonical(test_bundles: list[SpendBundle]) -> None:
    for sb in test_bundles:
        for spend in sb.coin_spends:
            assert is_clvm_canonical(bytes(spend.puzzle_reveal))
            assert is_clvm_canonical(bytes(spend.solution))
```
