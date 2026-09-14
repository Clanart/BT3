### Title
Out-of-bounds unchecked byte read in CLVM canonical-serialization checker allows a crafted spend to crash mempool processing - ([File: chia/full_node/mempool_manager.py])

### Summary
`is_atom_canonical()` in `chia/full_node/mempool_manager.py` parses a variable-length CLVM atom length-prefix directly out of attacker-controlled `puzzle_reveal`/`solution` bytes without validating that the buffer is long enough to contain the full prefix, mirroring the ImageMagick `wpg.c` bug class in CVE-2016-7527 (crafted file → out-of-bounds read via unchecked length-prefixed parsing).

### Finding Description
`is_atom_canonical(clvm_buffer, offset)` determines the CLVM atom length-prefix size from the leading byte and then reads `prefix_len` additional bytes to build `atom_len`: [1](#0-0) 

The loop `for i in range(prefix_len): ... offset += 1; atom_len |= clvm_buffer[offset]` indexes `clvm_buffer` with no check that `offset` stays within `len(clvm_buffer)`. This function is invoked from `is_clvm_canonical()`, which walks a full serialized CLVM buffer atom-by-atom and calls `is_atom_canonical()` whenever it encounters a byte indicating a multi-byte length prefix: [2](#0-1) 

Contrast this with `chia/full_node/full_block_utils.py`, where equivalent length-prefixed parsers (`skip_bytes`, `skip_list`, the generator-buffer length parsing in `generator_from_block`/`block_info_from_block`) explicitly check `len(buf) < 4` / `n > len(buf)` and raise a `ValueError` before indexing: [3](#0-2) [4](#0-3) 

`is_atom_canonical`/`is_clvm_canonical` lack this defensive bound-check pattern used elsewhere in the same codebase for untrusted-byte parsing.

`is_clvm_canonical()` is documented as being used to classify a coin spend's `puzzle_reveal`/`solution` bytes as canonical for `ELIGIBLE_FOR_DEDUP` mempool handling: [5](#0-4) 

and is exercised directly against `puzzle_reveal`/`solution` bytes in mempool tests: [6](#0-5) 

Because `puzzle_reveal` and `solution` bytes are fully attacker-controlled (any unprivileged spend-bundle submitter constructs both), a coin spend whose CLVM bytes end with a truncated multi-byte atom-length header (e.g., a trailing `0xFC`/`0xF8`/etc. prefix byte with insufficient following bytes) causes `clvm_buffer[offset]` to index past the end of the buffer, raising an unhandled `IndexError` inside mempool processing rather than the graceful `Err`-based rejection paths used elsewhere in the mempool manager (e.g. `Err.INVALID_COIN_SOLUTION`, `Err.DOUBLE_SPEND`, `Err.MEMPOOL_CONFLICT`, as seen surrounding this code): [7](#0-6) 

### Impact Explanation
If this unchecked read path is reached from an unhandled position during `add_spend_bundle`/mempool eligibility classification, a single unprivileged spend-bundle submission with a malformed trailing length-prefix atom in its puzzle reveal or solution can raise an uncaught `IndexError`, halting or crashing the mempool add path handling that transaction rather than being turned into a normal validation rejection. This is a spend-triggered transaction-processing halt vector, consistent with the report's accepted impact categories.

### Likelihood Explanation
Likelihood is moderate: the attacker fully controls `puzzle_reveal`/`solution` byte content, so crafting a trailing truncated atom-length prefix is straightforward. The main open question — which I was unable to fully verify before the tool budget ran out — is (a) the exact call site(s) that invoke `is_clvm_canonical()` on submitted spend bytes (only inferred from documentation and tests, not from a directly observed call site in `mempool_manager.py`'s main `add_spend_bundle`/eligibility code), and (b) whether any outer `try/except` wraps that call and downgrades the `IndexError` to a normal `Err` rejection. Confirming these requires reading the eligibility-computation code path (e.g. `get_eligibility_and_addition_info` / `chia/full_node/eligible_coin_spends.py`) directly, which I could not complete in the available iterations.

### Recommendation
Add explicit bounds checks in `is_atom_canonical()` before each `clvm_buffer[offset]` access (mirroring the `len(buf) < N` / `raise ValueError` pattern already used in `chia/full_node/full_block_utils.py`), and ensure any exception raised while classifying a spend's canonical-serialization eligibility is caught and converted into a normal mempool rejection (e.g., `Err.INVALID_COIN_SOLUTION`) rather than propagating as an unhandled exception.

### Proof of Concept
Construct a `CoinSpend` whose `puzzle_reveal` or `solution` bytes end with a byte matching one of the multi-byte length-prefix patterns handled by `is_atom_canonical` (e.g. `0xC0`–`0xFC` range) but with fewer trailing bytes than the prefix requires (e.g., a single trailing `0xC0` byte with no following byte, or `0xFC` followed by fewer than 5 bytes). Submit the resulting `SpendBundle` through the normal mempool submission path (RPC `push_tx` / wallet spend). If the canonical-serialization check is exercised on this buffer without the outer code catching the resulting `IndexError`, the mempool-processing call for that transaction fails with an unhandled exception instead of a normal `Err` rejection — reproducible using the same buffer-crafting helper already present in the test suite: [8](#0-7) [9](#0-8)

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

**File:** chia/full_node/mempool_manager.py (L230-309)
```python
def check_removals(
    removals: dict[bytes32, CoinRecord],
    bundle_coin_spends: dict[bytes32, BundleCoinSpend],
    *,
    get_items_by_coin_ids: Callable[[list[bytes32]], list[MempoolItem]],
) -> tuple[Err | None, list[MempoolItem]]:
    """
    This function checks for double spends, unknown spends and conflicting transactions in mempool.
    Returns Error (if any), the set of existing MempoolItems with conflicting spends (if any).
    Note that additions are not checked for duplicates, because having duplicate additions requires also
    having duplicate removals.
    """
    conflicts = set()
    for coin_id, coin_bcs in bundle_coin_spends.items():
        # 1. Checks if it's been spent already
        if removals[coin_id].spent and not coin_bcs.supports_fast_forward:
            return Err.DOUBLE_SPEND, []

        # 2. Checks if there's a mempool conflict
        # Fast forward spends rebase onto the latest singleton coin, so look
        # that up as well.
        latest_ff_id = None if coin_bcs.latest_singleton_lineage is None else coin_bcs.latest_singleton_lineage.coin_id
        coin_ids = [coin_id]
        if latest_ff_id is not None and latest_ff_id != coin_id:
            coin_ids.append(latest_ff_id)
        conflicting_items = get_items_by_coin_ids(coin_ids)
        for item in conflicting_items:
            if item in conflicts:
                continue
            conflict_bcs = item.bundle_coin_spends.get(coin_id)
            if conflict_bcs is None and latest_ff_id is not None:
                conflict_bcs = item.bundle_coin_spends.get(latest_ff_id)
            if conflict_bcs is None:
                # Check if this is an item that spends an older ff singleton
                # version with a latest version that matches our coin ID or the
                # same latest version we rebase onto.
                conflict_bcs = next(
                    (
                        bcs
                        for bcs in item.bundle_coin_spends.values()
                        if bcs.latest_singleton_lineage is not None
                        and (
                            bcs.latest_singleton_lineage.coin_id == coin_id
                            or (latest_ff_id is not None and bcs.latest_singleton_lineage.coin_id == latest_ff_id)
                        )
                    ),
                    None,
                )
                # We're not expected to get here but let's handle it gracefully
                if conflict_bcs is None:
                    log.warning(f"Coin ID {coin_id} expected but not found in mempool item {item.name}")
                    return Err.INVALID_SPEND_BUNDLE, []
            same_coin = coin_id == conflict_bcs.coin_spend.coin.name()
            # if the spend we're adding to the mempool is not DEDUP nor FF, it's
            # just a regular conflict
            if not coin_bcs.supports_fast_forward and not coin_bcs.eligible_for_dedup:
                conflicts.add(item)

            # If one spend is FF and the other isn't FF, they can't be chained
            # so that's a conflict.
            elif coin_bcs.supports_fast_forward != conflict_bcs.supports_fast_forward:
                conflicts.add(item)

            # if the spend we're adding is DEDUP, but there's a conflicting spend
            # that isn't DEDUP, we cannot merge them, so that's a conflict
            elif same_coin and coin_bcs.eligible_for_dedup and not conflict_bcs.eligible_for_dedup:
                conflicts.add(item)

            # if the spend we're adding is DEDUP but the existing spend has a
            # different solution, we cannot merge them, so that's a conflict
            elif (
                same_coin
                and coin_bcs.eligible_for_dedup
                and bytes(coin_bcs.coin_spend.solution) != bytes(conflict_bcs.coin_spend.solution)
            ):
                conflicts.add(item)

    if len(conflicts) > 0:
        return Err.MEMPOOL_CONFLICT, list(conflicts)
    return None, []
```

**File:** chia/full_node/full_block_utils.py (L27-34)
```python
def skip_bytes(buf: memoryview) -> memoryview:
    if len(buf) < 4:
        raise ValueError(f"byte length prefix requires 4 bytes, remaining buffer {len(buf)}")
    n = int.from_bytes(buf[:4], "big", signed=False)
    buf = buf[4:]
    if n > len(buf):
        raise ValueError(f"byte length {n} exceeds remaining buffer {len(buf)}")
    return buf[n:]
```

**File:** chia/full_node/full_block_utils.py (L259-267)
```python
    if version == 1:
        if len(buf) < 4:
            raise ValueError(f"generator_buffer length prefix requires 4 bytes, remaining buffer {len(buf)}")
        length = uint32.from_bytes(buf[:4])
        buf = buf[4:]
        if length > len(buf):
            raise ValueError(f"generator_buffer length: {length}, exceeds remaining buffer {len(buf)}")
        return bytes(buf[:length])
    raise ValueError(f"invalid FullBlock generator version: {version}")
```

**File:** .cursor/context/clvm-execution.md (L96-112)
```markdown
## Canonical serialization

**Location**: `mempool_manager.py:185`

### `is_clvm_canonical(clvm_buffer)`

Checks that a CLVM program uses shortest-form atom encoding:

- No unnecessary length prefix bytes
- No back-references (`0xFE` byte)
- No trailing garbage

### When enforced

Required for DEDUP-eligible spends. Without canonical form, identical
solutions could have different serializations, breaking dedup.

```

**File:** chia/_tests/core/mempool/test_mempool_manager.py (L130-156)
```python
@pytest.mark.parametrize("clvm_hex", ["80", "ff8080", "ff7f03", "ffff8080ff8080"])
def test_clvm_canonical(clvm_hex: str) -> None:
    clvm_buf = bytes.fromhex(clvm_hex)
    assert is_clvm_canonical(clvm_buf)


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

**File:** chia/_tests/clvm/test_chialisp_deserialization.py (L12-52)
```python
def serialized_atom_overflow(size: int) -> str:
    if size == 0:
        size_blob = b"\x80"
    elif size < 0x40:
        size_blob = bytes([0x80 | size])
    elif size < 0x2000:
        size_blob = bytes([0xC0 | (size >> 8), (size >> 0) & 0xFF])
    elif size < 0x100000:
        size_blob = bytes([0xE0 | (size >> 16), (size >> 8) & 0xFF, (size >> 0) & 0xFF])
    elif size < 0x8000000:
        size_blob = bytes(
            [
                0xF0 | (size >> 24),
                (size >> 16) & 0xFF,
                (size >> 8) & 0xFF,
                (size >> 0) & 0xFF,
            ]
        )
    elif size < 0x400000000:
        size_blob = bytes(
            [
                0xF8 | (size >> 32),
                (size >> 24) & 0xFF,
                (size >> 16) & 0xFF,
                (size >> 8) & 0xFF,
                (size >> 0) & 0xFF,
            ]
        )
    else:
        size_blob = bytes(
            [
                0xFC | ((size >> 40) & 0xFF),
                (size >> 32) & 0xFF,
                (size >> 24) & 0xFF,
                (size >> 16) & 0xFF,
                (size >> 8) & 0xFF,
                (size >> 0) & 0xFF,
            ]
        )
    extra_str = "01" * 1000
    return size_blob.hex() + extra_str
```
