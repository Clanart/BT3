### Title
DataLayerWallet.singleton_removed silently swallows singleton-lineage errors, permanently halting DL singleton spend tracking - (File: chia/data_layer/data_layer_wallet.py)

### Summary
`DataLayerWallet.singleton_removed` processes the spend of a DataLayer (DL) singleton coin to advance the wallet's local `SingletonRecord`/lineage chain, analogous to how the Canto keeper's `processEvents` advances CSR registration state from Turnstile `Register`/`Assign` events. When `singleton_removed` hits an error condition partway through processing a singleton spend, it just logs a warning and returns (`self.log.warning(...); return`) instead of raising or otherwise surfacing the failure to the caller/sync pipeline. Downstream, the wallet keeps building future singleton spends from the last known (now stale) `SingletonRecord`, which will be rejected on-chain, permanently halting the wallet's ability to spend/update that DL singleton — the same class of "silently drop, then future actions are rejected forever" halt as the original finding.

### Finding Description
`singleton_removed` is called (from `wallet_state_manager._add_coin_state`) whenever the wallet sees a spend of a coin belonging to a tracked DL singleton, to compute and persist the new `SingletonRecord` for the singleton's next generation: [1](#0-0) 

Inside this function there are multiple early-return branches on error/edge conditions that only log and return, with no exception raised and no error propagated to the sync caller:
- Missing parent singleton record: `self.log.warning(...); return`. [2](#0-1) 
- Failure to parse the hinted root/inner puzzle hash from the `CREATE_COIN` condition (`IndexError`): logs and returns without advancing the record. [3](#0-2) 

This is called from the wallet sync path `WalletStateManager._add_coin_state`, which does not check a return value or treat these silent failures specially — sync simply proceeds as if nothing happened: [4](#0-3) 

Because the new `SingletonRecord` (with updated `lineage_proof`, `root`, `inner_puzzle_hash`, `generation`) is never persisted when one of these silent paths is hit, subsequent calls to build the next DL update spend (`create_update_state_spend`, via `get_spendable_singleton_info`) will use the last confirmed (now outdated) `SingletonRecord`/lineage proof: [5](#0-4) 

Since the actual on-chain singleton has already advanced to a new coin/lineage that the wallet failed to record, any spend built from the stale lineage proof will reference a parent coin that no longer exists in that lineage relationship, and will be rejected by the singleton puzzle / node when broadcast. There is no retry or resync path that repairs this: the local `dl_store` state is permanently out of sync with the on-chain singleton once this silent-return path is taken.

This mirrors the reported Canto bug precisely: `processEvents` hits an error mid-loop, logs it, and returns without propagating the failure, while `PostTxProcessing` (its caller) continues normally — leaving on-chain state (Turnstile "registered" flag) and off-chain tracking state (CSR store) permanently diverged, with no way to recover since re-registration is blocked by `onlyUnregistered`. Here, the on-chain DL singleton state and the wallet's local `dl_store` lineage state diverge the same way, with no recovery path other than manually re-tracking the launcher from scratch (`track_new_launcher_id`), which itself is not automatically triggered by this failure.

### Impact Explanation
Once the wallet's `SingletonRecord` fails to advance due to a silently swallowed error (e.g., a DL singleton spend whose hint layout doesn't match the expected 3-argument hint list, triggering the `IndexError` branch, or a coin observed before its parent's `SingletonRecord` was persisted), the wallet becomes permanently unable to correctly build further update/melt spends for that DataLayer singleton. This is a spend-triggered transaction-processing halt: a single malformed-looking (from the wallet's parsing perspective) but validly-accepted on-chain spend permanently desynchronizes wallet state from consensus state for that store, blocking all future legitimate updates to that Data Layer singleton without any explicit error surfaced to the operator (only a log line).

### Likelihood Explanation
This path is reachable purely through normal Data Layer usage without any consensus-level malicious behavior — any spend of a tracked DL singleton whose `CREATE_COIN` hint list happens to have fewer than 3 elements, or any coin state processed for a singleton whose parent `SingletonRecord` hasn't yet been persisted (e.g., due to reorg/rollback timing or out-of-order coin-state delivery), triggers the silent-return path. Because these are edge/ordering conditions rather than attacker-controlled malicious inputs, likelihood is moderate, but the resulting state divergence is silent (log-only) and has no automatic recovery, making the eventual permanent-halt impact significant once triggered.

### Recommendation
`singleton_removed`'s error branches should not silently return leaving stale state. At minimum:
- Raise/propagate the error (or a well-defined recoverable exception) instead of only logging, so the wallet sync layer can retry, mark the wallet/store as unsynced, and surface this to the operator rather than silently continuing.
- Add a reconciliation mechanism so that if the local `SingletonRecord` chain is ever found to be behind the observed on-chain spends, the wallet automatically re-derives/resyncs the lineage (similar to `_track_new_launcher_id`'s resync loop) instead of relying on the happy-path update-only flow.

### Proof of Concept
1. Track a DL singleton (`track_new_launcher_id`) so `dl_store` has a `SingletonRecord`.
2. Have the singleton spent on-chain such that the `CREATE_COIN` condition hint list is malformed/short relative to the code's expectation (`condition[3][1]`, `condition[3][2]`), triggering `IndexError` inside `singleton_removed`. [6](#0-5) 
3. `singleton_removed` logs a warning and returns; no new `SingletonRecord` is added to `dl_store`. `_add_coin_state` continues processing normally with no error propagated. [4](#0-3) 
4. Any later call to `create_update_state_spend` for that `launcher_id` fetches the stale `SingletonRecord`/lineage proof via `get_spendable_singleton_info`, builds a spend against a coin whose lineage no longer matches the actual on-chain state, and that spend is rejected on-chain — permanently blocking further updates to the store from this wallet until manual intervention. [5](#0-4)

### Citations

**File:** chia/data_layer/data_layer_wallet.py (L384-424)
```python
    async def create_update_state_spend(
        self,
        launcher_id: bytes32,
        root_hash: bytes32 | None,
        action_scope: WalletActionScope,
        new_puz_hash: bytes32 | None = None,
        new_amount: uint64 | None = None,
        fee: uint64 = uint64(0),
        announce_new_state: bool = False,
        extra_conditions: tuple[Condition, ...] = tuple(),
    ) -> None:
        singleton_record, parent_lineage = await self.get_spendable_singleton_info(launcher_id)

        if root_hash is None:
            root_hash = singleton_record.root

        inner_puzzle_derivation: (
            DerivationRecord | None
        ) = await self.wallet_state_manager.puzzle_store.get_derivation_record_for_puzzle_hash(
            singleton_record.inner_puzzle_hash
        )
        if inner_puzzle_derivation is None:
            raise ValueError(f"DL Wallet does not have permission to update Singleton with launcher ID {launcher_id}")

        # Make the child's puzzles
        if new_puz_hash is None:
            new_puz_hash = await action_scope.get_puzzle_hash(self.wallet_state_manager)
        assert new_puz_hash is not None
        next_full_puz_hash: bytes32 = create_host_fullpuz(new_puz_hash, root_hash, launcher_id).get_tree_hash_precalc(
            new_puz_hash
        )

        # Construct the current puzzles
        current_inner_puzzle: Program = self.standard_wallet.puzzle_for_pk(inner_puzzle_derivation.pubkey)
        current_full_puz = create_host_fullpuz(
            current_inner_puzzle,
            singleton_record.root,
            launcher_id,
        )
        assert singleton_record.lineage_proof.parent_name is not None
        assert singleton_record.lineage_proof.amount is not None
```

**File:** chia/data_layer/data_layer_wallet.py (L802-845)
```python
    async def singleton_removed(self, parent_spend: CoinSpend, height: uint32) -> None:
        parent_name = parent_spend.coin.name()
        puzzle = parent_spend.puzzle_reveal
        solution = parent_spend.solution

        matched, _ = match_dl_singleton(puzzle)
        if matched:
            self.log.info(f"DL singleton removed: {parent_spend.coin}")
            singleton_record: SingletonRecord | None = await self.wallet_state_manager.dl_store.get_singleton_record(
                parent_name
            )
            if singleton_record is None:
                self.log.warning(f"DL wallet received coin it does not have parent for. Expected parent {parent_name}.")
                return

            # Information we need to create the singleton record
            full_puzzle_hash: bytes32
            amount: uint64
            root: bytes32
            inner_puzzle_hash: bytes32

            conditions = run_with_cost(puzzle, self.wallet_state_manager.constants.MAX_BLOCK_COST_CLVM, solution)[
                1
            ].as_python()
            found_singleton: bool = False
            for condition in conditions:
                if condition[0] == ConditionOpcode.CREATE_COIN and int.from_bytes(condition[2], "big") % 2 == 1:
                    full_puzzle_hash = bytes32(condition[1])
                    amount = uint64(int.from_bytes(condition[2], "big"))
                    try:
                        root = bytes32(condition[3][1])
                        inner_puzzle_hash = bytes32(condition[3][2])
                    except IndexError:
                        self.log.warning(
                            f"Parent {parent_name} with launcher {singleton_record.launcher_id} "
                            "did not hint its child properly"
                        )
                        return
                    found_singleton = True
                    break

            if not found_singleton:
                self.log.warning(f"Singleton with launcher ID {singleton_record.launcher_id} was melted")
                return
```

**File:** chia/wallet/wallet_state_manager.py (L1437-1444)
```python
            if record.wallet_type == WalletType.DATA_LAYER:
                singleton_spend = await fetch_coin_spend_for_coin_state(coin_state, peer)
                dl_wallet = self.get_wallet(id=uint32(record.wallet_id), required_type=DataLayerWallet)
                await dl_wallet.singleton_removed(
                    singleton_spend,
                    uint32(coin_state.spent_height),
                )

```
