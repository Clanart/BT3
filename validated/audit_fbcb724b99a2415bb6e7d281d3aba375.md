Based on my investigation, I found that `is_clvm_canonical()` is invoked directly on attacker-supplied puzzle reveal and solution bytes from every coin spend in a submitted spend bundle, before any length/bounds validation is done relative to the atom length-prefix decoding performed by `is_atom_canonical()`.

### Title
Unbounded index read in mempool CLVM canonical-form check crashes the full node on a crafted spend bundle - (File: chia/full_node/mempool_manager.py)

### Summary
`is_atom_canonical()` decodes a CLVM atom's variable-length size prefix (1–6 bytes) by repeatedly indexing `clvm_buffer[offset]` for `prefix_len` iterations without ever checking that `offset` stays within `len(clvm_buffer)`. This mirrors the ClamAV ALZ advisory's root cause: a length-prefixed content parser that trusts an attacker-controlled length field and walks past the buffer end during decoding.

### Finding Description
`is_atom_canonical(clvm_buffer, offset)` [1](#0-0)  reads the byte at `offset`, determines a `prefix_len` (0–5) from the top bits, then loops `prefix_len` times doing `offset += 1; atom_len |= clvm_buffer[offset]`, with no bound check against `len(clvm_buffer)`. It is called from `is_clvm_canonical()` [2](#0-1)  which walks the buffer atom by atom but likewise never validates that `offset` plus the atom's declared prefix/length stays inside the buffer before calling `is_atom_canonical` or before advancing `offset += atom_len`.

`is_clvm_canonical()` is called directly on unauthorized, attacker-supplied bytes in `validate_spend_bundle()`: `is_clvm_canonical(bytes(coin_spend.puzzle_reveal))` and `is_clvm_canonical(bytes(coin_spend.solution))` [3](#0-2) . This runs on every coin spend of a submitted spend bundle during mempool admission (`validate_spend_bundle`), which is reachable by any unauthenticated wallet/RPC caller submitting a transaction to a full node.

Because `puzzle_reveal`/`solution` are raw bytes controlled by the submitter and are only checked for basic CLVM validity by the Rust CLVM engine during `validate_clvm_and_signature` earlier in the pipeline (which accepts non-canonical/oversized length-prefix encodings — that is the entire point of this canonical check, to reject them after CLVM execution has already succeeded), a crafted buffer can present a truncated/malformed trailing atom header (e.g., a `0xFC` byte introducing a 5-byte length field with fewer than 5 bytes actually remaining in the buffer). This causes `clvm_buffer[offset]` to raise an unhandled `IndexError` inside `is_atom_canonical`/`is_clvm_canonical`.

### Impact Explanation
Unlike the ClamAV C/C++ case, Python bounds violations raise `IndexError` rather than corrupting memory, but the effect on availability is analogous: `is_clvm_canonical` is called synchronously inside `validate_spend_bundle()` [3](#0-2) , which is awaited from `add_spend_bundle()` [4](#0-3) . If the raised `IndexError` is not caught by an enclosing handler in the mempool/full-node-API call chain, it propagates up and can crash or halt transaction processing for that spend bundle submission path, producing a remotely triggerable DoS in the full node's transaction-processing pipeline — reachable by any single unauthenticated spend-bundle submitter (wallet, RPC caller, or peer relaying a transaction), matching the report's "unauthenticated remote attacker causes DoS via crafted content".

### Likelihood Explanation
High. No signature, prior mempool state, or special privilege is required — only a single, syntactically-valid-to-CLVM but crafted `puzzle_reveal` or `solution` with a truncated non-canonical atom length header at the end of the buffer. The existing test suite already exercises non-canonical inputs (`test_mempool_requires_canonical_clvm`, `test_clvm_not_canonical`) [5](#0-4)  but does not cover the truncated-buffer boundary case, so this path appears untested against buffer-overrun offsets.

### Recommendation
Add explicit bounds checks in `is_atom_canonical()` before each `clvm_buffer[offset]` read (raise/return non-canonical, or catch `IndexError`, when `offset >= len(clvm_buffer)`), and in `is_clvm_canonical()` validate that `offset < len(clvm_buffer)` before each read and after computing `atom_len`/`prefix_len` before advancing. Wrap or pre-validate lengths so malformed trailing atoms are rejected gracefully (`Err.INVALID_COIN_SOLUTION`) instead of raising an unhandled exception.

### Proof of Concept
Construct a `coin_spend.solution` (or `puzzle_reveal`) whose CLVM bytes are valid enough to pass Rust CLVM execution (e.g., wrapped so it still evaluates, such as inside a quoted/ignored branch) but end in a truncated multi-byte atom header, e.g. bytes ending in `\xfc` (declaring a 6-byte-total length prefix) with only 1–2 bytes actually following it in the buffer. Submitting a `SpendBundle` containing this as a coin spend's `solution` to `add_spend_bundle`/`validate_spend_bundle` triggers `is_clvm_canonical(bytes(coin_spend.solution))` [6](#0-5) , which walks into `is_atom_canonical` and indexes past the end of the buffer, raising an unhandled `IndexError` during mempool admission for that submission.

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

**File:** chia/_tests/core/mempool/test_mempool_manager.py (L3288-3300)
```python
async def test_mempool_requires_canonical_clvm(flags: int, puzzle_hex: str | None, solution_hex: str) -> None:
    coin_spend = make_spend(
        TEST_COIN,
        SerializedProgram.fromhex(puzzle_hex) if puzzle_hex is not None else IDENTITY_PUZZLE,
        SerializedProgram.fromhex(solution_hex),
    )
    coins = TestCoins([TEST_COIN], lineage={})
    async with setup_mempool(coins) as mempool_manager:
        sb = SpendBundle([coin_spend], G2Element())
        sb_conds = make_test_conds(spend_ids=[(TEST_COIN, flags)])
        bundle_add_info = await mempool_manager.add_spend_bundle(sb, sb_conds, sb.name(), uint32(1))
        assert bundle_add_info.status == MempoolInclusionStatus.FAILED
        assert bundle_add_info.error == Err.INVALID_COIN_SOLUTION
```
