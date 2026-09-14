### Title
Unhandled `IndexError` in CLVM canonical-form check on attacker-controlled puzzle/solution bytes causes spend-triggered mempool processing halt - (File: chia/full_node/mempool_manager.py)

### Summary
`is_atom_canonical()` / `is_clvm_canonical()` in `chia/full_node/mempool_manager.py` parse the variable-length atom size prefix of a CLVM-serialized buffer by repeatedly indexing into the raw `bytes` buffer (`clvm_buffer[offset]`) without ever checking that `offset` stays within the buffer bounds before dereferencing it, mirroring the bufPos/elementLength unchecked-length-prefix pattern in CVE-2019-19957's `getNumberOfElements`.

### Finding Description
`is_atom_canonical()` reads the first byte at `offset` to determine `prefix_len` (0-5 additional bytes), then loops `prefix_len` times incrementing `offset` and dereferencing `clvm_buffer[offset]` each time to build `atom_len`: [1](#0-0) 

There is no check anywhere in this function (or in its caller `is_clvm_canonical`) that `offset` (or `offset + prefix_len`) is less than `len(clvm_buffer)` before the prefix bytes are read: [2](#0-1) 

This is called directly on the raw bytes of every coin spend's `puzzle_reveal` and `solution` from any submitted `SpendBundle`, during the mempool admission path `MempoolManager.validate_spend_bundle()`: [3](#0-2) 

A spend bundle whose puzzle reveal or solution ends with a truncated multi-byte atom length-prefix byte (e.g. an `0xFC`/`0xF8`/etc. prefix byte placed as the very last byte of the buffer, with no following length bytes) causes `is_atom_canonical` to index past the end of the `bytes` object, raising an unhandled `IndexError`. Because Python `bytes` indexing raises an exception (rather than a raw memory read as in the C/C++ `libIEC61850` original), this is a controlled crash rather than a memory-disclosure bug, but the trigger condition — attacker-supplied length prefix at the very edge of a length-checked buffer — is structurally the same bug class as the CVE.

### Impact Explanation
`validate_spend_bundle()` does not wrap the `is_clvm_canonical()` calls in any `try/except`, and this code path runs during ordinary mempool admission for every submitted spend bundle (`add_spend_bundle` → `validate_spend_bundle`), which is reachable by any unprivileged peer/wallet submitting a spend bundle to a full node. An unhandled `IndexError` propagating out of `validate_spend_bundle` during mempool processing is a spend-triggered exception in the transaction-processing pipeline, which can disrupt admission of that spend bundle and, depending on how the exception propagates through the mempool manager's async task/lock handling, can interfere with subsequent mempool processing for the node — a transaction-processing halt triggered by a single crafted spend.

### Likelihood Explanation
Constructing the byte sequence to trigger this is straightforward: the puzzle reveal or solution just needs to end exactly on a multi-byte atom-length-prefix marker byte (`0xC0`-`0xFF` range with insufficient trailing bytes) so that the prefix-reading loop in `is_atom_canonical` walks past the end of the buffer. Test coverage in the repo (`chia/_tests/core/mempool/test_mempool_manager.py`) already exercises `is_atom_canonical`/`is_clvm_canonical` with truncated/non-canonical encodings, showing the parsing logic is a known-sensitive area, though the existing tests only cover in-bounds truncated-looking atoms, not the exact end-of-buffer boundary condition. No signature or special privilege is required beyond submitting a spend bundle.

### Recommendation
Add explicit bounds checks in `is_atom_canonical` (and the calling loop in `is_clvm_canonical`) before dereferencing `clvm_buffer[offset]` for each prefix byte, returning "not canonical" (or raising a caught `ValidationError`/`Err.INVALID_COIN_SOLUTION`) instead of allowing an `IndexError` to escape; alternatively wrap the `is_clvm_canonical` calls in `validate_spend_bundle` in a `try/except IndexError` that maps to `Err.INVALID_COIN_SOLUTION`, consistent with how other malformed-spend cases are already rejected in that function.

### Proof of Concept
1. Construct a `CoinSpend` whose `solution` (or `puzzle_reveal`) bytes end with a lone multi-byte atom prefix marker, e.g. a buffer such as `b"\xff\xff\xfc"` (an `0xFC` byte, which signals a 5-byte trailing length field, but with zero trailing bytes present).
2. Submit a `SpendBundle` containing this `CoinSpend` to a full node (e.g. via `mempool_manager.add_spend_bundle`, reachable from the wallet/full-node RPC transaction-submission path).
3. `validate_spend_bundle()` calls `is_clvm_canonical(bytes(coin_spend.solution))` at [3](#0-2) , which calls `is_atom_canonical`, which indexes past the end of the buffer at [4](#0-3) , raising an unhandled `IndexError` instead of the expected `Err.INVALID_COIN_SOLUTION` rejection.

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
