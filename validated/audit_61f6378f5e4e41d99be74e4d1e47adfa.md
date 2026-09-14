## Finding

### Title
Unvalidated CLVM atom length-prefix read causes out-of-bounds index / unhandled exception in mempool canonical-serialization check - (File: `chia/full_node/mempool_manager.py`)

### Summary
`is_atom_canonical()` and `is_clvm_canonical()` parse the raw bytes of a submitted coin spend's puzzle reveal and solution to detect non-canonical CLVM atom-length encodings, but they read the untrusted buffer at increasing offsets without ever checking those offsets against the buffer length. This is the same bug class as CVE-2022-34266: a size/length value taken from untrusted input is used to index/scan a buffer without a bounds check, and a truncated/malformed length field drives the read past the end of the buffer.

### Finding Description
`is_atom_canonical()` decodes the CLVM atom length-prefix format directly from `clvm_buffer[offset]` and then, based on the high bits of that first byte, loops `prefix_len` (0–5) additional times reading `clvm_buffer[offset]` after incrementing `offset` each time — with no check that `offset < len(clvm_buffer)`: [1](#0-0) 

`is_clvm_canonical()` calls this helper while walking the buffer token-by-token, again indexing `clvm_buffer[offset]` with no bound check before dereferencing: [2](#0-1) 

This function is invoked directly on attacker-controlled bytes from every coin spend in a submitted `SpendBundle`, on the mempool admission path reachable by any unprivileged spend-bundle submitter, before any other structural validation of the atom rejects it: [3](#0-2) 

If the last byte(s) of a `puzzle_reveal` or `solution` buffer encode a multi-byte atom length prefix (e.g. `0xF8`/`0xFC` class prefix bytes) that is truncated — i.e., the buffer ends before all `prefix_len` length bytes are present — the `for i in range(prefix_len): ... offset += 1; atom_len |= clvm_buffer[offset]` loop indexes past the end of `clvm_buffer`, raising an unhandled `IndexError` instead of returning a validation error. This mirrors the LibTIFF bug: a length/range value derived from untrusted data is used before validating it is actually within bounds, producing a crash on malformed/malicious input rather than a controlled memory-safety fault (Python's bounds-checked buffers turn a would-be segfault into an uncaught exception).

### Impact Explanation
Because `is_clvm_canonical()` runs synchronously inside `validate_spend_bundle()`, which is called from `add_spend_bundle()` on the mempool admission path for every incoming spend bundle, a crafted spend bundle with a truncated atom-length prefix at the tail of its `puzzle_reveal` or `solution` can raise an uncaught `IndexError` during admission processing. This is a spend-triggered transaction-processing fault: it is reachable purely by submitting a spend bundle (no privileged access, no malicious peer/node assumption required) and disrupts normal admission-pipeline error handling, which is designed to return `Err` codes (e.g. `Err.INVALID_COIN_SOLUTION`) rather than raise.

### Likelihood Explanation
Triggering the malformed input is trivial and fully within the control of any wallet user or offer counterparty submitting a spend bundle — it only requires constructing a `puzzle_reveal`/`solution` byte string whose trailing bytes look like the start of a multi-byte atom length prefix but are cut short. No signature validity or specific puzzle semantics are needed to reach this code path, since `is_clvm_canonical()` executes before deeper puzzle/condition validation.

### Recommendation
Add explicit bounds checks in `is_atom_canonical()` before every `clvm_buffer[offset]` access (both the initial read and the `prefix_len` loop), returning a definitive non-canonical/invalid result (or raising a caught `ValidationError`/`Err.INVALID_COIN_SOLUTION`) when the buffer is too short for the declared prefix length, mirroring the defensive length checks already used elsewhere in the codebase (e.g., `chia/full_node/full_block_utils.py`'s `skip_bytes`/`skip_list`, which explicitly compare declared lengths against remaining buffer size before slicing).

### Proof of Concept
Construct a `puzzle_reveal` or `solution` whose final byte is a multi-byte atom-length-prefix marker with no following length bytes, e.g. a buffer ending in `0xF8` (declares a 4-byte length prefix) with zero trailing bytes. Submit this as a `SpendBundle` coin spend to `MempoolManager.add_spend_bundle()` / `validate_spend_bundle()`; the call to `is_clvm_canonical(bytes(coin_spend.puzzle_reveal))` at `mempool_manager.py:723-726` will invoke `is_atom_canonical()`, whose `offset += 1; atom_len |= clvm_buffer[offset]` loop reads past the end of the buffer and raises `IndexError`, as demonstrated conceptually by the existing canonical-serialization tests that exercise `is_atom_canonical`/`is_clvm_canonical` with crafted byte prefixes: [4](#0-3) .

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
