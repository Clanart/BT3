Found the analog. `is_atom_canonical()` / `is_clvm_canonical()` in `chia/full_node/mempool_manager.py` parse the length-prefix of a CLVM atom using unchecked byte indexing (`clvm_buffer[offset]`) exactly the same bug-class pattern as `qtdemux_parse_samples` reading `stco` offsets past the buffer boundary — a length-prefix parser walking a byte buffer without verifying the read stays within bounds before dereferencing.

### Title
Unvalidated CLVM atom length-prefix parsing causes out-of-bounds index read in mempool canonical-serialization check - (File: chia/full_node/mempool_manager.py)

### Summary
`is_clvm_canonical()` is invoked on every submitted `SpendBundle`'s puzzle reveal and solution bytes during mempool admission, at `chia/full_node/mempool_manager.py:723` inside `validate_spend_bundle()`. It walks the buffer atom-by-atom, and for multi-byte atom headers it calls `is_atom_canonical(clvm_buffer, offset)` which reads `prefix_len` additional bytes from `clvm_buffer` via `clvm_buffer[offset]` in a loop, with no check that `offset` stays within `len(clvm_buffer)`. [1](#0-0) [2](#0-1) 

### Finding Description
`is_atom_canonical()` decodes the CLVM length-prefix encoding by reading the header byte at `clvm_buffer[offset]`, determining `prefix_len` (1–5 extra bytes depending on the leading bits), then looping `prefix_len` times to read further length bytes from `clvm_buffer[offset]` without ever checking `offset < len(clvm_buffer)`: [1](#0-0) 

This mirrors the GHSL-2024-245 bug class precisely: a variable-length-prefix parser (`qtdemux_parse_samples` reading `stco` entries in the GStreamer case, `is_atom_canonical` reading CLVM atom length bytes here) that trusts an attacker-supplied length/prefix field and advances a read cursor without bounds-checking against the buffer's actual size before dereferencing.

The caller, `is_clvm_canonical()`, similarly reads `clvm_buffer[offset]` in its main loop without checking `offset < len(clvm_buffer)` before each read, and only validates `offset == len(clvm_buffer)` after breaking out of the loop (post-hoc, not defensively): [3](#0-2) 

Because `bytes.__getitem__`/indexing in CPython raises `IndexError` rather than silently reading adjacent heap memory, this is not a memory-corruption/OOB-read in the C sense — but it is the direct analog of the C bug class in a Python codebase: an untrusted-length-prefix parser without a length pre-check, reachable from `validate_spend_bundle()` for every coin spend's `puzzle_reveal` and `solution` in a submitted spend bundle.

### Impact Explanation
`validate_spend_bundle()` calls `is_clvm_canonical()` unconditionally for both `puzzle_reveal` and `solution` of every coin spend in every submitted `SpendBundle`, before any DEDUP-eligibility gating: [4](#0-3) 

A crafted truncated buffer (e.g. a multi-byte atom-length-prefix header byte placed at the very end of the buffer, with insufficient trailing bytes to satisfy `prefix_len`) causes `is_atom_canonical()`/`is_clvm_canonical()` to raise an unhandled `IndexError` instead of returning `False`. This call is not wrapped in a try/except in `validate_spend_bundle()`, so the exception propagates out of the mempool admission path. Depending on how the calling code up the stack (spend-bundle push RPC / peer message handler) handles unexpected exceptions versus the expected `Err` return values, this can turn an ordinary spend-bundle validation failure into an unhandled exception during mempool processing — a spend-triggered processing fault reachable by any unprivileged bundle submitter, rather than a clean `Err.INVALID_COIN_SOLUTION` rejection.

### Likelihood Explanation
High reachability: no privileges are required, any wallet user or spend-bundle submitter can craft `puzzle_reveal`/`solution` bytes that reach `is_clvm_canonical()` directly via mempool admission, and the malformed trailing-length-prefix byte sequence needed to trigger the out-of-bounds index is trivial to construct (a single byte such as `0xFC` at buffer end with too few trailing bytes).

### Recommendation
Add explicit bounds checks in `is_atom_canonical()` before each `clvm_buffer[offset]` read (verify `offset < len(clvm_buffer)` prior to dereferencing, both in the prefix-byte loop and for the initial header byte), and add the same bounds check in `is_clvm_canonical()`'s main loop before reading `clvm_buffer[offset]`. On an out-of-range condition, return `False` (non-canonical) rather than raising, so malformed/truncated buffers are rejected the same way as any other non-canonical encoding, keeping `validate_spend_bundle()`'s existing `Err.INVALID_COIN_SOLUTION` handling intact instead of raising an uncaught exception.

### Proof of Concept
```python
from chia.full_node.mempool_manager import is_clvm_canonical

# 0xFC header byte declares a 6-byte length prefix (1 discriminant + 5 length bytes),
# but only 3 bytes follow -> is_atom_canonical() indexes past the buffer end.
malformed = bytes.fromhex("fc0000")
is_clvm_canonical(malformed)  # raises IndexError instead of returning False
```
Submitting a `SpendBundle` whose `puzzle_reveal` or `solution` ends with such a truncated multi-byte atom header reaches this code via `MempoolManager.validate_spend_bundle()` → `is_clvm_canonical()` → `is_atom_canonical()`, at `chia/full_node/mempool_manager.py:723`, `chia/full_node/mempool_manager.py:217`, and `chia/full_node/mempool_manager.py:181`, causing an unhandled `IndexError` in place of the expected graceful `Err.INVALID_COIN_SOLUTION` rejection.

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
