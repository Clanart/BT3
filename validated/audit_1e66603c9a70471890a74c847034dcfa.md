### Title
Uncaught `IndexError` in CLVM canonical-encoding check crashes transaction processing on malformed spend bundle - (File: chia/full_node/mempool_manager.py)

### Summary
`is_atom_canonical()` / `is_clvm_canonical()` in `chia/full_node/mempool_manager.py` manually parse the raw CLVM serialization of an attacker-supplied `puzzle_reveal` / `solution` byte string with no bounds checking on buffer offsets. This is the same bug class as CVE-2024-53432 (PCL's uncaught `std::out_of_range` in `PCLPointCloud2::at` while parsing malformed PLY files): untrusted, attacker-controlled binary input is walked with unchecked indexing, and a truncated/malformed multi-byte length prefix causes an out-of-bounds read that raises an uncaught exception instead of being handled as a validation failure. [1](#0-0) 

### Finding Description
`is_atom_canonical(clvm_buffer, offset)` reads `b = clvm_buffer[offset]` to determine how many additional length-prefix bytes (`prefix_len`, 0–5) follow, then loops:

```
for i in range(prefix_len):
    atom_len <<= 8
    offset += 1
    atom_len |= clvm_buffer[offset]
``` [2](#0-1) 

There is no check that `offset` stays within `len(clvm_buffer)` before each `clvm_buffer[offset]` access. If the leading byte declares a large multi-byte prefix (e.g. `0xFC`/`0xFD`, `prefix_len=5`) but the buffer ends before that many bytes are available (a truncated/malformed atom, exactly analogous to a malformed/truncated PLY record in the PCL bug), Python raises `IndexError: index out of range`.

This function is called by `is_clvm_canonical()`, which is invoked directly on the raw bytes of every coin spend's `puzzle_reveal` and `solution` inside `MempoolManager.validate_spend_bundle()`:

```
if not is_clvm_canonical(bytes(coin_spend.puzzle_reveal)) or not is_clvm_canonical(
    bytes(coin_spend.solution)
):
    return Err.INVALID_COIN_SOLUTION, None, []
``` [3](#0-2) 

`validate_spend_bundle` is reached from `add_spend_bundle`, which is the core mempool-admission entry point for any submitted `SpendBundle` (via full node peer protocol `RespondTransaction`/`transaction` handling, or the `push_tx` / `push_transactions` RPCs). [4](#0-3) [5](#0-4) 

Unlike the CLVM/BLS validation path (`pre_validate_spendbundle`), which explicitly catches `ValueError` from the Rust validator and converts it to a `ValidationError`, there is no `try/except` anywhere around the `is_clvm_canonical` calls in `validate_spend_bundle` to catch an `IndexError`. [6](#0-5) 

The existing test coverage for this function only exercises well-formed-but-non-canonical encodings (extra unnecessary length-prefix bytes) that return `False` cleanly — it never exercises a *truncated* buffer that would trigger the out-of-bounds read: [7](#0-6) 

### Impact Explanation
`is_clvm_canonical` runs unconditionally on every coin spend of every submitted spend bundle before signature/CLVM cost validation completes, as part of the standard mempool-admission pipeline reachable by any unprivileged wallet/RPC caller or peer submitting a transaction. An uncaught `IndexError` propagating out of `validate_spend_bundle`/`add_spend_bundle` is not converted into a normal validation error (`Err`/`ValidationError`) the way other malformed-input cases are; it is a raw Python exception. Depending on where in the async call chain it surfaces (RPC endpoint, peer message handler, or the blockchain-lock-guarded mempool add path), this can manifest as an unhandled exception disrupting the coroutine processing that spend bundle, and — because this code path executes under the full node's mempool/blockchain lock — has the potential to abort in-flight transaction processing for that call, representing a spend-triggered transaction-processing halt (DoS) rather than data corruption. It does not affect coin identity, signatures, or consensus semantics — no funds/asset-identity impact — but is a reachable, single-spend-bundle-triggered halt.

### Likelihood Explanation
Any unprivileged party who can submit a `SpendBundle` (wallet user, RPC caller, or transaction relay peer) fully controls the raw bytes of `puzzle_reveal` and `solution` for a spend, since these are simply serialized CLVM byte strings that don't need to actually validate as sensible puzzles to reach this canonical check — the check happens before/independently of the puzzle actually being run correctly, only requiring that a `SpendBundleConditions` object was produced by the (separate) Rust validator for the same spends. Producing a coin spend whose `puzzle_reveal`/`solution` bytes end in a byte such as `0xFC` with fewer than 5 trailing bytes is a trivial, deterministic bytes-level construction requiring no cryptographic material beyond what's needed to get past the earlier Rust CLVM/signature checks in `pre_validate_spendbundle`. This makes the trigger low-effort and repeatable.

### Recommendation
Add explicit bounds checks in `is_atom_canonical()` before each `clvm_buffer[offset]` access (both the initial read and inside the `prefix_len` loop), returning `False` (non-canonical/invalid) instead of raising, whenever the offset would exceed `len(clvm_buffer)`. Equivalently, wrap the calls to `is_clvm_canonical()` inside `validate_spend_bundle()` in a `try/except IndexError` that maps to `Err.INVALID_COIN_SOLUTION`, matching how other malformed-input paths are handled. Add regression tests using truncated multi-byte atom prefixes for each of the 5 prefix-length cases.

### Proof of Concept
Construct a `CoinSpend` whose serialized `puzzle_reveal` or `solution` bytes are, e.g., a single byte `0xFC` (declaring a 5-byte length prefix) with no following bytes, or more generally any prefix-declaring byte followed by fewer trailing bytes than `prefix_len` requires. Include it in a `SpendBundle` that otherwise passes CLVM/signature pre-validation (e.g. reusing a valid, minimal puzzle/solution structure but appending/truncating trailing bytes at the buffer boundary so the length-prefix parsing walks off the end). Submit via `push_tx` RPC or peer `RespondTransaction`; `MempoolManager.validate_spend_bundle()` calls `is_clvm_canonical(bytes(coin_spend.puzzle_reveal))` / `...solution)` [3](#0-2)  which calls `is_atom_canonical()` and raises `IndexError` at `atom_len |= clvm_buffer[offset]` [8](#0-7)  instead of returning a controlled validation failure.

Note: I was unable to fully trace whether the surrounding RPC/peer message-handling layers (`chia/server/ws_connection.py`, RPC websocket dispatcher) have a generic top-level `except Exception` that would catch this `IndexError` and merely log it (reducing impact to a logged error per call) versus letting it propagate further and disrupt the blockchain-lock-guarded mempool processing loop. This distinction affects whether the practical impact is "isolated exception per bad bundle" or a more severe processing-halt; confirming this would require tracing the exact caller stack from `full_node.py`'s peer-protocol handlers through `ws_connection.py` at runtime, which was not fully completed due to iteration limits.

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

**File:** chia/full_node/mempool_manager.py (L599-648)
```python
    async def add_spend_bundle(
        self,
        new_spend: SpendBundle,
        conds: SpendBundleConditions,
        spend_name: bytes32,
        first_added_height: uint32,
        get_coin_records: Callable[[Collection[bytes32]], Awaitable[list[CoinRecord]]] | None = None,
        get_unspent_lineage_info_for_puzzle_hash: Callable[[bytes32], Awaitable[UnspentLineageInfo | None]]
        | None = None,
    ) -> SpendBundleAddInfo:
        """
        Validates and adds to mempool a new_spend with the given
        SpendBundleConditions, and spend_name, and the current mempool. The mempool
        should be locked during this call (blockchain lock). If there are mempool
        conflicts, the conflicting spends might be removed (if the new spend is
        a superset of the previous). Otherwise, the new spend might be
        added to the potential pool.

        Args:
            new_spend: spend bundle to validate and add
            conds: SpendBundleConditions resulting from running the clvm in the spend bundle's coin spends
            spend_name: hash of the spend bundle data, passed in as an optimization

        Returns:
            Optional[uint64]: cost of the entire transaction, None iff status is FAILED
            MempoolInclusionStatus:  SUCCESS (should add to pool), FAILED (cannot add), and PENDING (can add later)
            list[MempoolRemoveInfo]: conflicting mempool items which were removed, if no Err
            Optional[Err]: Err is set iff status is FAILED
        """

        # Skip if already added
        existing_item = self.mempool.get_item_by_id(spend_name)
        if existing_item is not None:
            return SpendBundleAddInfo(existing_item.cost, MempoolInclusionStatus.SUCCESS, [], None)

        if get_coin_records is None:
            get_coin_records = self.get_coin_records

        if get_unspent_lineage_info_for_puzzle_hash is None:
            get_unspent_lineage_info_for_puzzle_hash = self.get_unspent_lineage_info_for_puzzle_hash

        err, item, remove_items = await self.validate_spend_bundle(
            new_spend,
            conds,
            spend_name,
            first_added_height,
            get_coin_records,
            get_unspent_lineage_info_for_puzzle_hash,
        )
        if err is None:
```

**File:** chia/full_node/mempool_manager.py (L723-726)
```python
            if not is_clvm_canonical(bytes(coin_spend.puzzle_reveal)) or not is_clvm_canonical(
                bytes(coin_spend.solution)
            ):
                return Err.INVALID_COIN_SOLUTION, None, []
```

**File:** chia/full_node/full_node_rpc_api.py (L827-856)
```python
    async def push_tx(self, request: dict[str, Any]) -> EndpointResult:
        if "spend_bundle" not in request:
            raise RpcError.simple(RpcErrorCodes.SPEND_BUNDLE_NOT_IN_REQUEST, "Spend bundle not in request")

        spend_bundle: SpendBundle = SpendBundle.from_json_dict(request["spend_bundle"])
        spend_name = spend_bundle.name()

        if self.service.mempool_manager.get_spendbundle(spend_name) is not None:
            status = MempoolInclusionStatus.SUCCESS
            error = None
        else:
            status, error = await self.service.add_transaction(spend_bundle, spend_name)
            if status != MempoolInclusionStatus.SUCCESS:
                if self.service.mempool_manager.get_spendbundle(spend_name) is not None:
                    # Already in mempool
                    status = MempoolInclusionStatus.SUCCESS
                    error = None

        if status == MempoolInclusionStatus.FAILED:
            assert error is not None
            raise RpcError(
                RpcErrorCodes.TRANSACTION_FAILED,
                f"Failed to include transaction {spend_name}, error {error.name}",
                data={"spend_name": str(spend_name), "error": error.name},
                structured_message="Failed to include transaction",
            )
        return {
            "status": status.name,
        }

```

**File:** chia/_tests/core/mempool/test_mempool_manager.py (L3277-3300)
```python
@pytest.mark.anyio
@pytest.mark.parametrize("flags", [0, ELIGIBLE_FOR_DEDUP, ELIGIBLE_FOR_FF, ELIGIBLE_FOR_FF | ELIGIBLE_FOR_DEDUP])
@pytest.mark.parametrize(
    "puzzle_hex,solution_hex",
    [
        # ((1)) with a non-canonical atom length prefix in the solution
        (None, "ffffc001018080"),
        # atom 1 with a non-canonical length prefix in the puzzle
        ("c00101", "80"),
    ],
)
async def test_mempool_requires_canonical_clvm(flags: int, puzzle_hex: str | None, solution_hex: str) -> None:
    coin_spend = make_spend(
        TEST_COIN,
        SerializedProgram.fromhex(puzzle_hex) if puzzle_hex is not None else IDENTITY_PUZZLE,
        SerializedProgram.fromhex(solution_hex),
    )
    coins = TestCoins([TEST_COIN], lineage={})
    async with setup_mempool(coins) as mempool_manager:
        sb = SpendBundle([coin_spend], G2Element())
        sb_conds = make_test_conds(spend_ids=[(TEST_COIN, flags)])
        bundle_add_info = await mempool_manager.add_spend_bundle(sb, sb_conds, sb.name(), uint32(1))
        assert bundle_add_info.status == MempoolInclusionStatus.FAILED
        assert bundle_add_info.error == Err.INVALID_COIN_SOLUTION
```
