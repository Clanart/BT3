### Title
Unbounded index read in CLVM canonical-encoding check can raise an uncaught `IndexError`, halting mempool transaction admission - ([File: chia/full_node/mempool_manager.py])

### Summary
`validate_spend_bundle()` calls `is_clvm_canonical()` on the raw, attacker-supplied `puzzle_reveal` and `solution` bytes of every coin spend in a submitted `SpendBundle`, before any exception-safe bounds validation is applied to the atom length-prefix parsing performed by `is_atom_canonical()`. [1](#0-0) 

### Finding Description
`is_atom_canonical(clvm_buffer, offset)` decodes a CLVM atom's length-prefix by reading `clvm_buffer[offset]` and then, depending on the prefix pattern, reads up to `prefix_len` (0–5) additional bytes at `clvm_buffer[offset+1]`, `offset+2`, etc., in a loop with no check that `offset` stays within `len(clvm_buffer)`: [2](#0-1) 

This is invoked by `is_clvm_canonical()`, which walks an entire CLVM-serialized buffer atom-by-atom to check whether every atom uses the minimal (canonical) length encoding, again with no independent bounds guard around the call: [3](#0-2) 

`is_clvm_canonical()` is called directly on the untrusted `coin_spend.puzzle_reveal` and `coin_spend.solution` bytes for every coin spend inside `MempoolManager.validate_spend_bundle()`, which runs for every spend bundle submitted to the mempool (via RPC `push_tx`, wallet transactions, or peer relay reaching the local node's admission path): [4](#0-3) 

An attacker can craft a puzzle reveal or solution whose final bytes contain a multi-byte atom length-prefix marker (e.g. `0xFC`..`0xFF`, requiring up to 5 additional prefix bytes) positioned such that fewer than `prefix_len` bytes remain in the buffer. Because `is_atom_canonical` only bounds-checks via Python's native list/bytes indexing (which raises `IndexError` on out-of-range access rather than being validated explicitly), this raises an unhandled `IndexError` instead of the `ValueError`/`ConsensusError` that the rest of the condition/atom-parsing code paths deliberately raise and catch. This is the same bug class as CVE-2018-16526 (FreeRTOS+TCP `usGenerateProtocolChecksum`/`prvProcessIPPacket`): a checksum/length-prefix parsing routine that walks past the end of an attacker-supplied buffer because the trailing bytes needed to complete a multi-byte field are missing.

### Impact Explanation
`is_clvm_canonical` is reached before any other validation on the raw puzzle/solution bytes in `validate_spend_bundle`, which itself is called from `add_spend_bundle`, the core mempool-admission entry point invoked for every incoming `SpendBundle`/`push_tx`. If the `IndexError` is not caught anywhere in the call chain up to the RPC/peer message handler that invokes `add_spend_bundle`, a single malformed spend bundle can throw an unhandled exception during mempool processing, which is exactly the "spend-triggered transaction-processing halt" impact category called out in the validation criteria — an unprivileged submitter causing normal transaction admission to fail/crash for the node processing the bundle.

### Likelihood Explanation
Exploitation requires no special privileges: any wallet user, offer counterparty, or mempool submitter can construct a `CoinSpend` with a crafted trailing byte sequence in the puzzle reveal or solution and submit it as a normal transaction. Constructing such a buffer is straightforward (append a single length-prefix marker byte such as `0xFC` at the very end of the puzzle_reveal/solution bytes with no continuation bytes following it).

### Recommendation
Add explicit bounds checking in `is_atom_canonical` before reading each of the `prefix_len` continuation bytes (mirroring the pattern already used in `chia/full_node/full_block_utils.py`'s `skip_bytes`/`skip_list`, which check `len(buf)` before indexing and raise a `ValueError` instead of allowing an `IndexError`). Ensure any exception raised by `is_clvm_canonical`/`is_atom_canonical` is a handled `ValueError`/`ConsensusError` that propagates through `validate_spend_bundle` as an admission failure (e.g., `Err.INVALID_COIN_SOLUTION`) rather than an uncaught exception.

### Proof of Concept
Note: I was not able to fully trace whether an `IndexError` raised inside `validate_spend_bundle` is caught by an outer `try/except` somewhere in the RPC/peer message-handling stack before reaching `add_spend_bundle`/`validate_spend_bundle`, due to remaining iteration budget. This should be verified directly against the running code (e.g. by unit-testing `is_clvm_canonical` with a crafted buffer such as `bytes([0xFF, 0xFC])` — a pair marker followed by a truncated 5-byte-prefix atom marker with no continuation bytes — and confirming whether `add_spend_bundle` surfaces a controlled `Err` or an unhandled `IndexError`) before treating this as confirmed exploitable, since the described impact fully depends on the exception not being caught at a higher level.

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
