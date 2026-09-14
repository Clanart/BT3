Found a solid analog. Here's the finding:

### Title
Full Node event loop can be blocked for extended periods by mempool block-building triggered from an unprivileged spend bundle, causing a spend-triggered transaction-processing halt - ([File: chia/full_node/full_node_api.py])

### Summary
The Samba `rpcecho` bug lets an unprivileged RPC caller invoke `dcesrv_echo_TestSleep()`, which blocks the single worker thread that also services all other RPC/DCE operations, causing a full denial of service. Chia's full node has an analogous single-threaded chokepoint: `Mempool.create_block_generator2()` is a synchronous (non-`async`, non-offloaded) function invoked directly on the node's asyncio event loop from `FullNodeAPI.declare_proof_of_space()`. This function re-runs full CLVM validation synchronously for singleton fast-forward (FF) spends via `process_fast_forward_spends()` → `get_conditions_from_spendbundle()`. Because this call is not dispatched to `MempoolManager.pool` (unlike normal admission validation and unlike `request_block_header`'s `additions_and_removals` call), it fully occupies the single asyncio event loop thread for its duration, blocking all other node responsibilities (RPC, peer messages, timelord/VDF callbacks) while it runs.

### Finding Description
`declare_proof_of_space()` selects and calls the block-generator builder directly, without `await`ing an executor-dispatched coroutine: [1](#0-0) 

`create_block_generator2` is a plain synchronous method on `Mempool`: [2](#0-1) 

Within block building, for every mempool item that supports singleton fast-forward, `SingletonFastForward.process_fast_forward_spends()` re-runs the coin spend's full CLVM validation synchronously via `get_conditions_from_spendbundle()` to recompute cost after rebasing the singleton lineage: [3](#0-2) 

The per-batch timeout check in `create_bundle_from_mempool_items`/`create_block_generator2` only fires *between* items, not while a single item's FF re-validation is executing: [4](#0-3) 

Contrast this with the normal admission path, which explicitly offloads CLVM execution to a thread pool to avoid blocking the event loop: [5](#0-4) 

...and with `request_block_header`, which explicitly notes the need to keep expensive CLVM work off the event loop by using `pool.run_in_loop`: [6](#0-5) 

A single spend bundle admitted to the mempool is bounded only by `max_tx_clvm_cost` (half of `MAX_BLOCK_COST_CLVM`, i.e. ~5.5 billion cost units): [7](#0-6) 

An unprivileged submitter can craft a spend bundle containing a singleton-fast-forward-eligible spend (odd amount, no disqualifying conditions per `can_fast_forward_singleton`) whose CLVM execution cost approaches `max_tx_clvm_cost`, and pay enough fee to avoid mempool eviction. As long as this item remains in the mempool and is not the latest singleton version, every call to `declare_proof_of_space()` — which happens on the node's *single* asyncio event loop thread once per signage point, i.e. multiple times per ~9.3s sub-slot during normal farming — re-executes that near-maximum-cost CLVM validation synchronously and unbounded by the wall-clock timeout used elsewhere in the same function.

### Impact Explanation
Because the full node's asyncio event loop is single-threaded, blocking it stalls concurrently-scheduled coroutines: RPC endpoint handling (`chia/rpc/rpc_server.py`), peer message dispatch (`WSChiaConnection.incoming_message_handler`), and farmer/timelord protocol callbacks all share this loop. Repeated multi-second synchronous CLVM re-executions triggered on every signage point produce a recurring transaction-processing and consensus-participation halt for the duration the crafted item sits in the mempool — directly matching the "spend-triggered transaction-processing halt" impact category and the rpcecho bug class (single-worker blocking call disrupting all co-located services).

### Likelihood Explanation
The path is reachable by any unprivileged spend-bundle submitter: craft and broadcast one spend bundle with a costly, fast-forward-eligible singleton spend and a fee sufficient to remain in the mempool (fee-per-cost above `nonzero_fee_minimum_fpc`), then wait for a sibling transaction that advances the singleton so the item is no longer "latest," forcing the `perform_the_fast_forward` + re-validation branch on every `declare_proof_of_space()` call. No node-operator, farmer, or peer privilege is required.

### Recommendation
Offload `Mempool.create_block_generator2()` (and its FF re-validation via `get_conditions_from_spendbundle`) to `MempoolManager.pool.run_in_loop()`, consistent with how `pre_validate_spendbundle` and `request_block_header` already avoid running CLVM execution directly on the event loop. Additionally, enforce the existing wall-clock `timeout` check around each item's FF re-validation itself (not just between items), and consider capping the number/cost of FF re-validations performed per `create_block_generator2()` invocation.

### Proof of Concept
1. Create a singleton coin with an odd amount and no AGG_SIG/relative-timelock conditions that disqualify fast-forward eligibility (see `can_fast_forward_singleton`).
2. Submit spend bundle A: spends the singleton with an inner puzzle whose conditions maximize CLVM cost up to just under `max_tx_clvm_cost`, paying a fee sufficient to avoid low-fee eviction.
3. Submit spend bundle B: an independent transaction that also spends/advances the same singleton (e.g., via a competing FF spend), causing bundle A's `latest_singleton_lineage` to become stale relative to the chain state, forcing the `perform_the_fast_forward` branch on every subsequent block-generator build.
4. Observe that every `declare_proof_of_space()` call for subsequent signage points invokes `create_block_generator2()` → `process_fast_forward_spends()` → `get_conditions_from_spendbundle()` synchronously on the event loop, each taking wall-clock time proportional to the near-max CLVM cost, stalling all concurrently scheduled RPC/peer/timelord coroutines for that duration.

### Citations

**File:** chia/full_node/full_node_api.py (L1104-1115)
```python
                        block_version = self.full_node.config.get("block_creation", 1)
                        block_timeout = self.full_node.config.get("block_creation_timeout", 2.0)
                        if block_version == 0:
                            create_block = self.full_node.mempool_manager.create_block_generator
                        elif block_version == 1:
                            create_block = self.full_node.mempool_manager.create_block_generator2
                        else:
                            self.log.warning(f"Unknown 'block_creation' config: {block_version}")
                            create_block = self.full_node.mempool_manager.create_block_generator

                        assert tx_peak is not None
                        new_block_gen = create_block(tx_peak.header_hash, block_timeout)
```

**File:** chia/full_node/full_node_api.py (L1436-1445)
```python
            assert generator_bytes is not None
            additions, removals = await self.full_node.pool.run_in_loop(
                additions_and_removals,
                generator_bytes,
                block_generator.generator_refs,
                flags,
                self.full_node.constants,
                nice=(5,) if trusted else (15,),
                dedicated=trusted,
            )
```

**File:** chia/full_node/mempool_manager.py (L374-378)
```python

        self.max_block_clvm_cost = uint64(self.constants.MAX_BLOCK_COST_CLVM - BLOCK_OVERHEAD)
        self.max_tx_clvm_cost = (
            max_tx_clvm_cost if max_tx_clvm_cost is not None else uint64(self.constants.MAX_BLOCK_COST_CLVM // 2)
        )
```

**File:** chia/full_node/mempool_manager.py (L460-466)
```python
    def create_block_generator2(self, last_tb_header_hash: bytes32, timeout: float) -> NewBlockGenerator | None:
        """
        Returns a block generator program, the aggregate signature and all additions, for a new block
        """
        if self.peak is None or self.peak.header_hash != last_tb_header_hash:
            return None
        return self.mempool.create_block_generator2(self.constants, self.peak.height, timeout)
```

**File:** chia/full_node/mempool_manager.py (L550-559)
```python
            flags = get_flags_for_height_and_constants(self.peak.height, self.constants)
            sbc: SpendBundleConditions
            sbc, new_cache_entries, duration = await self.pool.run_in_loop(
                validate_clvm_and_signature,
                spend_bundle,
                self.max_tx_clvm_cost,
                self.constants,
                flags | MEMPOOL_MODE,
                nice=(5, -fee_per_cost),
            )
```

**File:** chia/full_node/eligible_coin_spends.py (L343-368)
```python
        # Re-run the new spend bundle to make sure it remains valid, and to
        # check that its cost didn't change. Fast forwarding rewrites the
        # lineage proof's parent amount to the singleton's own amount, which can
        # have a different serialized length than the amount that was in the
        # original spend (this happens when the singleton's amount changed in an
        # earlier generation), and that changes the CLVM cost. We account for
        # this item using the cost we computed at admission, so rather than
        # tracking a changed cost through block building we simply reject the
        # fast forward when the cost changed. Such a spend won't be included; a
        # fresh spend against the current singleton can be submitted instead.
        new_sb = SpendBundle(coin_spends=new_coin_spends, aggregated_signature=mempool_item.aggregated_signature)
        try:
            new_conds = get_conditions_from_spendbundle(
                new_sb, uint64(constants.MAX_BLOCK_COST_CLVM), constants, prev_tx_height
            )
        # get_conditions_from_spendbundle raises a ValueError with an error code
        except ValueError as e:
            # Convert that to a ValidationError
            if len(e.args) > 1:
                error = Err(e.args[1])
                raise ValueError(f"Mempool item became invalid after singleton fast forward with error {error}.")
            else:
                raise ValueError(
                    "Mempool item became invalid after singleton fast forward with an unspecified error."
                )  # pragma: no cover
        if new_conds.cost != mempool_item.conds.cost:
```

**File:** chia/full_node/mempool.py (L616-657)
```python
        log.info(f"Starting to make block, max cost: {self.mempool_info.max_block_clvm_cost}")
        bundle_creation_start = monotonic()
        cursor = self._db_conn.execute("SELECT name, fee FROM tx ORDER BY priority DESC, seq ASC")
        skipped_items = 0
        for row in cursor:
            name = bytes32(row[0])
            fee = int(row[1])
            item = self._items[name]

            current_time = monotonic()
            if current_time - bundle_creation_start >= timeout:
                log.info(f"exiting early, already spent {current_time - bundle_creation_start:0.2f} s")
                break
            try:
                assert item.conds is not None
                cost = item.conds.cost
                if skipped_items >= PRIORITY_TX_THRESHOLD:
                    # If we've encountered `PRIORITY_TX_THRESHOLD` number of
                    # transactions that don't fit in the remaining block size,
                    # we want to keep looking for smaller transactions that
                    # might fit, but we also want to avoid spending too much
                    # time on potentially expensive ones, hence this shortcut.
                    if any(
                        sd.eligible_for_dedup or sd.supports_fast_forward for sd in item.bundle_coin_spends.values()
                    ):
                        log.info(f"Skipping transaction with dedup or FF spends {name}")
                        continue

                    unique_coin_spends = []
                    unique_additions = []
                    for spend_data in item.bundle_coin_spends.values():
                        unique_coin_spends.append(spend_data.coin_spend)
                        unique_additions.extend(spend_data.additions)
                    ff_state_update: dict[bytes32, UnspentLineageInfo] = {}
                    dedup_state_update: dict[bytes32, DedupCoinSpend] = {}
                    cost_saving = 0
                    atoms_saving = 0
                    pairs_saving = 0
                else:
                    bundle_coin_spends, ff_state_update = singleton_ff.process_fast_forward_spends(
                        mempool_item=item, prev_tx_height=prev_tx_height, constants=constants
                    )
```
