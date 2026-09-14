### Title
Missing bounds checks in `is_atom_canonical`/`is_clvm_canonical` allow an unbounded-index crash on attacker-controlled spend data - (File: `chia/full_node/mempool_manager.py`)

### Summary
`chia/full_node/mempool_manager.py` contains a hand-rolled CLVM canonical-encoding checker (`is_atom_canonical` / `is_clvm_canonical`) that is run on every coin spend's `puzzle_reveal` and `solution` bytes during mempool admission of an untrusted, user-submitted spend bundle. Like the NGINX `ngx_http_mp4_module` bug (a parser that trusts length fields inside an attacker-supplied file without validating them against the buffer bounds), this Python parser walks a byte buffer using length-prefix fields taken directly from attacker data, without checking those offsets against `len(clvm_buffer)`. A crafted spend bundle can drive the parser to index past the end of the buffer, raising an unhandled `IndexError` inside mempool spend-bundle validation.

### Finding Description
`is_atom_canonical()` decodes CLVM atom length prefixes and returns `1 + prefix_len + atom_len` as the number of bytes "consumed," but it only walks and bounds-implicitly-checks the `prefix_len` prefix bytes; it never validates that the `atom_len` data bytes it claims to skip actually exist in the buffer: [1](#0-0) 

`is_clvm_canonical()` then advances its own cursor by that unverified `atom_len` and, on the next loop iteration, indexes `clvm_buffer[offset]` again with no bounds check: [2](#0-1) 

The function's own comment ("if there's garbage at the end, it's not canonical") shows that reaching this code path with a valid-CLVM-plus-trailing-bytes buffer is an expected, reachable case — i.e., the function is specifically designed to be called on buffers whose top-level CLVM object may not span the whole slice. Any trailing byte sequence after the real top-level expression that looks like a multi-byte atom-length prefix (e.g. a lone `0xC0`/`0xE0`/... marker byte, or a lone `0xFF` pair marker) with no follow-up bytes will cause `clvm_buffer[offset]` to be read past the end of the buffer, raising `IndexError`.

This function is invoked unconditionally for every coin spend in `validate_spend_bundle()`, which runs during ordinary mempool admission of any submitted spend bundle: [3](#0-2) 

There is no `try`/`except` around this call in `validate_spend_bundle`, so an `IndexError` propagates out of the coroutine that processes the incoming spend bundle.

### Impact Explanation
This is the transaction/mempool-admission analog of the NGINX MP4 over-read: a length field taken from attacker-controlled data is used to advance a cursor without validating it stays within the buffer, and the failure mode is a crash/exception in the code path that processes that untrusted input (worker termination in NGINX; an unhandled `IndexError` in the coroutine handling `add_spend_bundle` here). Because `validate_spend_bundle` is reached for every spend bundle submitted via RPC (`push_tx`) or received from peers, a single crafted, otherwise-valid spend bundle can throw an unhandled exception during mempool processing, which is a spend-triggered disruption of transaction processing as required by the validation rules (denial-of-service class, not a memory-safety issue in the C sense since Python raises a catchable exception, but reachable and unauthenticated).

### Likelihood Explanation
Reachable by any unprivileged spend-bundle submitter: no special permissions are needed, only a puzzle_reveal/solution byte string that (a) is accepted by the CLVM runner well enough to produce spend conditions, and (b) has trailing bytes after the logically consumed top-level expression that form a truncated multi-byte atom-length or pair prefix at the very end of the buffer. Given the docstring explicitly anticipates "garbage at the end" as a real input shape this function must reject, the trailing-garbage-after-valid-CLVM scenario is treated as expected/reachable by the code's own design.

Note on unverified assumption: I could not confirm from this Python-only index whether the underlying `chia_rs` (Rust) CLVM deserializer used to execute the puzzle/solution and compute `SpendBundleConditions` strictly rejects any trailing bytes at the top level, or tolerates them (only consuming the first well-formed expression). That Rust-side behavior is the load-bearing precondition for whether `validate_spend_bundle` can actually observe a "well-formed-CLVM-plus-truncated-trailing-marker" buffer in practice. This detail lives in the `chia_rs` crate, which is not present/indexed in this repository, so it should be verified directly against `chia_rs`'s program deserializer before treating this as confirmed exploitable.

### Recommendation
Add explicit bounds checks in `is_atom_canonical` and `is_clvm_canonical` before every buffer index/slice: verify `offset < len(clvm_buffer)` prior to reading `clvm_buffer[offset]`, and verify `offset + atom_len <= len(clvm_buffer)` before treating `atom_len` bytes as consumed. On any out-of-range condition, return `False` (non-canonical / reject) rather than raising an uncaught `IndexError`, and add a regression test using a spend whose puzzle/solution end with a truncated multi-byte atom prefix or a lone `0xFF` byte.

### Proof of Concept
1. Build a `CoinSpend` whose `solution` (or `puzzle_reveal`) bytes are: `<valid complete CLVM-encoded solution bytes> + b"\xff"` (a trailing lone pair marker with no following elements), or ending in a lone multi-byte atom marker such as `b"\xc0"` with no length byte following.
2. Confirm this buffer is still accepted by the CLVM runtime such that `get_conditions_from_spendbundle`/`run_chia_program` succeeds far enough to reach `validate_spend_bundle` (this step needs to be verified against `chia_rs`'s actual deserializer tolerance for trailing bytes, per the caveat above).
3. Submit the resulting `SpendBundle` through `MempoolManager.add_spend_bundle()` (e.g. via RPC `push_tx`).
4. Observe `is_clvm_canonical(bytes(coin_spend.solution))` being called at: [3](#0-2) 
and the internal loop indexing `clvm_buffer[offset]` past `len(clvm_buffer)`, raising `IndexError` instead of returning `False`, as shown in: [4](#0-3)

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

**File:** chia/full_node/mempool_manager.py (L196-222)
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

```

**File:** chia/full_node/mempool_manager.py (L723-726)
```python
            if not is_clvm_canonical(bytes(coin_spend.puzzle_reveal)) or not is_clvm_canonical(
                bytes(coin_spend.solution)
            ):
                return Err.INVALID_COIN_SOLUTION, None, []
```
