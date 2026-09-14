I found the strongest analog: `is_clvm_canonical`/`is_atom_canonical` in `chia/full_node/mempool_manager.py`, which parses attacker-controlled `puzzle_reveal`/`solution` bytes directly from every submitted spend bundle, indexing into the buffer without validating that the length-prefix offset stays within bounds.

### Title
Unbounded index read in CLVM canonical-encoding check can crash the mempool on a crafted spend bundle - ([File: chia/full_node/mempool_manager.py])

### Summary
`validate_spend_bundle()` calls `is_clvm_canonical(bytes(coin_spend.puzzle_reveal))` and `is_clvm_canonical(bytes(coin_spend.solution))` for every coin spend in every submitted `SpendBundle`, before the coin spend is otherwise fully validated. [1](#0-0)  This mirrors the reported bug class: a length-prefixed binary format is walked with attacker-controlled length fields and no bound check against the buffer's actual size, exactly like the DNS packet name/record parsing overlay in the external report.

### Finding Description
`is_clvm_canonical()` walks `clvm_buffer` byte by byte using a `tokens_left` state machine and an `offset` cursor: [2](#0-1)  For any atom byte greater than `0x80`, it delegates to `is_atom_canonical(clvm_buffer, offset)`, which reads a variable-length size prefix (1–6 bytes) directly out of the buffer: [3](#0-2) 

Neither function ever checks `offset` (or `offset + prefix_len`) against `len(clvm_buffer)` before indexing. If an attacker crafts a `puzzle_reveal` or `solution` blob whose final byte(s) claim a multi-byte length prefix (e.g. `0xFC`..`0xFF` class prefixes needing up to 6 header bytes) but the buffer ends before those prefix bytes are actually present, the loop in `is_atom_canonical` will index `clvm_buffer[offset]` past the end of the `bytes` object. In CPython, indexing a `bytes` object out of range raises `IndexError` rather than performing a true memory over-read, so the practical effect here is an unhandled exception rather than a C-level OOB memory read — but the reachable trigger (crafted, attacker-supplied byte buffer with an unvalidated internal length field) is the same bug class as the CVE.

### Impact Explanation
`validate_spend_bundle()` is invoked from `add_spend_bundle()`, which is called for every incoming transaction that reaches the full node's mempool admission path. [4](#0-3)  Because `is_clvm_canonical` is called unconditionally on the raw `puzzle_reveal`/`solution` bytes before any other structural validation of those specific bytes' shape, a malformed but otherwise "canonical-looking" truncated buffer can raise an `IndexError`. Whether this manifests as a per-bundle rejection or a full crash depends on the exception handling in the mempool add path; if unhandled at any layer, it constitutes a spend-triggered transaction-processing halt (denial of service) reachable by any unprivileged spend-bundle submitter, matching the rules' "spend-triggered transaction-processing halt" acceptance criterion.

### Likelihood Explanation
Likelihood is high in terms of reachability: constructing a `puzzle_reveal`/`solution` byte buffer with a trailing partial atom-length-prefix byte (e.g., a lone `0xC0`+ prefix byte at the very end of the buffer) is trivial for anyone able to submit a `SpendBundle` to a full node's mempool (via RPC or the wallet protocol), requiring no special privileges, valid signatures being checked elsewhere in the flow notwithstanding. However, whether `is_clvm_canonical` is actually reached for arbitrary coin spends, versus only DEDUP-eligible ones, depends on the exact call ordering and whether `pre_validate_spendbundle`'s prior CLVM execution already rejects malformed encodings before `is_clvm_canonical` runs — this needs confirmation against the current code path order.

### Recommendation
Add explicit bounds checks in `is_atom_canonical()` before each buffer index (verify `offset < len(clvm_buffer)` before reading `clvm_buffer[offset]`, and verify the full prefix plus atom length stays within `len(clvm_buffer)`), returning "not canonical"/raising a handled `ValidationError` instead of allowing an `IndexError` to propagate. Additionally, ensure any exception from `is_clvm_canonical` is caught and converted to `Err.INVALID_COIN_SOLUTION` (or similar) in `validate_spend_bundle()` so a malformed buffer cannot escape as an unhandled exception into the mempool add path.

### Proof of Concept
1. Construct a `CoinSpend` whose `solution` (or `puzzle_reveal`) serialized bytes end with a single byte such as `0xC0` (indicating "5 bits + 8 bits length prefix", i.e., `prefix_len = 1`) as the very last byte of the buffer, with no following byte for the length continuation.
2. Wrap it in a `SpendBundle` and submit it through the normal mempool admission flow (`add_spend_bundle` → `validate_spend_bundle`).
3. When `is_clvm_canonical(bytes(coin_spend.solution))` is called at `chia/full_node/mempool_manager.py:723`, execution reaches `is_atom_canonical()`, which attempts `clvm_buffer[offset]` for `offset == len(clvm_buffer)`, raising `IndexError`.
4. Observe whether this exception propagates unhandled through `validate_spend_bundle()`/`add_spend_bundle()`, confirming a spend-triggered exception in the mempool admission path.

### Citations

**File:** chia/full_node/mempool_manager.py (L144-182)
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
