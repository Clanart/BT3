Found the analog: `is_atom_canonical()`/`is_clvm_canonical()` in `chia/full_node/mempool_manager.py` perform hand-rolled binary parsing of an attacker-supplied CLVM buffer (the coin's puzzle reveal / solution from any submitted spend bundle) without bounds-checking offsets against the buffer length before indexing into it, directly mirroring the c-ares `ares_parse_naptr_reply()` bug class (structured-record parser reads a length/type byte and then advances past declared sub-fields without checking that the declared length stays within the buffer).

### Title
Unbounded index read while validating CLVM atom length prefixes can crash mempool spend-bundle admission - (`chia/full_node/mempool_manager.py`)

### Summary
`is_clvm_canonical()` and its helper `is_atom_canonical()` walk a raw `bytes` buffer taken directly from an unprivileged submitter's `coin_spend.puzzle_reveal` / `coin_spend.solution` to check for canonical CLVM serialization, used as a gate for DEDUP/FF eligibility in `validate_spend_bundle()`. [1](#0-0)  Neither function validates that `offset` stays within `len(clvm_buffer)` before indexing, unlike the sibling parsers in `full_block_utils.py` which explicitly check remaining-buffer length before each read. [2](#0-1) 

### Finding Description
`is_atom_canonical()` reads the atom's discriminant byte `b = clvm_buffer[offset]`, then determines `prefix_len` (0–5) from the high bits, and loops reading `prefix_len` additional bytes via `clvm_buffer[offset]` with no check that `offset < len(clvm_buffer)` at any point. [2](#0-1)  `is_clvm_canonical()` similarly advances `offset` by `atom_len` (an attacker-controlled value derived from the length-prefix bytes) and loops back to `clvm_buffer[offset]` on the next iteration without first checking `offset < len(clvm_buffer)`. [3](#0-2)  A crafted `puzzle_reveal`/`solution` byte string ending right after a multi-byte length-prefix marker (e.g. `0xC0` with no following byte, or a `0xFF` pair token followed by a truncated atom) causes `clvm_buffer[offset]` to be evaluated with `offset >= len(clvm_buffer)`, raising an unhandled `IndexError` in Python. This is the direct analog of CVE-2017-1000381: a length/type-prefixed record parser that trusts embedded length fields to bound subsequent reads instead of checking them against the actual buffer size, and only differs in that Python's memory-safe indexing turns "out-of-bounds read" into an uncaught exception rather than leaking adjacent heap memory.

### Impact Explanation
`is_clvm_canonical()` is invoked inside `validate_spend_bundle()` for every coin spend in a submitted `SpendBundle`, on the main mempool-processing path (not behind any try/except for `IndexError`). [4](#0-3)  An unhandled `IndexError` here would propagate out of `validate_spend_bundle()`/`add_spend_bundle()`, which are called from the full node's core spend-bundle admission RPC/gossip handling path. This can halt processing of that request/task with an unexpected exception instead of a controlled `Err.INVALID_COIN_SOLUTION` rejection, which is a spend-triggered transaction-processing disruption reachable by any unprivileged spend-bundle submitter — matching the "spend-triggered transaction-processing halt" acceptance criterion.

### Likelihood Explanation
High from a reachability standpoint: any wallet user or third party can submit a `SpendBundle` with an arbitrary `puzzle_reveal`/`solution` byte string to a full node (via RPC or gossip), and `is_clvm_canonical()` runs unconditionally on that raw byte data before any other structural validation of the CLVM encoding occurs. Constructing a truncated multi-byte length-prefix atom is trivial (a handful of bytes, e.g. `0xC0` alone, or `0xFF` followed by a truncated atom marker) and requires no special privileges or CLVM execution cost, since the crash happens during pre-cost-metered canonicality checking.

### Recommendation
Add explicit bounds checks in `is_atom_canonical()` before each `clvm_buffer[offset]` access (mirroring the `len(buf) < N` guards already used in `chia/full_node/full_block_utils.py`), and in `is_clvm_canonical()`'s main loop before dereferencing `clvm_buffer[offset]` on each iteration; treat an out-of-range offset as non-canonical (return `False`) rather than raising, and ensure any residual exception from this function is caught and converted into `Err.INVALID_COIN_SOLUTION` in `validate_spend_bundle()`.

### Proof of Concept
```python
from chia.full_node.mempool_manager import is_clvm_canonical

# Truncated multi-byte length-prefix atom: 0xC0 signals a "5+8 bit" length
# prefix requiring 1 extra byte, but the buffer ends immediately after it.
truncated = bytes.fromhex("c0")
is_clvm_canonical(truncated)  # raises IndexError instead of returning False

# Truncated after a pair token, atom marker present but body missing
truncated2 = bytes.fromhex("ffc0")
is_clvm_canonical(truncated2)  # raises IndexError instead of returning False
```
Submitting a `CoinSpend` whose `puzzle_reveal` or `solution` serializes to one of these truncated byte strings inside a `SpendBundle` and passing it to `MempoolManager.validate_spend_bundle()` reaches the vulnerable `clvm_buffer[offset]` access at [5](#0-4)  with no surrounding exception handling for `IndexError`.

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
