### Title
Unhandled index-out-of-range read/crash in `is_clvm_canonical`/`is_atom_canonical` when validating attacker-supplied `puzzle_reveal`/`solution` bytes - (File: `chia/full_node/mempool_manager.py`)

### Summary
`is_clvm_canonical()` and `is_atom_canonical()` hand-parse a raw CLVM byte buffer to decide whether it uses the shortest-form atom-length encoding, and this parser is invoked directly on attacker-controlled `coin_spend.puzzle_reveal`/`coin_spend.solution` bytes during mempool admission of a submitted spend bundle.

### Finding Description
`validate_spend_bundle()` calls, for every coin spend in a submitted `SpendBundle`: [1](#0-0) 
which runs `is_clvm_canonical(bytes(coin_spend.puzzle_reveal))` / `is_clvm_canonical(bytes(coin_spend.solution))`.

`is_clvm_canonical` walks the buffer byte-by-byte, indexing `clvm_buffer[offset]` and, for multi-byte atom length prefixes, calling `is_atom_canonical(clvm_buffer, offset)`: [2](#0-1) [3](#0-2) 

Neither function bounds-checks `offset` against `len(clvm_buffer)` before dereferencing it. `is_atom_canonical` reads `prefix_len` extra bytes in a loop (`offset += 1; atom_len |= clvm_buffer[offset]`, lines 178-181) with no length check, and after computing an atom's total length, `is_clvm_canonical` advances `offset += atom_len` (line 221) without verifying that this stays within the buffer before the next loop iteration re-indexes `clvm_buffer[offset]` (line 199). This is structurally the same defect class as CVE-2017-9761's `find_eoq`: a length-prefixed token scanner that trusts an attacker-supplied length/prefix field to walk forward without validating remaining-buffer size, producing an out-of-bounds read once the crafted buffer's trailing bytes look like the start of a longer atom header or an unterminated pair chain than actually exists.

Whether this is reachable in practice depends on whether the CLVM object that was independently validated by the Rust `validate_clvm_and_signature()` call (used to compute `SpendBundleConditions`) requires the *entire* `puzzle_reveal`/`solution` buffer to be consumed as a single well-formed program, or whether it merely parses a prefix and tolerates trailing bytes. Chia's CLVM byte format does not inherently require full-buffer consumption for successful execution (unlike `Streamable.from_bytes`, which explicitly rejects trailing bytes — see `chia/util/streamable.py:765-771`). If trailing bytes after a fully valid CLVM object are tolerated by the Rust deserializer used for execution, an attacker can append a truncated multi-byte atom-length prefix (e.g. a single `0xC0` byte with no following continuation byte) after an otherwise valid puzzle/solution, causing `is_atom_canonical`/`is_clvm_canonical` to index past the end of the buffer and raise an unhandled `IndexError` inside `validate_spend_bundle()`.

### Impact Explanation
An `IndexError` raised inside `validate_spend_bundle()` is not one of the explicitly handled `Err` paths in `add_spend_bundle()`/`validate_spend_bundle()`; it would propagate up out of the mempool-admission call chain as an unexpected exception rather than a clean `MempoolInclusionStatus.FAILED` rejection. Depending on how the calling RPC/gossip handler treats unexpected exceptions from spend-bundle processing, this can crash or abort the processing task for that request, denying transaction processing for the submitted bundle and potentially destabilizing the mempool-admission code path — a spend-triggered transaction-processing halt, matching the "denial of service" class of the reference CVE.

### Likelihood Explanation
Medium. The path is reachable by any unprivileged spend-bundle submitter with only a syntactically-valid coin spend (no special coin ownership needed, since this check happens before/alongside CLVM execution and before ownership signature checks are cached). The primary uncertainty is whether the upstream Rust CLVM parser used by `validate_clvm_and_signature()` strictly rejects trailing bytes after the top-level program in `puzzle_reveal`/`solution` before this Python-side canonical check ever sees a buffer with dangling truncated header bytes; if it does, the crafted input would be rejected earlier and this analog would not be reachable. This could not be confirmed with certainty from the available Python-level code and tests alone (the enforcement lives in the `chia_rs` Rust extension, which is outside the indexed Python source).

### Recommendation
Add explicit bounds checks in `is_atom_canonical()` before every `clvm_buffer[offset]` access (verify `offset < len(clvm_buffer)` for the initial byte and for each of the `prefix_len` continuation bytes) and in `is_clvm_canonical()` before re-entering the loop after advancing `offset` past an atom (verify `offset <= len(clvm_buffer)`), returning `False`/raising a controlled `ValueError` instead of relying on Python's `IndexError`. Additionally, ensure `validate_spend_bundle()`/`add_spend_bundle()` catch and translate any unexpected exception from `is_clvm_canonical` into `Err.INVALID_COIN_SOLUTION` (or similar) rather than letting it propagate uncaught.

### Proof of Concept
Conceptual (unverified against the Rust CLVM boundary, see Likelihood):
1. Craft a `puzzle_reveal` or `solution` byte buffer consisting of a fully valid, executable CLVM program followed by a single trailing byte `0xC0` (a byte that, per the atom-length-prefix encoding used in `is_atom_canonical` lines 151-155, signals a 2-byte length prefix requiring one more byte that is not present).
2. Submit a `SpendBundle` with a `CoinSpend` using this buffer as `puzzle_reveal` (or `solution`) via the normal `push_tx`/mempool-submission RPC.
3. If the Rust CLVM executor used in `validate_clvm_and_signature()` successfully parses/executes the leading valid program and ignores the trailing byte, `validate_spend_bundle()` proceeds to call `is_clvm_canonical(bytes(coin_spend.puzzle_reveal))`, which walks off the end of the buffer at the trailing `0xC0` byte and raises an unhandled `IndexError`, propagating out of spend-bundle validation for that submission.

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
