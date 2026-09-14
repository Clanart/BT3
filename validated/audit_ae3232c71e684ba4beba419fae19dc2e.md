Confirmed: `is_clvm_canonical()` is called unconditionally on every coin spend's `puzzle_reveal` and `solution` in `MempoolManager.validate_spend_bundle()`, reachable directly from any unprivileged spend-bundle submitter. [1](#0-0) 

### Title
Out-of-bounds read / crash in `is_atom_canonical` via truncated CLVM length-prefix in submitted spend bundle solution/puzzle_reveal - (File: chia/full_node/mempool_manager.py)

### Summary
`is_atom_canonical()` parses a CLVM atom's multi-byte length prefix by indexing into the raw `clvm_buffer` for `prefix_len` (up to 5) additional bytes without ever checking that those offsets are within the buffer's bounds, mirroring the ALPINE-CVE-2019-3861 pattern of trusting an attacker-controlled length/padding field without validating it against the actual buffer size before reading past it.

### Finding Description
`is_atom_canonical(clvm_buffer, offset)` reads the leading byte at `offset`, determines a `prefix_len` (0–5) based on the high bits, and then loops reading `clvm_buffer[offset]` for each of the `prefix_len` continuation bytes: [2](#0-1) 

There is no bounds check comparing `offset + prefix_len` against `len(clvm_buffer)` before these reads. `is_clvm_canonical()`, which calls `is_atom_canonical()`, is invoked directly on attacker-supplied bytes — `bytes(coin_spend.puzzle_reveal)` and `bytes(coin_spend.solution)` — for every coin spend in every spend bundle submitted to the mempool, before any other bounds validation of the atom occurs: [3](#0-2) 

An attacker can craft a `puzzle_reveal` or `solution` blob whose final byte is a multi-byte length-prefix marker (e.g. `0xFC`, indicating a 5-byte continuation) placed at or near the very end of the buffer, so that the follow-on read `clvm_buffer[offset]` inside the `for i in range(prefix_len)` loop indexes past the end of the buffer.

### Impact Explanation
In CPython, indexing a `bytes` object past its end raises `IndexError` rather than causing memory corruption, but this exception is unhandled at the call site in `validate_spend_bundle()` — there is no try/except around the `is_clvm_canonical()` calls. An uncaught `IndexError` propagating out of spend-bundle validation would abort processing of that bundle-add path, and depending on how the mempool manager's async task/executor wraps this call, could either just fail that one bundle (low impact) or, if unhandled further up in the connection/task machinery, disrupt processing of subsequently queued bundles — a transaction-processing halt triggered by a single malicious but otherwise well-formed-looking spend bundle. This matches the CVE's "attacker-controlled length exceeds actual data, causing an out-of-bounds read / DoS" bug class, translated to the Python mempool admission path.

### Likelihood Explanation
High reachability: any wallet user or unprivileged peer can submit a `SpendBundle` with a crafted `puzzle_reveal`/`solution` whose CLVM byte stream is truncated right after a multi-byte atom-length marker. No prior authentication or special privilege is required to reach `MempoolManager.validate_spend_bundle()`.

### Recommendation
Add explicit bounds checks in `is_atom_canonical()` before reading each continuation byte (e.g. `if offset + prefix_len >= len(clvm_buffer): return <some length>, False` or raise a caught `ValueError`), and/or wrap the `is_clvm_canonical()` calls in `validate_spend_bundle()` with exception handling that safely rejects the bundle (`Err.INVALID_COIN_SOLUTION`) instead of allowing an unhandled `IndexError` to propagate.

### Proof of Concept
Construct a `solution` (or `puzzle_reveal`) blob ending in a truncated multi-byte atom prefix, e.g. `bytes.fromhex("fc00")` — the leading byte `0xFC` signals a 5-byte length continuation, but only 1 byte follows. Submitting a `SpendBundle` whose `coin_spend.solution` equals this blob causes `is_clvm_canonical()` → `is_atom_canonical()` to attempt reading `clvm_buffer[offset]` for offsets beyond `len(clvm_buffer)`, raising an unhandled `IndexError` inside `validate_spend_bundle()`.

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

**File:** chia/full_node/mempool_manager.py (L670-726)
```python
    async def validate_spend_bundle(
        self,
        new_spend: SpendBundle,
        conds: SpendBundleConditions,
        spend_name: bytes32,
        first_added_height: uint32,
        get_coin_records: Callable[[Collection[bytes32]], Awaitable[list[CoinRecord]]],
        get_unspent_lineage_info_for_puzzle_hash: Callable[[bytes32], Awaitable[UnspentLineageInfo | None]],
    ) -> tuple[Err | None, MempoolItem | None, list[bytes32]]:
        """
        Validates new_spend with the given SpendBundleConditions, and
        spend_name, and the current mempool. The mempool should
        be locked during this call (blockchain lock).

        Args:
            new_spend: spend bundle to validate
            conds: result of running the clvm transaction
            spend_name: hash of the spend bundle data, passed in as an optimization
            first_added_height: The block height that `new_spend`  first entered this node's mempool.
                Used to estimate how long a spend has taken to be included on the chain.
                This value could differ node to node. Not preserved across full_node restarts.

        Returns:
            Optional[Err]: Err is set if we cannot add to the mempool, None if we will immediately add to mempool
            Optional[MempoolItem]: the item to add (to mempool or pending pool)
            list[bytes32]: conflicting mempool items to remove, if no Err
        """
        start_time = time.monotonic()
        if self.peak is None:
            return Err.MEMPOOL_NOT_INITIALIZED, None, []

        cost = conds.cost

        removal_names: set[bytes32] = set()
        additions_dict: dict[bytes32, Coin] = {}
        addition_amount: int = 0

        # Map of coin ID to SpendConditions
        spend_conditions = {bytes32(spend.coin_id): spend for spend in conds.spends}

        # if this happens, the SpendBundle doesn't match the
        # SpendBundleConditions.
        assert len(new_spend.coin_spends) == len(spend_conditions)

        bundle_coin_spends: dict[bytes32, BundleCoinSpend] = {}
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
