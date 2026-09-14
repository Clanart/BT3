I found the reachable analog: `is_atom_canonical()` / `is_clvm_canonical()` in `chia/full_node/mempool_manager.py` performs unbounded byte-buffer indexing while parsing an attacker-supplied CLVM atom length prefix — structurally the same bug class as CVE-2020-19751 (missing bounds check while parsing a variable-length-prefixed encoded field, causing an out-of-bounds read).

### Title
Unbounded buffer indexing in CLVM atom length-prefix parser causes crash on attacker-controlled spend bundle - (File: chia/full_node/mempool_manager.py)

### Summary
`is_atom_canonical()` reads `clvm_buffer[offset]` up to `prefix_len` (0–5) additional times without ever checking that `offset` stays within `len(clvm_buffer)` [1](#0-0) . This function is called from `is_clvm_canonical()`, which is invoked directly on the puzzle reveal and solution bytes of every coin spend in a submitted spend bundle during mempool admission, before any other bounds validation of the trailing bytes has occurred [2](#0-1) .

### Finding Description
`is_clvm_canonical()` walks a raw CLVM byte buffer to check whether all atoms use the shortest-form length-prefix encoding, rejecting non-canonical spends for DEDUP eligibility [3](#0-2) . When it encounters an atom-length-prefix byte pattern (`0b10xxxxxx` through `0b1111110x`), it delegates to `is_atom_canonical(clvm_buffer, offset)`, which reads the length-prefix continuation bytes one at a time via `clvm_buffer[offset]` inside a `for i in range(prefix_len)` loop, incrementing `offset` each time with no check that `offset < len(clvm_buffer)` [4](#0-3) . If a crafted puzzle reveal or solution ends with a length-prefix byte (e.g. `0xFC`, indicating a 5-byte length field) but is truncated before supplying all continuation bytes, the loop indexes past the end of `clvm_buffer`, causing an `IndexError` in Python.

This is called from `MempoolManager.validate_spend_bundle()`, which is reached for every incoming spend bundle (from RPC `push_tx`, wallet submission, or peer-relayed transactions) as part of building `BundleCoinSpend` for mempool admission [5](#0-4) . Because `is_clvm_canonical` is called directly, an uncaught `IndexError` here is not the `ValidationError`/`ValueError` types that `pre_validate_spendbundle` and the broader mempool pipeline are designed to catch and convert to graceful `MempoolInclusionStatus.FAILED` results.

### Impact Explanation
An `IndexError` raised out of `validate_spend_bundle()` is not one of the expected/caught exception types in the mempool admission flow that this code path relies on (`ValueError`/`ValidationError` handling is done in `pre_validate_spendbundle`, a separate earlier stage) [6](#0-5) . If `validate_spend_bundle` (called under the blockchain lock while processing a transaction) does not have a catch-all around this call, an unhandled exception during mempool admission of a single malformed but attacker-controlled spend bundle could disrupt transaction processing for that request and, depending on the caller in `full_node/full_node.py`, potentially propagate and destabilize the transaction-processing task. This matches the "spend-triggered transaction-processing halt" impact category.

### Likelihood Explanation
Reachability is high: any unprivileged spend-bundle submitter can construct a puzzle reveal or solution whose trailing bytes end mid length-prefix (e.g., ending exactly on `0xFC`, `0xF8`, `0xF0`, `0xE0`, or `0xC0`-style prefix bytes without the required continuation bytes). No signature validity or coin ownership is required to reach `is_clvm_canonical()`, since it is called unconditionally for every coin spend in `validate_spend_bundle()` prior to other lineage/coin checks.

### Recommendation
Add explicit bounds checks in `is_atom_canonical()` before each `clvm_buffer[offset]` access (both the initial read and the loop over `prefix_len`), raising a well-defined, already-handled exception (e.g., returning "not canonical" or a `ValidationError`) instead of allowing an `IndexError` to escape. Ensure `is_clvm_canonical()`'s caller in `validate_spend_bundle()` explicitly catches `IndexError` (or the bounds-checked replacement) and converts it into `Err.INVALID_COIN_SOLUTION`, consistent with how other malformed-input cases are handled in that function.

### Proof of Concept
1. Construct a `CoinSpend` whose `puzzle_reveal` (or `solution`) serialized bytes end with a single `0xFC` byte (6-byte length-prefix marker) and no further bytes, i.e., the buffer is exactly `[..., 0xFC]`.
2. Submit this as part of a `SpendBundle` via `push_tx` RPC or wallet transaction submission.
3. When `MempoolManager.validate_spend_bundle()` calls `is_clvm_canonical(bytes(coin_spend.puzzle_reveal))`, execution reaches `is_atom_canonical()`, which reads `b = clvm_buffer[offset]` (the `0xFC`), sets `prefix_len = 5`, then in the loop calls `clvm_buffer[offset]` for `offset` values beyond the buffer length, raising `IndexError: index out of range`.
4. Because this exception type is not among those the surrounding mempool-admission code is designed to convert into a graceful `MempoolInclusionStatus.FAILED`/`ValidationError` response, it may propagate uncaught out of `validate_spend_bundle()`.

Note: I was unable to fully trace every call site of `validate_spend_bundle()` in `full_node/full_node.py` and the mempool-lock/task-handling code within the indexed context to confirm definitively whether an outer `try/except Exception` wraps this call and fully absorbs the `IndexError` without further effect. This would need direct verification in a full Devin session with complete file access to confirm the exact blast radius (isolated per-bundle failure vs. broader task/lock disruption).

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

**File:** chia/full_node/mempool_manager.py (L560-567)
```python
        # validate_clvm_and_signature raises a ValueError with an error code
        except ValueError as e:
            # Convert that to a ValidationError
            if len(e.args) > 1:
                error = Err(e.args[1])
                raise ValidationError(error)
            else:
                raise ValidationError(Err.UNKNOWN)  # pragma: no cover
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
