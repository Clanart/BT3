### Title
Unbounded length-prefix walk in `is_atom_canonical()` raises uncaught `IndexError`, halting mempool spend-bundle processing - ([File: chia/full_node/mempool_manager.py])

### Summary
`is_atom_canonical()` in `chia/full_node/mempool_manager.py` decodes a CLVM atom's variable-length size prefix (1–6 bytes) by looping and indexing forward into `clvm_buffer` without first checking that enough bytes remain in the buffer, mirroring the `xt_tcpmss` bug class of reading a length/option byte before verifying the remaining buffer covers it.

### Finding Description
`is_atom_canonical(clvm_buffer, offset)` reads the atom's leading byte `b`, decides a `prefix_len` (0–5 extra bytes) based on the high bits of `b`, and then walks forward reading `prefix_len` more bytes to compute `atom_len`, without any check that `offset + prefix_len < len(clvm_buffer)`: [1](#0-0) 

```python
atom_len = b & mask
for i in range(prefix_len):
    atom_len <<= 8
    offset += 1
    atom_len |= clvm_buffer[offset]
```

This is called from `is_clvm_canonical()` for every atom byte in the `0x81..0xFD` range encountered while scanning a CLVM-serialized buffer: [2](#0-1) 

`is_clvm_canonical()` in turn is invoked directly on attacker-controlled, unvalidated bytes — the raw `puzzle_reveal` and `solution` of every coin spend in a submitted `SpendBundle` — inside `MempoolManager.validate_spend_bundle()`, which is on the mempool admission path reachable by any unprivileged peer/wallet submitting a spend bundle: [3](#0-2) 

If a crafted puzzle reveal or solution ends immediately after a multi-byte length-prefix marker (e.g. a trailing byte `0xFC`, which signals a 5-byte length prefix, with fewer than 5 bytes remaining), the `for` loop's `clvm_buffer[offset]` access goes past the end of the buffer. In Python this raises an `IndexError` rather than performing an unsafe memory read (unlike the C kernel bug), but the exception is not caught anywhere in `is_clvm_canonical()`, `is_atom_canonical()`, or the caller `validate_spend_bundle()` — there is no `try/except` around this call, unlike `pre_validate_spendbundle()` which only wraps the Rust `validate_clvm_and_signature` call in `except ValueError`: [4](#0-3) 

The canonical-form check runs synchronously in `validate_spend_bundle()` (unlike `pre_validate_spendbundle`, it is not offloaded to a worker pool), so an uncaught `IndexError` here propagates up through `add_spend_bundle()` directly on the event loop / mempool-lock-held code path.

### Impact Explanation
An unprivileged spend-bundle submitter can craft a `SpendBundle` whose `puzzle_reveal` or `solution` triggers this uncaught `IndexError` during `validate_spend_bundle()`, which executes while holding the blockchain/mempool lock. Because the exception is not handled, it surfaces as an unhandled exception in the mempool admission pipeline rather than a clean `MempoolInclusionStatus.FAILED`/`Err.INVALID_COIN_SOLUTION` result, which is a spend-triggered transaction-processing halt/DoS against full-node mempool admission — the failure mode this scan is scoped to accept.

### Likelihood Explanation
The buffer bytes are fully attacker-controlled: `puzzle_reveal` and `solution` are taken verbatim from the submitted `CoinSpend` bytes, requiring no valid signature or successful CLVM execution to reach `is_clvm_canonical()` (it runs unconditionally on every coin spend in `validate_spend_bundle()`, before/independent of AGG_SIG or cost checks other than passing `pre_validate_spendbundle`'s CLVM run). Constructing a trailing byte sequence such as `...\xfc` (or `\xf8`, `\xf0`, `\xe0`, `\xc0` variants) with insufficient trailing bytes is trivial and deterministic — the existing test suite already exercises `is_atom_canonical`/`is_clvm_canonical` with similar edge-case hex strings (e.g. `"fc0000000000"`), demonstrating the boundary is reachable with straightforward crafted input, though none of the current tests specifically test a truncated/short trailing buffer that would hit the missing bounds check.

### Recommendation
In `is_atom_canonical()`, validate that `offset + prefix_len < len(clvm_buffer)` (or equivalently check remaining length before entering the loop) and return a definitive non-canonical/invalid result (or raise a caught `ValidationError`/`ConsensusError`) instead of indexing unconditionally. Additionally, wrap the `is_clvm_canonical()` calls in `validate_spend_bundle()` in a `try/except` (or make the length check exception-safe) so any malformed/truncated buffer results in `Err.INVALID_COIN_SOLUTION` rather than an unhandled exception during mempool admission.

### Proof of Concept
```python
from chia.full_node.mempool_manager import is_clvm_canonical

# Atom starting byte 0xFC signals: read 5 more prefix-length bytes.
# Buffer ends immediately after the first extra byte is consumed -> IndexError
# instead of a graceful "not canonical" result.
malicious_solution = bytes.fromhex("fc00")  # 0xFC + only 1 of 5 needed trailing bytes

is_clvm_canonical(malicious_solution)  # raises IndexError: index out of range
```
Submitting a `SpendBundle` whose `coin_spend.solution` or `coin_spend.puzzle_reveal` serializes to a buffer with this truncated trailing pattern causes `MempoolManager.validate_spend_bundle()` to raise this uncaught `IndexError` at [3](#0-2)  instead of returning `Err.INVALID_COIN_SOLUTION`.

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

**File:** chia/full_node/mempool_manager.py (L549-569)
```python
        try:
            flags = get_flags_for_height_and_constants(self.peak.height, self.constants)
            sbc: SpendBundleConditions
            sbc, new_cache_entries, duration = await self.pool.run_in_loop(
                validate_clvm_and_signature,
                spend_bundle,
                self.max_tx_clvm_cost,
                self.constants,
                flags | MEMPOOL_MODE,
                nice=(5, -fee_per_cost),
            )
        # validate_clvm_and_signature raises a ValueError with an error code
        except ValueError as e:
            # Convert that to a ValidationError
            if len(e.args) > 1:
                error = Err(e.args[1])
                raise ValidationError(error)
            else:
                raise ValidationError(Err.UNKNOWN)  # pragma: no cover
        finally:
            self._worker_queue_size -= 1
```

**File:** chia/full_node/mempool_manager.py (L723-726)
```python
            if not is_clvm_canonical(bytes(coin_spend.puzzle_reveal)) or not is_clvm_canonical(
                bytes(coin_spend.solution)
            ):
                return Err.INVALID_COIN_SOLUTION, None, []
```
