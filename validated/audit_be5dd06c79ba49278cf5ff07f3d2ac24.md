### Title
Unhandled `IndexError` in mempool canonical-CLVM check on attacker-supplied spend bytes — (File: `chia/full_node/mempool_manager.py`)

### Summary
`MempoolManager.validate_spend_bundle()` runs `is_clvm_canonical()` directly on the raw `puzzle_reveal` and `solution` bytes of every coin spend in a submitted `SpendBundle` before admission to the mempool [1](#0-0) . `is_clvm_canonical()` delegates length-prefix decoding to `is_atom_canonical()`, which reads a variable number of trailing length bytes from the buffer without ever checking that those offsets are within the buffer's bounds [2](#0-1) . This is the same bug class as the reported CGAL CVE: a length-prefixed record parser trusts an attacker-controlled length/prefix header and reads past the end of the buffer instead of validating available remaining bytes first.

### Finding Description
`is_atom_canonical(clvm_buffer, offset)` reads the leading byte to determine `prefix_len` (0–5 extra bytes depending on the high bits), then unconditionally loops `prefix_len` times doing `offset += 1; atom_len |= clvm_buffer[offset]` [3](#0-2) . There is no check that `offset < len(clvm_buffer)` before each read, and the caller `is_clvm_canonical()` also indexes `clvm_buffer[offset]` on every loop iteration without a bounds check [4](#0-3) .

For example, a single trailing byte `0xFC` at the tail of a buffer indicates a 5-extra-byte length prefix (`(b & 0b11111110) == 0b11111100`); if fewer than 5 bytes remain, the loop indexes past the end of `clvm_buffer` and Python raises `IndexError`.

The critical question is whether an attacker can get such a malformed trailing sequence into a buffer that has already been "successfully" processed by the Rust CLVM engine (`validate_clvm_and_signature`) upstream, since `conds` is computed before `is_clvm_canonical()` runs. CLVM's `Program.parse()`/`sexp_from_stream()` is a stream-based parser that reads exactly one top-level S-expression and does not require the whole buffer to be consumed [5](#0-4) . This is precisely why `is_clvm_canonical()` has its own explicit trailing-garbage check (`return offset == len(clvm_buffer)`), separate from CLVM execution validity — i.e., CLVM execution alone does not guarantee the buffer has no trailing bytes, or that trailing bytes form a complete, well-formed atom header. An attacker who appends a truncated/malformed atom-length header (like a bare `0xFC`) after a valid top-level CLVM expression in `puzzle_reveal` or `solution` can get the spend past CLVM execution/condition generation, but then crash Python's `is_clvm_canonical()`/`is_atom_canonical()` re-parse with an uncaught `IndexError`.

`add_spend_bundle()`, which calls `validate_spend_bundle()`, has no `try/except` around this call [6](#0-5) , so the exception is not handled at this layer and propagates to whatever caller invoked `add_spend_bundle()` for the submitted transaction.

### Impact Explanation
A malformed but CLVM-parseable `puzzle_reveal`/`solution` submitted by any unprivileged spend-bundle submitter can trigger an unhandled `IndexError` deep inside mempool admission logic (`validate_spend_bundle` → `is_clvm_canonical` → `is_atom_canonical`), which is not defensively caught at the `add_spend_bundle()` layer. This is a spend-triggered transaction-processing fault directly in the mempool admission path reachable from a single submitted spend bundle, matching the CVE's parser out-of-bounds-read bug class, though bounded by Python's memory safety to a raised exception rather than memory corruption/code execution.

### Likelihood Explanation
The vulnerable code path is unconditionally exercised for every coin spend of every submitted spend bundle during mempool admission — no special network position, privilege, or timing is required, only a spend bundle with a crafted trailing byte sequence in `puzzle_reveal` or `solution` after a valid top-level CLVM expression.

### Recommendation
Add explicit bounds checks in `is_atom_canonical()` before each `clvm_buffer[offset]` access (raise/return non-canonical on truncated headers instead of indexing past the buffer), and add a bounds check for the initial `clvm_buffer[offset]` read in `is_clvm_canonical()`'s main loop. Additionally, wrap the call to `is_clvm_canonical()` in `validate_spend_bundle()` (or `add_spend_bundle()`) so any parsing exception is converted into a normal `Err.INVALID_COIN_SOLUTION` mempool rejection rather than propagating as an unhandled exception.

### Proof of Concept
Construct a `puzzle_reveal` or `solution` byte string consisting of a valid, fully-consumable top-level CLVM atom/pair (so CLVM execution succeeds and conditions are generated), followed by a single trailing byte `0xFC` (or `0xF8`, `0xF0`, `0xE0`, `0xC0` — any multi-byte atom-length-prefix indicator) with fewer than the required number of trailing length bytes remaining. Submit this as a coin spend inside a `SpendBundle`. When the bundle reaches `MempoolManager.add_spend_bundle()` → `validate_spend_bundle()`, the call `is_clvm_canonical(bytes(coin_spend.puzzle_reveal))` (or `.solution`) reaches `is_atom_canonical()` at the trailing malformed byte and raises an uncaught `IndexError` while indexing `clvm_buffer[offset]` past the end of the byte string [3](#0-2) .

Note: I was unable to fully trace every caller above `add_spend_bundle()` (e.g., `full_node.py`/`full_node_api.py` transaction-handling entry points) to confirm whether some outer layer wraps this call in a broad `try/except Exception`, which would downgrade this from a potential service disruption to a logged/handled error. This should be verified before treating the severity as definitively "High."

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

**File:** chia/types/blockchain_format/program.py (L36-38)
```python
    @classmethod
    def parse(cls, f) -> Self:
        return sexp_from_stream(f, cls.to)
```
