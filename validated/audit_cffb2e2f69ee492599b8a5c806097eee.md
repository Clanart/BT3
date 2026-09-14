## Title
Missing bounds check in CLVM canonical-atom length-prefix parser causes unhandled `IndexError` on submitted spend bundles - (File: `chia/full_node/mempool_manager.py`)

### Summary
`is_atom_canonical()` and its caller `is_clvm_canonical()` in `chia/full_node/mempool_manager.py` parse a CLVM atom's variable-length size prefix (1–6 bytes) by walking an attacker-supplied `bytes` buffer and indexing past the current offset without ever checking that the buffer actually contains that many remaining bytes. This is the same bug class as CVE-2026-66034: a length field taken from untrusted input is used to advance a parse cursor with no bound check against the buffer's real size, causing an out-of-bounds access. In this codebase Python raises an `IndexError` rather than reading adjacent heap memory, but the missing-bounds-check root cause and the reachable, unauthenticated trigger are identical.

### Finding Description
`is_atom_canonical()` reads a leading byte to determine how many additional length-prefix bytes follow (`prefix_len` up to 5), then loops: [1](#0-0) 
No check exists that `offset + prefix_len < len(clvm_buffer)` before this loop reads `clvm_buffer[offset]` on each iteration, and no check exists that the resulting `atom_len` bytes actually exist in the buffer before the function returns `1 + prefix_len + atom_len`.

The caller, `is_clvm_canonical()`, then uses that unchecked value to advance its own cursor and, if more tokens remain, immediately re-indexes the buffer at the new offset: [2](#0-1) 
The only length check performed is the *final* `return offset == len(clvm_buffer)` check reached after `tokens_left == 0` — that is, after all such indexing has already occurred. A crafted buffer that starts a multi-byte length prefix near the end of the buffer (e.g., a byte matching the `0b11111100` pattern selecting a 5-byte extended prefix, followed by fewer than 5 bytes) causes `clvm_buffer[offset]` to be indexed past the end of the buffer, raising an unhandled Python `IndexError`.

This function is invoked directly on attacker-controlled bytes during mempool spend-bundle admission, with the input value being exactly `coin_spend.puzzle_reveal` and `coin_spend.solution` from an incoming, unvalidated `SpendBundle`: [3](#0-2) 
`validate_spend_bundle()` is called with no surrounding `try/except` for this specific call from `add_spend_bundle()`: [4](#0-3) 
so an `IndexError` raised inside `is_clvm_canonical`/`is_atom_canonical` propagates out of `add_spend_bundle()` uncaught by this code path.

### Impact Explanation
Any unprivileged actor able to submit a spend bundle (via the wallet RPC `push_tx`, peer transaction relay, or any code path that calls `MempoolManager.add_spend_bundle()`) can construct a `puzzle_reveal` or `solution` buffer whose trailing bytes form a truncated multi-byte CLVM atom length prefix. This deterministically raises an unhandled `IndexError` inside the mempool's canonical-serialization check, instead of the intended `False`/`Err.INVALID_COIN_SOLUTION` rejection. Because the calling function does not catch this specific exception type, the error propagates out of the per-spend-bundle validation routine, which is a spend-triggered failure in the transaction-admission pipeline reachable from a single submitted spend bundle.

### Likelihood Explanation
High likelihood: no signature, no privileged puzzle, and no prior blockchain state is required — the attacker only needs to submit an arbitrary `SpendBundle` whose puzzle reveal or solution ends with a truncated CLVM atom length-prefix byte. The `is_clvm_canonical` check runs unconditionally on every coin spend's puzzle_reveal and solution in `validate_spend_bundle()`, so this path is exercised on every submitted transaction.

### Recommendation
Add explicit bounds checks in `is_atom_canonical()` before indexing `clvm_buffer[offset]` in the prefix-parsing loop (verify `offset < len(clvm_buffer)` on each read), and validate that `1 + prefix_len + atom_len <= len(clvm_buffer)` before returning. In `is_clvm_canonical()`, treat any parse condition that would run off the end of the buffer as "not canonical" (return `False`) rather than relying on Python's implicit `IndexError`, and ensure callers of these canonicalization checks handle unexpected exceptions defensively so a malformed buffer cannot escape as an unhandled exception from spend-bundle validation.

### Proof of Concept
```python
from chia.full_node.mempool_manager import is_clvm_canonical

# 0xFC selects the 5-extra-byte length prefix (prefix_len=5), but only
# 2 bytes follow instead of the required 5+ before any atom payload.
malicious_buffer = bytes([0xFC, 0x00, 0x00])

# Raises IndexError instead of returning False, because is_atom_canonical()
# indexes clvm_buffer[offset] without checking remaining buffer length.
is_clvm_canonical(malicious_buffer)
```
Embedding `malicious_buffer` as the trailing bytes of a `CoinSpend.puzzle_reveal` or `CoinSpend.solution` inside a submitted `SpendBundle` causes `MempoolManager.validate_spend_bundle()` (called from `add_spend_bundle()`) to raise this uncaught `IndexError` instead of returning `Err.INVALID_COIN_SOLUTION`.

### Citations

**File:** chia/full_node/mempool_manager.py (L177-183)
```python
    atom_len = b & mask
    for i in range(prefix_len):
        atom_len <<= 8
        offset += 1
        atom_len |= clvm_buffer[offset]

    return 1 + prefix_len + atom_len, atom_len >= min_value
```

**File:** chia/full_node/mempool_manager.py (L212-224)
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

        if tokens_left == 0:
            break
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
