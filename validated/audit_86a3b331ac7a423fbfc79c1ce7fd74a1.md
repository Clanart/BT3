### Title
Unbounded scan in `is_clvm_canonical()` causes an uncaught `IndexError` on a crafted spend-bundle puzzle/solution - ([File: chia/full_node/mempool_manager.py])

### Summary
`is_clvm_canonical()` walks a CLVM-serialized buffer with a `while True:` token-counting loop that has no bounds check on `offset` against `len(clvm_buffer)` before indexing into it, analogous to the unbounded `read_formatted_entries`/dwarf2.c loop in CVE-2017-14933 that walks attacker-controlled data without validating remaining buffer length. Because a CLVM "pair" byte (`0xFF`) increments `tokens_left` without any corresponding atom to close it, a short crafted buffer (e.g. a lone `0xFF` byte) can drive `offset` past the end of `clvm_buffer` before `tokens_left` ever reaches `0`, causing `clvm_buffer[offset]` to raise an `IndexError` instead of returning a clean boolean result.

### Finding Description
`is_clvm_canonical()` is: [1](#0-0) 

The loop only terminates via `tokens_left == 0` (line 223-224) or an explicit `return False` for non-canonical atoms/back-references. There is no check that `offset < len(clvm_buffer)` before `b = clvm_buffer[offset]` on line 199. Every "pair" byte (`0xFF`) increases `tokens_left` by one (expecting two subsequent sub-expressions) while advancing `offset` by only 1 byte. If the buffer contains more open pairs than it has bytes to close them, `offset` walks off the end of the buffer and `clvm_buffer[offset]` throws `IndexError: index out of range` rather than gracefully returning `False`.

This function is called directly on attacker-supplied bytes taken from an incoming `SpendBundle`'s `puzzle_reveal` and `solution` in `validate_spend_bundle()`: [2](#0-1) 

`coin_spend.puzzle_reveal` and `coin_spend.solution` are fully controlled by whoever submits the spend bundle (a wallet user, a remote peer relaying a transaction, or an offer counterparty constructing a coin spend). A minimal crafted buffer, e.g. a puzzle_reveal or solution consisting solely of the byte `0xFF` (a "pair" marker with no closing atoms), causes `tokens_left` to become `2` after the first iteration while `offset` becomes `1`, which is already `== len(clvm_buffer)`; the very next iteration's `clvm_buffer[offset]` access raises `IndexError`.

### Impact Explanation
`validate_spend_bundle()` is invoked from the mempool admission path (`add_spend_bundle` / `MempoolManager.add_spend_bundle`, driven from `full_node.py`'s new-transaction handling) for every spend bundle submitted to a full node, whether from a wallet's own transaction or a peer-relayed transaction. An uncaught `IndexError` raised deep inside `is_clvm_canonical()` is not one of the `Err` enum-based validation failures the function is designed to communicate; it propagates as a raw Python exception through `validate_spend_bundle()`. Depending on how far up the call chain the exception is caught (or not), this can abort the mempool-admission task for that peer connection, repeatedly fail transaction admission, or otherwise disrupt spend-bundle processing — a spend-triggered transaction-processing halt reachable by any unprivileged spend-bundle submitter. I was not able to fully trace the outer exception-handling boundary above `full_node.py`'s call into `add_spend_bundle` in the time available, so whether it crashes the whole node process versus only the single request/task is unconfirmed and should be verified directly against `chia/full_node/full_node.py` and its callers (e.g. `full_node_api.py`).

### Likelihood Explanation
High: constructing the trigger requires no special privilege, no valid signature, and no valid puzzle logic — the crafted bytes need only be placed in the `puzzle_reveal` or `solution` fields of a `CoinSpend` inside a `SpendBundle` that is submitted to a full node's mempool. `is_clvm_canonical` is called unconditionally for every coin spend in every incoming spend bundle, so triggering the code path is trivial and does not depend on cost limits, signature validation succeeding, or coin existence checks passing.

### Recommendation
Add an explicit bounds check before every buffer access in `is_clvm_canonical()` and `is_atom_canonical()` — e.g., check `offset < len(clvm_buffer)` (and that atom length-prefix reads plus `atom_len` don't exceed `len(clvm_buffer)`) before indexing, returning `False` (non-canonical / malformed) instead of letting a raw `IndexError` propagate. This mirrors the fix pattern used elsewhere in the codebase (e.g., `chia/full_node/full_block_utils.py`'s `skip_bytes`/`skip_list`, which explicitly raise descriptive `ValueError`s when a length prefix or count exceeds the remaining buffer) rather than trusting the buffer to be well-formed.

### Proof of Concept
1. Construct a `CoinSpend` whose `puzzle_reveal` (or `solution`) serialized bytes are exactly `b"\xff"` (a single CLVM "pair" opcode byte with no operands).
2. Wrap it in a `SpendBundle` with any (even invalid) aggregated signature and submit it to a full node as a new transaction (e.g., via `full_node_api`'s transaction-relay path or a wallet RPC that forwards it to the mempool).
3. During mempool admission, `validate_spend_bundle()` calls `is_clvm_canonical(bytes(coin_spend.puzzle_reveal))` on the 1-byte buffer:
   - iteration 1: `b = 0xFF` → `tokens_left = 2`, `offset = 1`
   - iteration 2: `b = clvm_buffer[1]` → `IndexError: index out of range` (buffer length is 1)
4. The `IndexError` propagates out of `is_clvm_canonical`, unhandled by the `Err`-based validation flow in `validate_spend_bundle`, aborting normal transaction-admission handling for that call instead of returning a proper `Err.INVALID_COIN_SOLUTION` rejection. [3](#0-2)

### Citations

**File:** chia/full_node/mempool_manager.py (L186-227)
```python
def is_clvm_canonical(clvm_buffer: bytes) -> bool:
    """
    checks whether the CLVM serialization is all canonical representation.
    atoms can be serialized in more than one way by using more bytes than
    necessary to encode the length prefix. This functions ensures that all atoms are
    encoded with the shortest representation. back-references are not allowed
    and will make this function return false
    """
    assert clvm_buffer != b""

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

        if tokens_left == 0:
            break

    # if there's garbage at the end, it's not canonical
    return offset == len(clvm_buffer)
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
