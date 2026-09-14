### Title
Out-of-bounds read / unhandled exception in `is_clvm_canonical`/`is_atom_canonical` via crafted `puzzle_reveal`/`solution` bytes - (File: `chia/full_node/mempool_manager.py`)

### Summary
`is_clvm_canonical()` and its helper `is_atom_canonical()` parse raw, attacker-controlled CLVM byte buffers (`coin_spend.puzzle_reveal` / `coin_spend.solution`) by indexing directly into the buffer with no bounds checking, similarly to how Apache Thrift's `TJSONProtocol`/`TSimpleJSONProtocol` (CVE-2019-0210) read attacker-supplied bytes without validating them, causing a panic on malformed input.

### Finding Description
`is_clvm_canonical` walks a byte buffer using an `offset` counter and reads `clvm_buffer[offset]` in a `while True` loop, only breaking when `tokens_left == 0` [1](#0-0) . For non-pair, non-back-reference, non-small-atom bytes, it delegates to `is_atom_canonical(clvm_buffer, offset)`, which reads a length prefix and then indexes further into the buffer (`clvm_buffer[offset]`) for up to 5 additional bytes based on an attacker-controlled prefix length, without ever checking that `offset` stays within `len(clvm_buffer)` [2](#0-1) . A truncated buffer whose length-prefix byte claims more trailing bytes than actually exist causes an `IndexError` inside `is_atom_canonical`/`is_clvm_canonical` (an unhandled exception on attacker input, analogous to the OOB-read/panic behavior in the Thrift advisory).

This function is called from `validate_spend_bundle()`, which is reached from `add_spend_bundle()` during mempool admission of every submitted `SpendBundle`. Each `coin_spend.puzzle_reveal` and `coin_spend.solution` from the (unprivileged) submitter's spend bundle is passed unchecked into `is_clvm_canonical(bytes(coin_spend.puzzle_reveal))` / `is_clvm_canonical(bytes(coin_spend.solution))` [3](#0-2) . Because `pre_validate_spendbundle()` (which runs the Rust CLVM/signature validation) is invoked before `validate_spend_bundle()` in normal flow, most malformed CLVM there is caught by `validate_clvm_and_signature` earlier; however, `is_clvm_canonical` is a separate, independent Python-level parser that repeats byte-level parsing on the raw puzzle/solution bytes and is not guaranteed to reject every truncated/malformed length-prefix byte sequence that the Rust CLVM parser tolerates or that reaches this stage with a length prefix pointing past the buffer end.

### Impact Explanation
An unhandled `IndexError` raised out of `is_clvm_canonical`/`is_atom_canonical` during `validate_spend_bundle()` would propagate up through `add_spend_bundle()` in the mempool-admission code path used for every incoming spend bundle (RPC, wallet submission, and P2P transaction relay all funnel through this method). If not caught by an enclosing `try/except`, this can crash or halt the coroutine handling the spend, effectively creating a transaction-processing-halt condition triggered by a single crafted spend bundle — a legitimate, spend-triggered denial-of-service analog reachable by an unprivileged submitter, matching the "spend-triggered transaction-processing halt" acceptance criterion.

### Likelihood Explanation
Likelihood is moderate to high in terms of reachability: `is_clvm_canonical` is called unconditionally on every removal's `puzzle_reveal` and `solution` for spends flagged for dedup or fast-forward paths in `validate_spend_bundle()`, which is on the hot path for mempool admission of externally submitted spend bundles. The exact bit pattern needed to reach a byte index past the buffer's end (an atom whose declared length-prefix byte count exceeds the remaining buffer, while still passing whatever validation happens earlier in `pre_validate_spendbundle`) requires precise crafting, and I was not able to fully verify from the available code snippets whether all such malformed shapes are already rejected earlier by `validate_clvm_and_signature`'s Rust-side canonical/serialization checks before reaching this Python function. This uncertainty affects the practical exploitability and should be validated with a concrete truncated buffer against a running mempool.

### Recommendation
Add explicit bounds checks in `is_atom_canonical`/`is_clvm_canonical` before every buffer index (e.g., verify `offset < len(clvm_buffer)` and that the computed atom length does not exceed the remaining buffer length), raising a controlled `ValueError`/returning `False` instead of allowing an `IndexError` to propagate. Ensure `validate_spend_bundle()` (or its caller) catches parsing exceptions from `is_clvm_canonical` and converts them into a proper mempool-rejection `Err` rather than allowing an unhandled exception to escape the mempool-admission path.

### Proof of Concept
Construct a `CoinSpend` whose `puzzle_reveal` or `solution` bytes end with a truncated multi-byte atom length prefix, e.g. a buffer ending in `0xF8` (5-byte length prefix, `0xF8|top_bits`) followed by fewer than 4 trailing bytes, such as `bytes.fromhex("f800")`. When passed through `is_clvm_canonical()`, `is_atom_canonical()` will attempt to read `clvm_buffer[offset]` for indices beyond the 2-byte buffer, raising `IndexError` [4](#0-3) . Submitting a `SpendBundle` containing a `coin_spend` with such a `solution` (or `puzzle_reveal`) through the wallet/RPC transaction-submission path and reaching `validate_spend_bundle()` would trigger this exception during mempool admission. Full confirmation requires exercising this against `pre_validate_spendbundle()` + `add_spend_bundle()` together to confirm the malformed buffer is not already rejected earlier by the Rust CLVM validator.

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

**File:** chia/full_node/mempool_manager.py (L196-224)
```python
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
```

**File:** chia/full_node/mempool_manager.py (L723-726)
```python
            if not is_clvm_canonical(bytes(coin_spend.puzzle_reveal)) or not is_clvm_canonical(
                bytes(coin_spend.solution)
            ):
                return Err.INVALID_COIN_SOLUTION, None, []
```
