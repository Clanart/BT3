Given my investigation, I found a concrete analog: unchecked/unbounded array indexing when parsing attacker-supplied byte buffers, directly mirroring the CVE's root cause (index into a buffer without validating it stays within bounds).

### Title
Missing bounds check in CLVM canonical-atom length-prefix parser can raise unhandled `IndexError` on crafted spend-bundle puzzle/solution bytes - ([File: chia/full_node/mempool_manager.py])

### Summary
`is_atom_canonical()` in `chia/full_node/mempool_manager.py` decodes the CLVM variable-length atom size prefix by repeatedly indexing into the raw byte buffer (`clvm_buffer[offset]`) for `prefix_len` (up to 5) additional bytes, without ever checking that `offset` stays within `len(clvm_buffer)`.

### Finding Description
`is_atom_canonical(clvm_buffer, offset)` reads the leading byte to determine how many additional length-prefix bytes follow (up to 5, for the largest size class), then loops: [1](#0-0) 

There is no check that `offset` is less than `len(clvm_buffer)` before each `clvm_buffer[offset]` read. If the last atom in the buffer has a length-prefix byte indicating a multi-byte header (e.g. the `0b11110000`/`0b11111000` patterns requiring 3-5 trailing bytes) but the buffer ends before those bytes are present, the loop indexes past the end of the buffer and Python raises an unhandled `IndexError`.

This function is invoked from `is_clvm_canonical()`, which is called directly on attacker-controlled data during mempool spend-bundle validation: [2](#0-1) 

`bytes(coin_spend.puzzle_reveal)` and `bytes(coin_spend.solution)` come straight from a submitted `SpendBundle`'s `CoinSpend`s — fully attacker-controlled bytes reachable by any unprivileged wallet/peer submitting a transaction to the mempool, analogous to how the CVE's crafted video file drives an out-of-bounds array read in `put_epel_hv_fallback` via unchecked index computation from untrusted input.

### Impact Explanation
An unhandled `IndexError` raised deep inside `validate_spend_bundle()` → `add_spend_bundle()` during mempool admission is a crafted-input, spend-triggered fault in transaction-processing logic. Depending on whether upstream callers in `full_node.py` catch only specific exception types (e.g. `ValidationError`) rather than a bare `Exception`, this can propagate as an unexpected exception in the node's mempool-processing coroutine, disrupting spend-bundle admission for that peer/request — a spend-triggered transaction-processing halt in the sense the analog rules call out. I was not able to fully confirm within the available search budget whether every call path wraps `add_spend_bundle` in a broad enough `except Exception` to prevent any observable disruption; this remains an open verification point.

### Likelihood Explanation
Reaching this code requires only submitting a normal-looking `SpendBundle` (via RPC, peer relay, or a wallet action) whose `puzzle_reveal` or `solution` bytes end mid-way through a multi-byte CLVM atom length prefix. No special privileges, signatures bypass, or consensus-height gating are required — `is_clvm_canonical` runs unconditionally as part of standard mempool validation. Constructing such a truncated-prefix byte string is trivial and mirrors the existing test harness in the repo that already exercises overflow-sized atom headers (`chia/_tests/clvm/test_chialisp_deserialization.py`), confirming that malformed/overflow length prefixes are a recognized, reachable input class.

### Recommendation
Add an explicit bounds check in `is_atom_canonical()` before each `clvm_buffer[offset]` access (e.g., raise/return a defined "non-canonical"/error result if `offset >= len(clvm_buffer)`), and ensure `is_clvm_canonical()` treats any `IndexError`/malformed length prefix as `False` rather than allowing an unhandled exception to escape into the mempool validation call stack.

### Proof of Concept
1. Construct a `CoinSpend` whose `solution` (or `puzzle_reveal`) serialized bytes end with a single length-prefix byte matching the `0b11110000` pattern (indicating 3 trailing length bytes should follow) but provide zero trailing bytes, e.g. bytes ending in `\xf0` with nothing after it.
2. Submit this as part of a `SpendBundle` through normal transaction submission (RPC `push_tx` / peer `new_transaction` flow) so it reaches `MempoolManager.add_spend_bundle()` → `validate_spend_bundle()`.
3. When `is_clvm_canonical(bytes(coin_spend.solution))` is invoked at `chia/full_node/mempool_manager.py:723-726`, `is_atom_canonical()` attempts `clvm_buffer[offset]` past the end of the buffer, raising an unhandled `IndexError` inside spend-bundle validation. [3](#0-2) [4](#0-3)

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
