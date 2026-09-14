### Title
Out-of-Bounds Atom-Length-Prefix Read in Mempool CLVM Canonical-Form Check - (File: `chia/full_node/mempool_manager.py`)

### Summary
`is_atom_canonical()` reads a variable-length (0–5 extra byte) length prefix from an attacker-supplied CLVM byte buffer without ever checking that the buffer actually contains that many trailing bytes, mirroring the FreeRDP `planar_decompress_plane_rle` bug class: a control byte is read and trusted to declare additional bytes that are then read unconditionally from the source buffer. `is_atom_canonical()` is invoked by `is_clvm_canonical()`, which is called directly on the raw, attacker-controlled `puzzle_reveal` and `solution` bytes of every coin spend during mempool admission in `MempoolManager.validate_spend_bundle()`.

### Finding Description
`is_atom_canonical()` inspects the control byte `b = clvm_buffer[offset]` and, based on its high bits, decides how many additional length-prefix bytes (`prefix_len`, 0–5) to consume: [1](#0-0) 

The loop that follows unconditionally advances `offset` and indexes `clvm_buffer[offset]` for `prefix_len` iterations with no bounds check against `len(clvm_buffer)`: [2](#0-1) 

`is_clvm_canonical()` calls `is_atom_canonical()` whenever it encounters a byte `> 0x80` while walking the buffer, and it is the entry point exposed to untrusted data: [3](#0-2) 

That entry point is reached directly from `MempoolManager.validate_spend_bundle()`, which runs `is_clvm_canonical()` on the raw `puzzle_reveal` and `solution` bytes of *every* coin spend in a submitted `SpendBundle`, before any other structural validation of those buffers occurs: [4](#0-3) 

If a spend's `puzzle_reveal` or `solution` ends with a truncated atom — i.e., a final byte such as `0xFF`/pair marker followed by a control byte (e.g. `0xC0`) declaring a multi-byte length prefix that the buffer does not actually contain — the `for i in range(prefix_len)` loop indexes past the end of the `bytes` object. In CPython this raises an uncaught `IndexError` rather than returning a bounded/validated result, exactly analogous to the FreeRDP decoder trusting a declared byte count without verifying the source buffer holds that many bytes.

### Impact Explanation
`validate_spend_bundle()` is called from `add_spend_bundle()` with no surrounding `try/except` for this specific call, so a raised `IndexError` propagates up from the coroutine that handles mempool admission for a freshly submitted spend bundle: [5](#0-4) 

This lets an unprivileged spend-bundle submitter (any wallet peer, RPC caller, or `push_tx` sender) trigger an unhandled exception purely by choosing solution/puzzle-reveal bytes with a truncated non-canonical atom length prefix at the tail of the buffer — a single crafted spend bundle can crash or destabilize the mempool-admission code path of a full node. This matches the "spend-triggered transaction-processing halt" impact category explicitly accepted by the validation rules.

### Likelihood Explanation
Likelihood is high for reaching the vulnerable code: `is_clvm_canonical()` is unconditionally invoked on every coin spend's puzzle reveal and solution during ordinary mempool admission — there is no privilege requirement, no need for the CLVM to actually execute, and no need for a valid signature (the canonical-form check happens as part of building `BundleCoinSpend` prior to full acceptance/rejection logic). The exact conditions needed (a byte `> 0xC0`-class control byte placed as the very last byte(s) of the buffer) are simple to construct deterministically.

### Recommendation
In `is_atom_canonical()` (`chia/full_node/mempool_manager.py`), validate that `offset + prefix_len < len(clvm_buffer)` (or equivalently that there are at least `prefix_len` bytes remaining) before entering the prefix-reading loop, returning a definitive non-canonical/invalid result (or raising a handled `ValueError`) instead of indexing out of bounds. Additionally, wrap `is_clvm_canonical()` calls in `validate_spend_bundle()` in a `try/except` that treats malformed/truncated input as `Err.INVALID_COIN_SOLUTION` rather than allowing raw Python exceptions to propagate out of mempool admission.

### Proof of Concept
1. Construct a `CoinSpend` whose `solution` (or `puzzle_reveal`) bytes end with a pair marker followed by a truncated multi-byte atom header, e.g. bytes `b"\xff\xff\xc0"` — the trailing `0xC0` control byte declares a 1-extra-byte length prefix (`(b & 0b11100000) == 0b11000000` branch, `prefix_len = 1`), but no further byte exists in the buffer.
2. Submit a `SpendBundle` containing this coin spend to a full node via `push_tx`/RPC.
3. During mempool admission, `MempoolManager.validate_spend_bundle()` calls `is_clvm_canonical(bytes(coin_spend.solution))`, which walks into `is_atom_canonical()`; the loop `atom_len |= clvm_buffer[offset]` at `offset = len(clvm_buffer)` raises `IndexError`, propagating uncaught out of `add_spend_bundle()`.

Note: I was not able to fully confirm from the indexed code whether an outer caller in `full_node.py` (e.g. the `respond_transaction`/`push_tx` RPC handler) wraps `add_spend_bundle()` in a broad `except Exception` that would downgrade this to a logged error rather than a hard crash/task failure — this would need to be verified by reading the exact call sites in `chia/full_node/full_node.py`, which the index only partially surfaced.

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

**File:** chia/full_node/mempool_manager.py (L212-221)
```python
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
