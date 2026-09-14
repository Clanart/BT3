### Title
Unhandled `IndexError` in `is_clvm_canonical`/`is_atom_canonical` causes an unbounded read past the CLVM buffer, allowing a single spend bundle to crash mempool validation - (File: chia/full_node/mempool_manager.py)

### Summary
`is_atom_canonical()` reads a variable-length (0–5 extra byte) size prefix out of an attacker-controlled `bytes` buffer without ever checking that `offset` stays within `len(clvm_buffer)` before indexing it, and `is_clvm_canonical()` calls it (and re-derives `offset`) in the same unchecked way while iterating a spend's raw puzzle-reveal/solution bytes. [1](#0-0) 

### Finding Description
`is_atom_canonical(clvm_buffer, offset)` inspects the high bits of `clvm_buffer[offset]` to determine an atom's length-prefix size (`prefix_len` up to 5 extra bytes), then loops `prefix_len` times incrementing `offset` and dereferencing `clvm_buffer[offset]` with no bounds check against `len(clvm_buffer)`: [2](#0-1) 

`is_clvm_canonical(clvm_buffer)` walks the buffer token by token, calling `is_atom_canonical` whenever it hits a byte `> 0x80`, and advances `offset` by the returned `atom_len` without verifying `offset <= len(clvm_buffer)` before the next loop iteration re-reads `clvm_buffer[offset]`: [3](#0-2) 

This is directly analogous to the reported SQLite `sessionReadRecord` bug class: a length-prefixed record parser trusts an attacker-supplied length/prefix field and advances a cursor/offset into a buffer without validating that the read stays inside the buffer bounds. In C this manifests as a heap out-of-bounds read/overflow; in this Python code the same missing-bounds-check pattern manifests as an unhandled `IndexError` when the crafted CLVM bytes end exactly where a multi-byte length prefix (or an atom body whose declared length exceeds the remaining buffer) is expected.

Both functions are reached directly from `MempoolManager.validate_spend_bundle()`, which is invoked for every incoming `SpendBundle` (from RPC `push_tx`, wallet transactions, or peer-relayed transactions before they are re-validated) via `is_clvm_canonical(bytes(coin_spend.puzzle_reveal))` / `is_clvm_canonical(bytes(coin_spend.solution))`: [4](#0-3) 

Neither `validate_spend_bundle` nor `add_spend_bundle` wraps this call in a `try/except`, so a raised `IndexError` propagates out of the admission pipeline as an unhandled exception rather than being converted to a normal `Err.INVALID_COIN_SOLUTION` rejection.

### Impact Explanation
A single spend bundle with a puzzle reveal or solution crafted to end immediately after a byte indicating a multi-byte atom length prefix (e.g. `0xFC` with fewer than 5 trailing bytes) will raise an unhandled `IndexError` instead of being cleanly rejected. Because this is reached on the mempool admission hot path for every submitted spend bundle (unprivileged wallet/RPC/peer transaction submission), an attacker can repeatedly submit such malformed transactions to trigger unhandled exceptions during the CLVM-canonical-form pre-check, corresponding to the "spend-triggered transaction-processing halt" impact category. Whether this actually crashes the full node process, only fails that one transaction's coroutine, or is caught by a higher-level generic exception handler around the RPC/protocol message handlers could not be confirmed from the code paths inspected.

### Likelihood Explanation
Likelihood is high for reaching the vulnerable code: any unprivileged spend bundle submitter (wallet, offer counterparty, RPC caller, or gossiping peer) can supply arbitrary bytes as `puzzle_reveal`/`solution`, and this check runs unconditionally for every coin spend in `validate_spend_bundle`, with no privileged access required. The exact runtime consequence (crash vs. contained per-transaction failure) depends on exception handling further up the call stack, which was not fully traced.

### Recommendation
Add explicit bounds checks in `is_atom_canonical` (verify `offset + prefix_len < len(clvm_buffer)` before indexing, and verify the declared `atom_len` does not exceed the remaining buffer) and in `is_clvm_canonical` (verify `offset < len(clvm_buffer)` before each `clvm_buffer[offset]` read), returning "not canonical" (`False`) instead of letting Python raise `IndexError`. Alternatively, wrap the canonical-form checks in `validate_spend_bundle` with a `try/except (IndexError, ValueError)` that maps to `Err.INVALID_COIN_SOLUTION`, and add regression tests using truncated multi-byte-prefix inputs (e.g. `"fc00"`, `"f800"`).

### Proof of Concept
Construct a `puzzle_reveal` or `solution` whose bytes are e.g. `bytes.fromhex("fc0000")` (a `0xFC` marker declaring a 5-byte length prefix) truncated to only 2 trailing bytes instead of 5. Submitting a `SpendBundle` containing a `CoinSpend` with this solution to `MempoolManager.add_spend_bundle` invokes `validate_spend_bundle`, which calls `is_clvm_canonical(bytes(coin_spend.solution))` → `is_atom_canonical(...)`, whose `for i in range(prefix_len): ... clvm_buffer[offset]` loop indexes past the end of the 3-byte buffer, raising `IndexError: index out of range` instead of returning a graceful `False`/rejection. [5](#0-4) [4](#0-3)

### Citations

**File:** chia/full_node/mempool_manager.py (L144-184)
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
