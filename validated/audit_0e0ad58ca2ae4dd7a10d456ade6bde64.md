## Title
Off-by-one fee-limit check lets an unprivileged spend bundle trigger an uncaught `AssertionError` in mempool admission - (File: `chia/full_node/mempool.py`)

### Summary
`MempoolManager.validate_spend_bundle()` rejects a spend bundle's fee only when it *exceeds* `MEMPOOL_ITEM_FEE_LIMIT` (`fees > MEMPOOL_ITEM_FEE_LIMIT`), but `Mempool.add_to_pool()` asserts the fee is *strictly less than* the same limit (`assert item.fee < MEMPOOL_ITEM_FEE_LIMIT`). A spend bundle with `fee == MEMPOOL_ITEM_FEE_LIMIT` (2^50 mojos) passes the admission check but fails the assertion, raising an unhandled `AssertionError` deep in transaction processing.

### Finding Description
The rejection check lives in `MempoolManager.validate_spend_bundle()`: [1](#0-0) 

```
if fees > MEMPOOL_ITEM_FEE_LIMIT or SQLITE_INT_MAX - self.mempool.total_mempool_fees() <= fees:
    return Err.INVALID_BLOCK_FEE_AMOUNT, None, []
```

This only rejects fees strictly *greater than* `MEMPOOL_ITEM_FEE_LIMIT`; a fee exactly equal to the limit is allowed through (assuming the mempool isn't already near `SQLITE_INT_MAX` in accumulated fees, which is trivially true for an otherwise empty/low-fee mempool).

The item then flows into `Mempool.add_to_pool()`, which enforces a stricter, off-by-one bound: [2](#0-1) 

```
def add_to_pool(self, item: MempoolItem) -> MempoolAddInfo:
    assert item.fee < MEMPOOL_ITEM_FEE_LIMIT
    assert item.conds is not None
    assert item.cost <= self.mempool_info.max_block_clvm_cost
    ...
```

`item.fee < MEMPOOL_ITEM_FEE_LIMIT` fails when `item.fee == MEMPOOL_ITEM_FEE_LIMIT`, so a bundle whose fee equals the limit exactly - which was explicitly allowed past `validate_spend_bundle()` - triggers an `AssertionError` instead of a clean `Err` return.

`add_to_pool()` is called from `MempoolManager.add_spend_bundle()`: [3](#0-2) 

which is in turn invoked directly from `FullNode.add_transaction()` inside the blockchain lock, with no `try/except AssertionError` around it: [4](#0-3) 

The two call paths that reach `add_transaction()`:
- The peer-facing `respond_transaction()` handler catches only `ValidationError`, not `AssertionError`: [5](#0-4) 
- The RPC `push_tx` endpoint calls `self.service.add_transaction(...)` directly with no assertion handling: [6](#0-5) 

Fee is simply `removal_amount - addition_amount` from ordinary coin spends: [7](#0-6) 

so any wallet controlling coins whose spent value minus created value equals exactly 2^50 mojos (≈1126 XCH) can construct this bundle without any special puzzle tricks (e.g. spend a coin worth `X` mojos and create a coin worth `X - 2^50` mojos).

### Impact Explanation
The `AssertionError` is raised inside the `blockchain.priority_mutex` critical section used for both block addition and mempool admission (low-priority side), while the lock is held: [8](#0-7) 
Because the exception is unhandled by the immediate caller, it propagates up through the peer message-handler task (for `respond_transaction`) or the RPC endpoint task (for `push_tx`), producing an uncaught exception at every point a node encounters this specific fee value while processing that spend bundle - a spend-triggered halt of that transaction's processing path, reproducible on every full node the transaction reaches (since it is a deterministic function of the crafted fee, not a race condition). This matches the "hang or frequently repeatable crash" bug-class of the reference CVE, mapped onto Chia's transaction-processing pipeline rather than a SQL optimizer.

### Likelihood Explanation
Reaching this requires no special privilege - any wallet holding, or any offer/transaction constructing a spend bundle whose net fee equals exactly `2**50` mojos, can trigger it through the standard `push_tx` RPC or normal transaction relay (`respond_transaction`). The condition is a precise, deterministic off-by-one and does not depend on race conditions, mempool fullness edge cases beyond an easily satisfied `SQLITE_INT_MAX` margin, or crafted CLVM.

### Recommendation
Make the two checks consistent: either change `validate_spend_bundle()`'s rejection to `fees >= MEMPOOL_ITEM_FEE_LIMIT`, or change `add_to_pool()`'s assertion to `assert item.fee <= MEMPOOL_ITEM_FEE_LIMIT` (and correspondingly ensure `SQLITE_INT_MAX` headroom checks stay consistent). Additionally, `add_to_pool()`'s invariants should not be enforced via bare `assert` on externally influenced values reachable from unauthenticated peer/RPC input; such conditions should return a structured `Err`/`MempoolAddInfo.error` instead of raising, and callers of `add_spend_bundle()`/`add_transaction()` should not rely on `AssertionError` never being raised by attacker-influenced data.

### Proof of Concept
1. Construct (or acquire, e.g., via a whale wallet or aggregated coins) a coin set with total value `V`.
2. Build a `SpendBundle` whose `coin_spends` remove coins totaling `V` and create coins (via `CREATE_COIN` conditions) totaling `V - 2**50` mojos, so `fee = removal_amount - addition_amount == 2**50 == MEMPOOL_ITEM_FEE_LIMIT` exactly.
3. Submit via `push_tx` RPC or broadcast as a `RespondTransaction` message to a full node with an otherwise low/empty mempool (so `SQLITE_INT_MAX - total_mempool_fees() > fees`).
4. `validate_spend_bundle()` computes `fees == MEMPOOL_ITEM_FEE_LIMIT`; the check `fees > MEMPOOL_ITEM_FEE_LIMIT` is `False`, so admission proceeds to `add_spend_bundle()` → `Mempool.add_to_pool(item)`.
5. `assert item.fee < MEMPOOL_ITEM_FEE_LIMIT` evaluates `2**50 < 2**50` → `False` → `AssertionError` raised, unhandled by `respond_transaction`/`push_tx`, propagating as an unhandled exception in that transaction's processing task.

Note: I was unable to execute this against a live node in this environment; the finding is based on static analysis of the two inconsistent bound checks and their call chain as shown above. Verifying the exact runtime behavior (e.g., whether an outer asyncio task-exception logger silently swallows it versus visibly failing the RPC call) would benefit from a live Devin session with test execution capability.

### Citations

**File:** chia/full_node/mempool_manager.py (L648-655)
```python
        if err is None:
            # No error, immediately add to mempool, after removing conflicting TXs.
            assert item is not None
            conflict = self.mempool.remove_from_pool(remove_items, MempoolRemoveReason.CONFLICT)
            info = self.mempool.add_to_pool(item)
            if info.error is not None:
                return SpendBundleAddInfo(item.cost, MempoolInclusionStatus.FAILED, [], info.error)
            return SpendBundleAddInfo(item.cost, MempoolInclusionStatus.SUCCESS, [*info.removals, conflict], None)
```

**File:** chia/full_node/mempool_manager.py (L812-814)
```python
            removal_amount += removal_record.coin.amount

        fees = uint64(removal_amount - addition_amount)
```

**File:** chia/full_node/mempool_manager.py (L822-827)
```python
        # this is not very likely to happen, but it's here to ensure SQLite
        # never runs out of precision in its computation of fees.
        # sqlite's integers are signed int64, so the max value they can
        # represent is 2^63-1
        if fees > MEMPOOL_ITEM_FEE_LIMIT or SQLITE_INT_MAX - self.mempool.total_mempool_fees() <= fees:
            return Err.INVALID_BLOCK_FEE_AMOUNT, None, []
```

**File:** chia/full_node/mempool.py (L403-410)
```python
    def add_to_pool(self, item: MempoolItem) -> MempoolAddInfo:
        """
        Adds an item to the mempool by kicking out transactions (if it doesn't fit), in order of increasing fee per cost
        """

        assert item.fee < MEMPOOL_ITEM_FEE_LIMIT
        assert item.conds is not None
        assert item.cost <= self.mempool_info.max_block_clvm_cost
```

**File:** chia/full_node/full_node.py (L3092-3102)
```python
        async with self.blockchain.priority_mutex.acquire(priority=BlockchainMutexPriority.low):
            if self.mempool_manager.get_spendbundle(spend_name) is not None:
                self.mempool_manager.remove_seen(spend_name)
                return MempoolInclusionStatus.SUCCESS, None
            if self.mempool_manager.peak is None:
                return MempoolInclusionStatus.FAILED, Err.MEMPOOL_NOT_INITIALIZED
            info = await self.mempool_manager.add_spend_bundle(
                transaction, cost_result, spend_name, self.mempool_manager.peak.height
            )
            status = info.status
            error = info.error
```

**File:** chia/_tests/core/mempool/test_mempool.py (L402-425)
```python
@metadata.request(peer_required=True, bytes_required=True)  # type: ignore[type-var]
async def respond_transaction(
    self: FullNodeAPI,
    tx: full_node_protocol.RespondTransaction,
    peer: WSChiaConnection,
    tx_bytes: bytes = b"",
    test: bool = False,
) -> tuple[MempoolInclusionStatus, Err | None]:
    """
    Receives a full transaction from peer.
    If tx is added to mempool, send tx_id to others. (new_transaction)
    """
    assert tx_bytes != b""
    spend_name = std_hash(tx_bytes)
    if spend_name in self.full_node.full_node_store.pending_tx_request:
        self.full_node.full_node_store.pending_tx_request.pop(spend_name)
    if spend_name in self.full_node.full_node_store.peers_with_tx:
        self.full_node.full_node_store.peers_with_tx.pop(spend_name)
    try:
        ret = await self.full_node.add_transaction(tx.transaction, spend_name, peer, test)
    except ValidationError as e:
        ret = (MempoolInclusionStatus.FAILED, e.code)
    invariant_check_mempool(self.full_node.mempool_manager.mempool)
    return ret
```

**File:** chia/full_node/full_node_rpc_api.py (L827-843)
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
```
