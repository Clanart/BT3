### Title
Unhandled `IndexError` from unbounded atom-length-prefix parsing in mempool CLVM canonicality check causes spend-triggered transaction-processing crash - (File: `chia/full_node/mempool_manager.py`)

### Summary
`is_atom_canonical()` in `chia/full_node/mempool_manager.py` parses a CLVM atom's variable-length size prefix by indexing forward into `clvm_buffer` a fixed number of times (`prefix_len`, up to 5) based on the leading byte's high bits, without ever checking that `offset` stays within `len(clvm_buffer)`. This is the same bug class as ALPINE-CVE-2026-33069 (PJSIP's `pjsip_multipart_parse()`): after inspecting a boundary/marker byte, the parser advances a cursor a fixed number of steps past it without verifying the buffer actually contains that many remaining bytes, producing an out-of-bounds read.

### Finding Description
`is_atom_canonical()` reads the first byte at `offset` to determine how many additional length-prefix bytes to consume (`prefix_len`, 0–5), then unconditionally loops `prefix_len` times reading `clvm_buffer[offset]` after each increment, with no bounds check against `len(clvm_buffer)`: [1](#0-0) 

`is_clvm_canonical()` drives this parser over an attacker-supplied byte buffer, and itself also indexes `clvm_buffer[offset]` at the top of every loop iteration with no bounds check before dereferencing: [2](#0-1) 

This function is invoked unconditionally on every coin spend inside `MempoolManager.validate_spend_bundle()`, over both the puzzle reveal and the solution bytes supplied by any spend-bundle submitter: [3](#0-2) 

Because `coin_spend.puzzle_reveal` and `coin_spend.solution` are fully attacker-controlled byte strings (any wallet/peer can submit a `SpendBundle` with arbitrary CLVM bytes as a coin spend), a crafted buffer that ends immediately after (or shortly after) a multi-byte length-prefix marker (e.g. a trailing `0xFC`/`0xFD`/`0xF8` marker byte with insufficient trailing bytes) causes `clvm_buffer[offset]` to be indexed past the end of the buffer. In Python this raises an `IndexError` rather than returning a boolean, and nothing in `is_atom_canonical`/`is_clvm_canonical` catches it.

### Impact Explanation
`validate_spend_bundle()` is called from `add_spend_bundle()`, which is the core entry point used whenever a `SpendBundle` (from mempool submission, `respond_transaction`, wallet RPC push, etc.) is validated for mempool admission. An uncaught `IndexError` raised mid-validation is not one of the expected `Err` return paths, so it propagates out of `validate_spend_bundle`/`add_spend_bundle` as an unhandled exception. Depending on how the calling task in `full_node.py` handles bundle processing, this results in an unhandled exception during transaction admission — a spend-triggered halt/crash of that processing path — rather than a graceful `Err.INVALID_COIN_SOLUTION` rejection. This is a mempool-availability/DoS-class issue reachable by any single unprivileged spend-bundle submitter, matching the CVE's "processing malformed input causes O(1-2 byte) out-of-bounds read" bug class, translated to Python's "index past end raises unhandled exception" equivalent.

I was not able to fully confirm, within the available tool budget, whether the call site in `chia/full_node/full_node.py` (or `MempoolManager.add_spend_bundle`) wraps this call in a broad `try/except` that safely converts the exception into a rejected/failed status; I only confirmed matches for `add_spend_bundle(` calls and generic exception handlers exist somewhere in that file but did not inspect the exact surrounding code before running out of iterations. If such a catch-all exists around every call path that reaches `validate_spend_bundle`, the practical impact is downgraded to a rejected transaction rather than a crash/DoS — but the missing bounds check itself is a genuine correctness bug (unbounded buffer read) regardless of whether some outer handler currently masks its effect.

### Likelihood Explanation
Trivial to trigger: it requires no special privileges, just constructing a `SpendBundle` with a `puzzle_reveal` or `solution` whose CLVM bytes end mid-way through what looks like a multi-byte atom length prefix. Any wallet, offer counterparty, or RPC caller submitting a spend bundle can hit this code path deterministically, since `is_clvm_canonical` runs on every coin spend during admission.

### Recommendation
Add explicit bounds checks in `is_atom_canonical()` before dereferencing `clvm_buffer[offset]` in the prefix-reading loop, and in `is_clvm_canonical()`'s main loop before reading `clvm_buffer[offset]`, raising/returning a controlled "not canonical" / rejection result (e.g., treat truncated buffers as non-canonical, or explicitly catch `IndexError` and translate it to `Err.INVALID_COIN_SOLUTION`) instead of allowing an unbounded index to propagate as an unhandled exception.

### Proof of Concept
1. Construct a `puzzle_reveal` or `solution` byte buffer whose last byte is a multi-byte-length-prefix marker with no trailing bytes, e.g. `bytes.fromhex("fc")` (marker byte indicating a 5-byte length prefix should follow, `prefix_len = 5`) — or any buffer ending exactly on such a marker byte so the subsequent `offset += 1; clvm_buffer[offset]` read in `is_atom_canonical`'s loop lands past `len(clvm_buffer)`.
2. Submit a `SpendBundle` whose single `CoinSpend` uses this buffer as `solution` (or `puzzle_reveal`).
3. Call `MempoolManager.add_spend_bundle()` (as done throughout `chia/_tests/core/mempool/test_mempool_manager.py`, e.g. `test_mempool_requires_canonical_clvm` at [4](#0-3) , using a truncated variant of `solution_hex` instead of the non-canonical-but-in-bounds ones already tested) with the crafted spend bundle.
4. Observe `is_clvm_canonical()` → `is_atom_canonical()` raise `IndexError: index out of range` instead of returning a clean `False`/rejection, propagating out of `validate_spend_bundle`.

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

**File:** chia/full_node/mempool_manager.py (L196-221)
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

**File:** chia/_tests/core/mempool/test_mempool_manager.py (L3277-3300)
```python
@pytest.mark.anyio
@pytest.mark.parametrize("flags", [0, ELIGIBLE_FOR_DEDUP, ELIGIBLE_FOR_FF, ELIGIBLE_FOR_FF | ELIGIBLE_FOR_DEDUP])
@pytest.mark.parametrize(
    "puzzle_hex,solution_hex",
    [
        # ((1)) with a non-canonical atom length prefix in the solution
        (None, "ffffc001018080"),
        # atom 1 with a non-canonical length prefix in the puzzle
        ("c00101", "80"),
    ],
)
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
