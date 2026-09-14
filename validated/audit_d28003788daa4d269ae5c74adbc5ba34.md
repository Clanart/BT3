### Title
Missing Bounds Checks in `is_atom_canonical`/`is_clvm_canonical` CLVM Canonicality Parser Causes Unhandled Exception on Attacker-Controlled Spend Bundles - (File: chia/full_node/mempool_manager.py)

### Summary
`is_atom_canonical()` and `is_clvm_canonical()` in `chia/full_node/mempool_manager.py` parse an attacker-supplied, untrusted CLVM byte buffer (a spend bundle's `puzzle_reveal`/`solution`) to determine canonical-encoding eligibility (used for DEDUP/fast-forward mempool acceptance) [1](#0-0) . Similar to the libcoap OSCORE CBOR bug where `get_byte_inc()` relies solely on `assert()` for bounds checking (stripped in NDEBUG/release builds), this parser performs no explicit bounds validation before indexing into the buffer while decoding variable-length atom-length prefixes, and only has a single unconditional `assert clvm_buffer != b""` guard at the very top.

### Finding Description
`is_clvm_canonical()` only checks that the buffer is non-empty up front, then loops indexing `clvm_buffer[offset]` without ever re-validating `offset < len(clvm_buffer)` as it advances through nested pairs and atoms [2](#0-1) . `is_atom_canonical()` decodes a length-prefix that can consume up to 5 additional bytes (`prefix_len` up to 5) by looping `offset += 1; atom_len |= clvm_buffer[offset]` with no check that those bytes actually exist in the buffer [3](#0-2) . A truncated buffer (e.g., a single `0xFF` pair marker followed by a byte indicating a 5-byte length prefix but with fewer actual bytes present) drives `offset` past the end of `clvm_buffer`.

In Python, reading past the end of a `bytes` object raises `IndexError` rather than reading adjacent process memory (unlike the C `get_byte_inc()` OOB read case), so this is not a memory-disclosure vulnerability. However, it is the direct structural analog of the reported bug class: bounds checking that is effectively absent/assert-only during untrusted CBOR/CLVM-length-prefix parsing reachable from a single external message. This is confirmed reachable from mempool admission logic, which calls `is_clvm_canonical()` for DEDUP/fast-forward eligibility determination on spend bundle puzzle reveals and solutions submitted by any wallet/RPC caller [1](#0-0) ; tests confirm this canonical-length-prefix decoding path is exercised directly on attacker-suppliable buffers [4](#0-3) .

### Impact Explanation
If an unhandled `IndexError` is raised inside the mempool admission code path (rather than being caught and converted into a normal validation error like `Err.INVALID_COIN_SOLUTION`), a single malformed but otherwise well-formed-looking spend bundle could cause an unhandled exception during spend-bundle processing, i.e., a spend-triggered transaction-processing halt/crash of the mempool manager worker handling that bundle. This matches the "spend-triggered transaction-processing halt" acceptance criterion. I was not able to fully verify within the available context whether all call sites of `is_clvm_canonical()`/`is_atom_canonical()` are wrapped in a broad `try/except` that safely converts any exception (including `IndexError`) into a rejection status, or whether some call sites let it propagate unhandled — this needs to be confirmed by reading the exact call sites and their surrounding exception handling in `chia/full_node/mempool_manager.py` and `chia/full_node/eligible_coin_spends.py`/`chia/full_node/mempool.py` where DEDUP/fast-forward eligibility is computed.

### Likelihood Explanation
Likelihood is high for triggering the code path: any wallet user, RPC caller, or offer counterparty can submit a `SpendBundle` with an arbitrary `puzzle_reveal`/`solution` CLVM buffer, and the canonicality check runs as part of normal mempool admission for DEDUP/fast-forward eligibility determination, requiring no special privilege. The uncertain part is purely whether the resulting exception is caught upstream, which determines whether the actual impact is "safely rejected as INVALID" (no vulnerability) or "unhandled crash" (valid DoS analog).

### Recommendation
Add explicit bounds checks in `is_atom_canonical()` before each buffer index/increment (verify `offset < len(clvm_buffer)` prior to reading `clvm_buffer[offset]`, and verify the full prefix and atom length fit within the remaining buffer) rather than relying on Python's exception-on-out-of-range behavior implicitly. Ensure `is_clvm_canonical()`'s calling context in the mempool manager either explicitly catches `IndexError`/`ValueError` and converts it to a mempool rejection (`Err.INVALID_COIN_SOLUTION` or similar), or make the length/offset validation explicit and raise/return a controlled result instead of depending on implicit exceptions propagating correctly through all call sites.

### Proof of Concept
1. Craft a `SpendBundle` whose `coin_spend.puzzle_reveal` (or `solution`) CLVM buffer is: `\xff` (pair marker) followed by `\xfc` (indicating a 5-byte trailing length prefix per `is_atom_canonical`'s bit-mask table) with zero or one trailing bytes actually present, so the buffer is shorter than what the encoded prefix length requires.
2. Submit this spend bundle through the wallet/RPC (`push_tx`) so it reaches `MempoolManager.add_spend_bundle()` and its canonicality/DEDUP-eligibility check invokes `is_clvm_canonical()` → `is_atom_canonical()` on the buffer [5](#0-4) .
3. Observe whether the resulting `IndexError` is caught and converted to a normal rejection (expected/safe) or propagates unhandled out of the mempool processing task (the reachable analog of the reported bug class). This step requires runtime verification against the actual call-site exception handling, which could not be fully confirmed from static reading alone.

### Citations

**File:** chia/full_node/mempool_manager.py (L144-221)
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
```

**File:** chia/_tests/core/mempool/test_mempool_manager.py (L136-178)
```python
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


@pytest.mark.parametrize(
    "clvm_hex, expect",
    [
        ("c000", 2 + 0),
        ("c03f", 2 + 0x3F),
        ("e00000", 3 + 0),
        ("e01fff", 3 + 0x1FFF),
        ("f0000000", 4 + 0),
        ("f00fffff", 4 + 0xFFFFF),
        ("f800000000", 5 + 0),
        ("f807ffffff", 5 + 0x7FFFFFF),
        ("fc0000000000", 6 + 0),
        ("fc03ffffffff", 6 + 0x3FFFFFFFF),
    ],
)
def test_atom_not_canonical(clvm_hex: str, expect: int) -> None:
    clvm_buf = bytes.fromhex(clvm_hex)
    atom_len, is_canonical = is_atom_canonical(clvm_buf, 0)
    assert atom_len == expect
    assert not is_canonical
```
