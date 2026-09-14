### Title
Out-of-bounds buffer read in mempool canonical-CLVM check on submitted spend bundles - ([File: chia/full_node/mempool_manager.py])

### Summary
`is_atom_canonical()` in `chia/full_node/mempool_manager.py` decodes a CLVM atom length-prefix by indexing directly into the raw `clvm_buffer` for up to five additional prefix bytes, with no check that those bytes actually exist in the buffer before reading them. This mirrors the tcpdump `EXTRACT_16BITS`/`stp_print` bug class in ALPINE-CVE-2017-11108: a fixed-size field is extracted from attacker-supplied bytes without first validating there is enough remaining buffer, leading to an out-of-bounds read and a crash on malformed/truncated input.

### Finding Description
`is_atom_canonical()` computes `prefix_len` (0–5) from the first byte, then loops: [1](#0-0) 
```
atom_len = b & mask
for i in range(prefix_len):
    atom_len <<= 8
    offset += 1
    atom_len |= clvm_buffer[offset]
```
There is no bounds check that `offset` (after incrementing up to `prefix_len` times) stays within `len(clvm_buffer)` before the `clvm_buffer[offset]` read. This function is invoked from `is_clvm_canonical()`, which walks the entire serialized buffer atom by atom and calls `is_atom_canonical()` whenever it encounters a multi-byte-length atom marker (`b > 0x80`): [2](#0-1) 

`is_clvm_canonical()` is called directly on attacker-controlled bytes — the `puzzle_reveal` and `solution` of every coin spend in a submitted spend bundle — inside `MempoolManager.validate_spend_bundle()`, which runs for every spend bundle that reaches mempool admission (from RPC submission or peer relay): [3](#0-2) 

If a submitted `puzzle_reveal` or `solution` ends with a byte such as `0xFC`–`0xFE`-range prefix marker (indicating a 5-byte length prefix) placed near or at the end of the buffer, with fewer than the required trailing bytes actually present, `clvm_buffer[offset]` will raise an unhandled `IndexError` instead of returning a controlled `Err`/`ValueError`. This exactly parallels the tcpdump `EXTRACT_16BITS` bug: a length-driven fixed read past the end of a buffer supplied by an untrusted party.

### Impact Explanation
This function sits directly on the mempool admission path (`validate_spend_bundle`), reachable by any unprivileged spend-bundle submitter (via `push_tx` RPC or normal peer transaction relay). An `IndexError` raised here is not one of the `Err` codes the surrounding code expects (`Err.INVALID_COIN_SOLUTION`), so it propagates as an unexpected exception out of `validate_spend_bundle()`/`add_spend_bundle()`. Depending on how far up the call stack it is caught, this can crash the processing of that mempool add attempt or the surrounding async task, producing a spend-triggered transaction-processing halt for the affected code path — a remotely triggerable denial-of-service using a single, cheaply crafted spend bundle, analogous in effect and root cause (unbounded fixed-size extraction from untrusted bytes) to the tcpdump CVE.

### Likelihood Explanation
High likelihood of triggerability: the attacker fully controls `puzzle_reveal`/`solution` bytes of a spend bundle they submit, and only needs to place a specific single "extended length prefix" marker byte near the end of the buffer with insufficient trailing bytes to trigger the out-of-range index. No signature or coin validity is required to reach this code — it runs before/alongside canonical-form checks during mempool admission of any submitted spend bundle.

### Recommendation
Add explicit bounds checks in `is_atom_canonical()` before each `clvm_buffer[offset]` access (verify `offset < len(clvm_buffer)` for each of the up to `prefix_len` reads) and return a deterministic non-canonical/invalid result (or raise a handled `ValueError`) instead of allowing `IndexError` to propagate. Ensure `is_clvm_canonical()`/`validate_spend_bundle()` catches any such decode error and maps it to `Err.INVALID_COIN_SOLUTION` rather than allowing an unhandled exception to escape mempool processing.

### Proof of Concept
Construct a `CoinSpend` whose `solution` (or `puzzle_reveal`) ends with a byte in the `0xF8`–`0xFE` range (selecting a 4–5 byte length prefix) as the very last byte of the buffer, e.g. a buffer ending in `...\xfc` with zero trailing bytes. Submitting a `SpendBundle` containing this coin spend via `push_tx`/mempool relay causes `MempoolManager.validate_spend_bundle()` to call `is_clvm_canonical(bytes(coin_spend.solution))`, which reaches `is_atom_canonical()` and attempts `clvm_buffer[offset]` for `offset >= len(clvm_buffer)`, raising an unhandled `IndexError` instead of a graceful validation failure.

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

**File:** chia/full_node/mempool_manager.py (L723-726)
```python
            if not is_clvm_canonical(bytes(coin_spend.puzzle_reveal)) or not is_clvm_canonical(
                bytes(coin_spend.solution)
            ):
                return Err.INVALID_COIN_SOLUTION, None, []
```
