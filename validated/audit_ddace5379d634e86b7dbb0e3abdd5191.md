### Title
Out-of-bounds/uncaught-exception in `is_atom_canonical` when checking CLVM canonicity of an attacker-supplied `puzzle_reveal`/`solution` - (File: chia/full_node/mempool_manager.py)

### Summary
`is_atom_canonical()` in `chia/full_node/mempool_manager.py` parses a CLVM atom length-prefix directly out of an attacker-controlled byte buffer (a spend bundle's `puzzle_reveal` or `solution`), incrementing `offset` up to 5 times and indexing `clvm_buffer[offset]` at each step, without ever checking that `offset` stays within `len(clvm_buffer)`. This mirrors the NimBLE CVE-2026-45812 bug class: a length/offset value taken from untrusted input is used to step through a buffer without verifying the buffer actually contains enough bytes to satisfy that length, leading to an out-of-bounds read.

### Finding Description
`is_atom_canonical` decodes CLVM's variable-length atom size prefix: [1](#0-0) 

For prefix bytes indicating a longer length encoding (e.g. `0xFC`, prefix_len=5), the function loops `for i in range(prefix_len): ... offset += 1; atom_len |= clvm_buffer[offset]` with **no bounds check** on `offset` against `len(clvm_buffer)`. If the buffer is truncated right after such a prefix byte (e.g. a `puzzle_reveal`/`solution` ending in `\xfc` with fewer than 5 trailing bytes), this raises an uncaught `IndexError`.

`is_clvm_canonical`, which calls `is_atom_canonical`, is invoked directly and unconditionally on attacker-controlled data during mempool admission of every new spend bundle: [2](#0-1) 

Specifically:
```
if not is_clvm_canonical(bytes(coin_spend.puzzle_reveal)) or not is_clvm_canonical(bytes(coin_spend.solution)):
    return Err.INVALID_COIN_SOLUTION, None, []
```
This call site has no `try/except` around it. I was not able to fully verify (search budget exhausted) whether callers further up the stack (`add_spend_bundle` / `pre_validate_spendbundle` in `chia/full_node/mempool_manager.py`, and the full-node RPC/protocol handler that invokes them) wrap this call in a broad `try/except Exception`. If they do not, a crafted `puzzle_reveal` or `solution` that is truncated exactly at a multi-byte length prefix would raise an unhandled `IndexError` while a single unprivileged spend bundle is being validated for mempool admission — this is directly analogous to NimBLE's OOB read while parsing an attacker/peer-supplied buffer with a bad length/offset calculation.

Unlike the bounds-checked parsers elsewhere in this codebase (e.g. `chia/full_node/full_block_utils.py`'s `skip_bytes`/`skip_list`, which explicitly raise `ValueError` when a declared length exceeds the remaining buffer — see `skip_bytes` at lines 27-34), `is_atom_canonical` has no equivalent guard.

### Impact Explanation
If the exception is not caught by an ancestor handler, a single malicious spend bundle containing a `puzzle_reveal` or `solution` ending mid-length-prefix could raise an unhandled exception during `validate_spend_bundle`, called from the mempool admission path for every submitted transaction. Because this runs in the full node's mempool/threadpool validation path invoked for spend bundles from arbitrary wallet users/RPC callers, this could constitute a spend-triggered halt of transaction processing (denial of service) for the node handling admission, which is one of the accepted impact categories (spend-triggered transaction-processing halt). Whether it actually crashes the full node process or is silently swallowed by an outer handler (turning it into, at most, a rejection with a wrong error) could not be conclusively determined within the available search.

### Likelihood Explanation
Likelihood of triggering the code path is high: `is_clvm_canonical` is called unconditionally on every coin spend's `puzzle_reveal` and `solution` bytes during standard spend-bundle validation — no special privilege or peer role is needed, an ordinary spend-bundle submitter can craft the byte sequence. However, whether this actually causes unhandled crash/DoS impact depends on unverified exception-handling in the calling stack (`add_spend_bundle`, RPC layer), which reduces confidence that this reaches "concrete, high-likelihood" impact as strictly as required by the validation rules.

### Recommendation
Add explicit bounds checks in `is_atom_canonical` before each `clvm_buffer[offset]` access (mirroring the pattern already used in `chia/full_node/full_block_utils.py`'s `skip_bytes`/`skip_list`, which raise a `ValueError` when the declared length exceeds the remaining buffer), and ensure `is_clvm_canonical`/`is_atom_canonical` raise a well-defined, caught exception (or return "non-canonical") rather than allowing an `IndexError` to propagate from attacker-controlled input.

### Proof of Concept
Construct a `SpendBundle` where a `CoinSpend`'s `solution` (or `puzzle_reveal`) SerializedProgram bytes end with an atom whose length-prefix byte requires additional length bytes that are not present, e.g. a buffer ending in `\xfc` (indicating a 5-byte length suffix per `is_atom_canonical`'s `0b11111110 == 0b11111100` branch) with zero trailing bytes. Submitting this spend bundle to a node's mempool triggers `validate_spend_bundle` → `is_clvm_canonical` → `is_atom_canonical`, which will attempt `clvm_buffer[offset]` beyond the buffer's length and raise `IndexError`. I could not fully confirm within available searches whether this exception propagates unhandled to crash mempool processing or is caught upstream — this should be verified by tracing `add_spend_bundle`/`pre_validate_spendbundle` and the RPC/protocol message handler that calls them for a broad exception handler.

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
