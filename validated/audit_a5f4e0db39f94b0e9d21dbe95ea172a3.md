### Title
`ChunkExtra.balance_burnt` Inherited by Both Child Shards During Shard Split, Causing Double-Counted Balance Burn in Total Supply — (`chain/chain/src/resharding/manager.rs`)

### Summary

During a shard split in `process_memtrie_resharding_storage_update`, both child `ChunkExtra` records are created by cloning the parent's `ChunkExtra` and updating only `state_root` and `congestion_info`. The `balance_burnt` field — representing the balance burned in the parent shard's chunk at the resharding block — is inherited unchanged by **both** children. In the first block of the new epoch, `verify_total_supply_checked` sums `prev_balance_burnt` from every new chunk. With two child shards each reporting the parent's full `balance_burnt`, the total is `2 × parent_balance_burnt`, but only `1 × parent_balance_burnt` was actually burned. The total supply is permanently deflated by `parent_balance_burnt` at every resharding event.

### Finding Description

In `process_memtrie_resharding_storage_update`:

```rust
let mut child_chunk_extra = ChunkExtra::clone(&parent_chunk_extra);
*child_chunk_extra.state_root_mut() = trie_changes.new_root;
*child_chunk_extra.congestion_info_mut() = child_congestion_info;
// balance_burnt, gas_used, gas_limit, outcome_root, validator_proposals
// are all silently inherited from the parent
``` [1](#0-0) 

This `child_chunk_extra` is then saved for both the left and right child shards at the resharding block hash: [2](#0-1) 

The `balance_burnt` field in `ChunkExtra` is set during normal chunk application to `total_balance_burnt` — the tokens burned executing that specific chunk: [3](#0-2) 

Chunk header validation enforces that `chunk_header.prev_balance_burnt()` must equal `prev_chunk_extra.balance_burnt()`: [4](#0-3) 

Total supply verification sums `prev_balance_burnt` across all new chunks in the block: [5](#0-4) 

After resharding, the first block of epoch N+1 contains two new chunks (one per child shard). Each child chunk's `prev_balance_burnt` equals the parent's `balance_burnt` (call it `X`). The verification computes `X + X = 2X`, but the resharding block only burned `X` (from the single parent shard). The block producer uses the same summation logic, so both producer and validators agree on the wrong value — the block is accepted with a permanently deflated total supply.

### Impact Explanation

The `total_supply` field in the block header is permanently reduced by `X` (the parent shard's `balance_burnt` at the resharding block) at every shard split. Since `epoch_total_reward` is proportional to `total_supply`, validator rewards are also permanently reduced after each resharding. The error compounds across multiple resharding events. Because both block producers and validators apply the same incorrect summation, no consensus failure occurs — the incorrect value is silently accepted by all nodes.

### Likelihood Explanation

Any shard split where the resharding block processes at least one transaction (i.e., `balance_burnt > 0`) triggers the bug. In production, the resharding block is the last block of an epoch and processes normal transactions, so `balance_burnt` is virtually always non-zero. The bug fires deterministically on every resharding event.

### Recommendation

Reset `balance_burnt` (and `gas_used`, which has the same inheritance problem for gas-price computation) to zero when constructing child `ChunkExtra` records during resharding, since the child shards have no chunk execution in the resharding block:

```rust
let mut child_chunk_extra = ChunkExtra::clone(&parent_chunk_extra);
*child_chunk_extra.state_root_mut() = trie_changes.new_root;
*child_chunk_extra.congestion_info_mut() = child_congestion_info;
*child_chunk_extra.balance_burnt_mut() = Balance::ZERO;
*child_chunk_extra.gas_used_mut() = Gas::ZERO;
```

### Proof of Concept

1. Run a network with resharding enabled (`ProtocolFeature::DynamicResharding`).
2. Submit transactions in the last block of epoch N (the resharding block) so that `parent_balance_burnt = X > 0`.
3. After the shard split, inspect the `ChunkExtra` for both child shards at the resharding block hash — both report `balance_burnt = X`.
4. In the first block of epoch N+1, `verify_total_supply_checked` computes `balance_burnt = X + X = 2X`.
5. The block header records `total_supply = prev_total_supply + minted_amount − 2X`.
6. The correct value is `prev_total_supply + minted_amount − X`.
7. The total supply is permanently short by `X` yoctoNEAR after the resharding. [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** chain/chain/src/resharding/manager.rs (L200-266)
```rust
        for (new_shard_uid, retain_mode) in
            [(left_child_shard, RetainMode::Left), (right_child_shard, RetainMode::Right)]
        {
            let parent_trie = tries
                .get_trie_for_shard(*parent_shard_uid, *parent_chunk_extra.state_root())
                .recording_reads_new_recorder();

            if !allow_resharding_without_memtries && !parent_trie.has_memtries() {
                tracing::error!(
                    ?block_hash,
                    ?parent_shard_uid,
                    "memtrie not loaded, cannot process memtrie resharding storage update"
                );
                return Err(Error::Other("Memtrie not loaded".to_string()));
            }

            tracing::info!(
                target: "resharding", ?new_shard_uid, ?retain_mode,
                "creating child trie by retaining nodes in parent memtrie"
            );

            // Get the congestion info for the child.
            // We need to record this as this is used later in ImplicitTransitionParams::Resharding chunk validation.
            let parent_epoch_id = block.header().epoch_id();
            let parent_shard_layout = self.epoch_manager.get_shard_layout(&parent_epoch_id)?;
            let parent_congestion_info = parent_chunk_extra.congestion_info();

            let child_epoch_id = self.epoch_manager.get_next_epoch_id(&block_hash)?;
            let child_shard_layout = self.epoch_manager.get_shard_layout(&child_epoch_id)?;
            let child_congestion_info = Self::get_child_congestion_info(
                &parent_trie,
                &parent_shard_layout,
                parent_congestion_info,
                &child_shard_layout,
                new_shard_uid,
                retain_mode,
            )?;

            // Split the parent trie and create a new child trie. Save the trie nodes in store and memtrie.
            // Note that we only apply the insertions from the trie changes as we don't want to delete
            // nodes associated with retain_split_shard operation for the child.
            let trie_changes = parent_trie.retain_split_shard(boundary_account, retain_mode)?;
            tries.apply_insertions(&trie_changes, *parent_shard_uid, &mut store_update);
            tries.apply_memtrie_changes(&trie_changes, *parent_shard_uid, block_height);

            // Persist TrieChanges so that cold store can copy the resharding
            // insertions. We store only insertions (no deletions) because
            // deletions are not applied during resharding and would corrupt
            // GC refcounts.
            store_update.set_trie_changes(
                *new_shard_uid,
                block_hash,
                &trie_changes.insertions_only(),
            );

            // TODO(resharding): set all fields of `ChunkExtra`. Consider stronger
            // typing. Clarify where it should happen when `State` and
            // `FlatState` update is implemented.
            let mut child_chunk_extra = ChunkExtra::clone(&parent_chunk_extra);
            *child_chunk_extra.state_root_mut() = trie_changes.new_root;
            *child_chunk_extra.congestion_info_mut() = child_congestion_info;

            chain_store_update.save_chunk_extra(
                block_hash,
                &new_shard_uid,
                child_chunk_extra.into(),
            );
```

**File:** chain/chain/src/runtime/mod.rs (L387-403)
```rust
        let burnt = apply_result
            .stats
            .balance
            .tx_burnt_amount
            .checked_add(apply_result.stats.balance.other_burnt_amount)
            .and_then(|r| r.checked_add(apply_result.stats.balance.slashed_burnt_amount))
            .ok_or_else(|| {
                Error::Other("Integer overflow during burnt balance summation".to_string())
            })?;

        // Theoretically this may become negative but the subsidized amount is many orders
        // of magnitude lower than the burned amount for each promise, so it should not
        // happen.
        let total_balance_burnt =
            burnt.checked_sub(apply_result.stats.balance.subsidized_amount).ok_or_else(|| {
                Error::Other("subsidized amount exceeds total burnt balance".to_string())
            })?;
```

**File:** chain/chain/src/validate.rs (L158-165)
```rust
    if prev_chunk_extra.gas_used() != chunk_header.prev_gas_used() {
        return Err(Error::InvalidGasUsed);
    }

    if prev_chunk_extra.balance_burnt() != chunk_header.prev_balance_burnt() {
        return Err(Error::InvalidBalanceBurnt);
    }

```

**File:** core/primitives/src/block.rs (L320-340)
```rust
    pub fn verify_total_supply_checked(
        &self,
        prev_total_supply: Balance,
        minted_amount: Option<Balance>,
    ) -> Option<bool> {
        let mut balance_burnt = Balance::ZERO;

        for chunk in self.chunks().iter_new() {
            balance_burnt = balance_burnt.checked_add(chunk.prev_balance_burnt())?;
        }

        let Some(new_total_supply) = prev_total_supply
            .checked_add(minted_amount.unwrap_or(Balance::ZERO))?
            .checked_sub(balance_burnt)
        else {
            // This corresponds to balance_burnt > prev_total_supply + minted_amount
            // which indicates invalid balance burnt, not arithmetic overflow
            return Some(false);
        };
        Some(self.header().total_supply() == new_total_supply)
    }
```
