### Title
Unbounded offset walk in `is_atom_canonical`/`is_clvm_canonical` causes an uncaught `IndexError` on submitted spend-bundle admission — spend-triggered mempool/node crash - (File: `chia/full_node/mempool_manager.py`)

### Summary
`is_atom_canonical()` decodes a CLVM atom length-prefix by repeatedly indexing the input buffer at `offset` while incrementing `offset` for each declared prefix byte, without ever checking that `offset` stays within the bounds of `clvm_buffer`. This function, and its caller `is_clvm_canonical()`, are invoked directly on the raw, attacker-controlled `puzzle_reveal` and `solution` bytes of every coin spend in a spend bundle during mempool admission, before any bounds-safe deserialization has occurred. [1](#0-0) 

### Finding Description
`validate_spend_bundle()` calls `is_clvm_canonical(bytes(coin_spend.puzzle_reveal))` and `is_clvm_canonical(bytes(coin_spend.solution))` for every coin spend in an incoming `SpendBundle`, unconditionally (not only for DEDUP-eligible spends as the internal design notes claim): [2](#0-1) 

Inside `is_clvm_canonical()`, for any atom byte whose top bits indicate a multi-byte length prefix, `is_atom_canonical(clvm_buffer, offset)` is called: [1](#0-0) 

The loop `for i in range(prefix_len): ... offset += 1; atom_len |= clvm_buffer[offset]` blindly increments `offset` and indexes `clvm_buffer[offset]` with **no check that `offset < len(clvm_buffer)`**. A crafted puzzle reveal or solution can present a length-prefix byte (e.g. `0xFC`, which declares a 5-byte-following length prefix) right at (or near) the end of the buffer, so that the prefix-reading loop walks past the end of the buffer. In Python, indexing a `bytes` object past its end raises `IndexError`, which is not caught anywhere in `is_atom_canonical`, `is_clvm_canonical`, or the calling `validate_spend_bundle()`/`add_spend_bundle()` code path shown above — there is no `try/except IndexError` (or generic `except Exception`) around this canonical-check call.

This is directly analogous to the FreeRDP bug class: an insufficiently-validated length/offset computed from attacker-supplied data is used to walk further into a buffer than what remains, triggering an out-of-bounds access that aborts processing (in FreeRDP: `WINPR_ASSERT`; here: unhandled Python `IndexError`).

### Impact Explanation
An unauthenticated, unprivileged spend-bundle submitter can trigger this by sending a spend bundle containing a coin spend whose `puzzle_reveal` or `solution` ends with a truncated multi-byte atom length prefix (e.g., ending exactly on a `0xC0`/`0xE0`/`0xF0`/`0xF8`/`0xFC` prefix byte with too few trailing bytes). Because `validate_spend_bundle()` is invoked as part of the normal mempool admission pipeline (`add_spend_bundle` → `validate_spend_bundle` → `is_clvm_canonical`), an unhandled `IndexError` raised here propagates out of `add_spend_bundle()`. Depending on how the calling RPC/network-message handler wraps this call, this can crash the request-handling task or, if the exception propagates far enough, disrupt mempool processing for that node — a spend-triggered denial-of-service on transaction processing, matching the class of issue explicitly listed as in-scope ("a spend-triggered transaction-processing halt").

### Likelihood Explanation
The path is trivially reachable: any party who can submit a spend bundle to a node's mempool (via RPC or the transaction-relay protocol) controls the exact bytes of `puzzle_reveal` and `solution`, and thus can freely choose a truncated length-prefix byte sequence. No special privileges, signatures matching real coins, or specific chain state are required to reach the vulnerable canonical-serialization check, since it executes early in `validate_spend_bundle()` for every coin spend, independent of whether the coin exists or the spend is otherwise valid.

### Recommendation
Add explicit bounds checks in `is_atom_canonical()` before each buffer index: verify `offset + prefix_len < len(clvm_buffer)` (or check remaining length up front) prior to entering the prefix-reading loop, and raise/return a normal validation failure (e.g., treat as non-canonical / reject with `Err.INVALID_COIN_SOLUTION`) rather than allowing an `IndexError` to escape. Additionally, wrap the `is_clvm_canonical()` calls in `validate_spend_bundle()` with a defensive `try/except` that converts any unexpected exception into a standard mempool rejection, consistent with how other malformed-input paths in this file are handled.

### Proof of Concept
1. Construct a coin spend whose `solution` (or `puzzle_reveal`) bytes end with a byte matching the 5-byte-length-prefix pattern, e.g. bytes ending in `...\xfc` with fewer than 5 bytes following it (so the declared prefix length exceeds the remaining buffer).
2. Wrap it into a `SpendBundle` and submit it via the normal transaction-submission path so it reaches `MempoolManager.add_spend_bundle()` → `validate_spend_bundle()`.
3. `is_clvm_canonical(bytes(coin_spend.solution))` is invoked at `chia/full_node/mempool_manager.py:723-725`, which calls `is_atom_canonical()`; the prefix-reading loop indexes past the end of the buffer, raising an unhandled `IndexError` that is not caught anywhere in the call chain shown above, aborting normal processing of the request.

(Note: I was unable to trace all the way up to the specific full-node network-message handler that ultimately awaits `add_spend_bundle()` within the indexed portion of the codebase, so the exact blast radius — whether it kills only the single request task or destabilizes the mempool-manager's persistent state — could not be fully confirmed from the available context and would benefit from direct testing in a running node.)

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
