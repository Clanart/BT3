### Title
Malformed spend bundle with duplicate coin spends causes uncaught `AssertionError`/`KeyError` in `MempoolManager.validate_spend_bundle()`, halting transaction processing - (File: chia/full_node/mempool_manager.py)

### Summary
`MempoolManager.validate_spend_bundle()` assumes a 1:1 correspondence between `new_spend.coin_spends` (attacker-controlled, from the raw `SpendBundle`) and `spend_conditions`, a dict built by deduplicating `conds.spends` on `coin_id`. A spend bundle that spends the same coin twice breaks this invariant, causing an `assert` to fail or a `dict.pop()` to raise `KeyError` — both uncaught by the caller — which is analogous to the Nimbus attestation-parsing crash (CL-2025-01), where malformed-but-syntactically-valid consensus-layer input reached deep processing logic and triggered an unhandled exception that crashed node/task processing.

### Finding Description
In `chia/full_node/mempool_manager.py`, `validate_spend_bundle()` builds a lookup dict keyed by `coin_id`: [1](#0-0) 

```
# Map of coin ID to SpendConditions
spend_conditions = {bytes32(spend.coin_id): spend for spend in conds.spends}

# if this happens, the SpendBundle doesn't match the
# SpendBundleConditions.
assert len(new_spend.coin_spends) == len(spend_conditions)
``` [1](#0-0) 

Because `spend_conditions` is a dict comprehension keyed by `coin_id`, any duplicate `coin_id` in `conds.spends` collapses to a single dict entry. If a submitted `SpendBundle` contains two `CoinSpend`s for the same coin (e.g., the same `parent_coin_info`/`puzzle_hash`/`amount` triple appearing twice in `coin_spends`), `len(new_spend.coin_spends)` (2) will not equal `len(spend_conditions)` (1), so the `assert` on line 712 fails and raises an uncaught `AssertionError`. Even if that assert were bypassed or not always triggered, the subsequent loop: [2](#0-1) 

```
for coin_spend in new_spend.coin_spends:
    coin_id = coin_spend.coin.name()
    removal_names.add(coin_id)

    # if this coin_id isn't found, the SpendBundle doesn't match the
    # SpendBundleConditions.
    spend_conds = spend_conditions.pop(coin_id)
``` [2](#0-1) 

pops by `coin_id` on the second occurrence of a duplicate — since it was already removed on the first iteration — raising an uncaught `KeyError`.

This code path is reachable from the network- and RPC-facing transaction ingestion pipeline. `FullNodeAPI.respond_transaction()` enqueues an arbitrary attacker-supplied `SpendBundle` onto `self.full_node.transaction_queue` for asynchronous processing: [3](#0-2) 

Eventually `FullNode.add_transaction()` is invoked, which calls `pre_validate_spendbundle()` (wrapped only in `except ValueError` / `except ValidationError`) and then, outside of any exception guard, `self.mempool_manager.add_spend_bundle(...)`: [4](#0-3) 

`add_spend_bundle()` calls `validate_spend_bundle()` directly with no exception handling of its own: [5](#0-4) 

Neither `AssertionError` nor `KeyError` is caught anywhere in this call chain (`add_transaction` only catches `ValueError`/`ValidationError`), so the exception propagates out of whatever task is processing the transaction. I was unable to fully verify (due to running out of tool iterations) the exact exception-handling behavior of the coroutine/task that dequeues from `self.full_node.transaction_queue` and calls `add_transaction`, so I cannot confirm with certainty whether this crashes the entire node process, kills only the transaction-processing worker task (a persistent DoS on tx admission), or is silently swallowed at a higher level (e.g., a generic `except Exception` in the task runner). This is a genuine gap in my verification.

### Impact Explanation
If the transaction-processing task/loop lacks a catch-all exception handler, an unauthenticated peer or local RPC caller submitting a single specially-crafted spend bundle (containing a duplicate coin spend) can trigger an unhandled `AssertionError`/`KeyError` deep in mempool admission logic. Depending on how the enclosing task is supervised, this could crash the transaction-processing pathway repeatedly (each duplicate-coin bundle re-triggers it) or, if unguarded at a higher level, crash the full node process — a remote, spend-triggered transaction-processing halt, matching the report's DoS bug class.

### Likelihood Explanation
Constructing a `SpendBundle` with a duplicated `CoinSpend` (same coin spent twice) is straightforward and does not require any signature bypass, special CLVM tricks, or privileged access — any unprivileged submitter can build and broadcast/relay such a bundle via `respond_transaction`/`send_transaction` or local RPC `push_tx`. The main uncertainty is whether the Rust CLVM execution (`validate_clvm_and_signature` inside `pre_validate_spendbundle`) already rejects duplicate coin spends before this code is reached (e.g., as an implicit double-spend-within-bundle check) — this could not be confirmed with the tools available and would need to be verified in the `chia_rs` Rust implementation, which is outside the indexed Python codebase.

### Recommendation
Replace the brittle `assert`/`pop`-based matching in `validate_spend_bundle()` with explicit, exception-safe validation: detect duplicate coin IDs in `new_spend.coin_spends` early and return `Err.DOUBLE_SPEND` (or `Err.INVALID_SPEND_BUNDLE`) instead of relying on an `assert` for an invariant that untrusted input can violate. Additionally, wrap the `add_spend_bundle`/`validate_spend_bundle` call path in `full_node.add_transaction()` with a broad exception handler (translating unexpected errors to `Err.INVALID_SPEND_BUNDLE`, mirroring the existing `ValueError`/`ValidationError` handling) so that malformed input can never propagate an uncaught exception into the task/event loop.

### Proof of Concept
1. Craft a `CoinSpend` for a valid, spendable coin `C` with `puzzle_reveal` and `solution`.
2. Build a `SpendBundle` whose `coin_spends` list contains this same `CoinSpend` object twice: `coin_spends=[cs, cs]`, with an aggregated signature (or run in a test mode that skips signature checks).
3. Submit via `full_node_api.respond_transaction(RespondTransaction(spend_bundle), peer)` (or local RPC `push_tx`).
4. When `pre_validate_spendbundle` succeeds in producing `SpendBundleConditions` (`conds.spends` will contain the coin data, but as a Python dict keyed by `coin_id`, duplicate handling collapses entries), `MempoolManager.validate_spend_bundle()` is invoked via `add_spend_bundle()`.
5. Observe `assert len(new_spend.coin_spends) == len(spend_conditions)` at line 712 failing (2 != 1), or, if that line is bypassed, `spend_conditions.pop(coin_id)` at line 721 raising `KeyError` on the second occurrence — both uncaught in `add_transaction()`'s call chain. [6](#0-5)

### Citations

**File:** chia/full_node/mempool_manager.py (L640-647)
```python
        err, item, remove_items = await self.validate_spend_bundle(
            new_spend,
            conds,
            spend_name,
            first_added_height,
            get_coin_records,
            get_unspent_lineage_info_for_puzzle_hash,
        )
```

**File:** chia/full_node/mempool_manager.py (L707-721)
```python
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
```

**File:** chia/full_node/full_node_api.py (L373-410)
```python
    @metadata.request(peer_required=True, bytes_required=True)
    async def respond_transaction(
        self,
        tx: full_node_protocol.RespondTransaction,
        peer: WSChiaConnection,
        tx_bytes: bytes = b"",
        test: bool = False,
    ) -> Message | None:
        """
        Receives a full transaction from peer.
        If tx is added to mempool, send tx_id to others. (new_transaction)
        """
        assert tx_bytes != b""
        spend_name = std_hash(tx_bytes)
        if spend_name in self.full_node.full_node_store.pending_tx_request:
            self.full_node.full_node_store.pending_tx_request.pop(spend_name)
        elif peer.expected_mempool_responses > 0:
            peer.expected_mempool_responses -= 1
        else:
            self.log.info(
                f"Received unsolicited transaction {spend_name} from peer "
                f"{peer.peer_node_id} / {peer.peer_info.host} version {peer.version}"
            )
            await peer.close(RATE_LIMITER_BAN_SECONDS)
            return None
        peers_with_tx = {}
        if spend_name in self.full_node.full_node_store.peers_with_tx:
            peers_with_tx = self.full_node.full_node_store.peers_with_tx.pop(spend_name)

        # TODO: Use fee in priority calculation, to prioritize high fee TXs
        try:
            self.full_node.transaction_queue.put(
                TransactionQueueEntry(tx.transaction, spend_name, peer, test, peers_with_tx),
                peer.peer_node_id,
            )
        except TransactionQueueFull:
            pass  # we can't do anything here, the tx will be dropped. We might do something in the future.
        return None
```

**File:** chia/full_node/full_node.py (L3063-3100)
```python
        try:
            cost_result = await self.mempool_manager.pre_validate_spendbundle(
                transaction, spend_name, self._bls_cache, fee_per_cost=fee_per_cost
            )
        except ValueError as e:
            # ValueError is used to indicate a soft failure. We don't want to
            # ban the peer. Timeouts are logged by MempoolManager when
            # log_mempool is "timeout".
            self.log.info(f"Rejecting transaction {spend_name}: {e}")
            return MempoolInclusionStatus.FAILED, Err.INVALID_SPEND_BUNDLE
        except ValidationError as e:
            # Keep known-invalid bundles in seen-cache to prevent re-validation.
            self.mempool_manager.add_and_maybe_pop_seen(spend_name)
            self.log.info(f"Rejecting transaction {spend_name}: {e}")
            return MempoolInclusionStatus.FAILED, e.code
        finally:
            self.mempool_manager.remove_in_flight(spend_name)

        self.mempool_manager.add_and_maybe_pop_seen(spend_name)

        if log_mempool_mode == "true":
            try:
                mempool_dir = path_from_root(self.root_path, "mempool-log") / f"{self.blockchain.get_peak_height()}"
                mempool_dir.mkdir(parents=True, exist_ok=True)
                with open(mempool_dir / f"{spend_name}.bundle", "wb+") as f:
                    f.write(bytes(transaction))
            except Exception:
                self.log.exception(f"Failed to log mempool item: {spend_name}")

        async with self.blockchain.priority_mutex.acquire(priority=BlockchainMutexPriority.low):
            if self.mempool_manager.get_spendbundle(spend_name) is not None:
                self.mempool_manager.remove_seen(spend_name)
                return MempoolInclusionStatus.SUCCESS, None
            if self.mempool_manager.peak is None:
                return MempoolInclusionStatus.FAILED, Err.MEMPOOL_NOT_INITIALIZED
            info = await self.mempool_manager.add_spend_bundle(
                transaction, cost_result, spend_name, self.mempool_manager.peak.height
            )
```
