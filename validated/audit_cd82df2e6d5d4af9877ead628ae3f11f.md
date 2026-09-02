### No vulnerability found for this question.

The premise requires reorging a block that has already been recorded as "finalized" by the bridge's own bitcoin syncer, which contradicts the unprivileged/no-majority-hashrate threat model.

**Why the exploit doesn't work:**

The `payout_tx_blockhash` that `Operator::handle_finalized_payout` truncates and commits via Winternitz signature is not read live from the chain tip — it comes from the `withdrawals.payout_tx_blockhash` DB column, populated by `Verifier::update_finalized_payouts` [1](#0-0) , which is only invoked from `Verifier::handle_finalized_block` [2](#0-1) .

`handle_finalized_block` is only dispatched for a given block once `FinalizedBlockFetcherTask` determines the block has already accumulated `finality_depth` confirmations via `ProtocolParamset::is_block_finalized` [3](#0-2) [4](#0-3) . So by the time the operator's `PayoutCheckerTask` reads the "unhandled payout" record and calls `handle_finalized_payout` with that blockhash [5](#0-4) , block B1 already has `finality_depth` confirmations on top of it.

For the attacker's scenario ("orphan B1 via a 1-block reorg" right after this commit) to succeed, the attacker would need to reorg a chain segment that is already `finality_depth` blocks deep — i.e., outrace the honest network by that many blocks. That is a majority-hashrate-class attack, which is explicitly listed as out of scope ("Reject ... majority hashrate"). The bridge's own `fetch_new_blocks` function even hard-errors if a reorg exceeds `finality_depth` [6](#0-5) , confirming that reorgs within the protocol's trust assumptions cannot exceed this depth.

Separately, the attacker (unprivileged, no operator role, no collateral) cannot cause "their own decoy payout" to be the one an honest operator fronts — the payout transaction is constructed and broadcast by the operator itself in response to a Citrea withdrawal event, not by an arbitrary Bitcoin transaction broadcaster.

Since the binding (`committed_blockhash == truncated_blockhash` for the actual finalized payout block) cannot be broken without violating the finality-depth/majority-hashrate exclusion already enforced by the syncer, there is no reachable path for an unprivileged attacker to cause a false positive in `Verifier::is_kickoff_malicious`.

### Citations

**File:** core/src/verifier.rs (L2283-2296)
```rust
    async fn update_finalized_payouts(
        &self,
        dbtx: DatabaseTransaction<'_>,
        block_id: u32,
        block_cache: &block_cache::BlockCache,
    ) -> Result<(), BridgeError> {
        let payout_txids = self
            .db
            .get_payout_txs_for_withdrawal_utxos(Some(dbtx), block_id)
            .await?;

        let block = &block_cache.block;

        let block_hash = block.block_hash();
```

**File:** core/src/verifier.rs (L3053-3091)
```rust
    pub async fn handle_finalized_block(
        &self,
        mut dbtx: DatabaseTransaction<'_>,
        block_id: u32,
        block_height: u32,
        block_cache: Arc<block_cache::BlockCache>,
        light_client_proof_wait_interval_secs: Option<u32>,
    ) -> Result<(), BridgeError> {
        tracing::info!("Verifier handling finalized block height: {}", block_height);

        // before a certain number of blocks, citrea doesn't produce proofs (defined in citrea config)
        let max_attempts = light_client_proof_wait_interval_secs.unwrap_or(TEN_MINUTES_IN_SECS);
        let timeout = Duration::from_secs(max_attempts as u64);

        let (l2_height_start, l2_height_end) = self
            .citrea_client
            .get_citrea_l2_height_range(
                block_height.into(),
                timeout,
                self.config.protocol_paramset(),
            )
            .await
            .inspect_err(|e| tracing::error!("Error getting citrea l2 height range: {:?}", e))?;

        tracing::debug!(
            "l2_height_start: {:?}, l2_height_end: {:?}, collecting deposits and withdrawals...",
            l2_height_start,
            l2_height_end
        );
        self.update_citrea_deposit_and_withdrawals(
            dbtx,
            l2_height_start,
            l2_height_end,
            block_height,
        )
        .await?;

        self.update_finalized_payouts(dbtx, block_id, &block_cache)
            .await?;
```

**File:** core/src/bitcoin_syncer.rs (L279-289)
```rust
        // new blocks includes the new one (block with height our previous tip + 1),
        // so new_blocks.len() = 5 -> 4 reorged blocks,
        if new_blocks.len() as u32 > finality_depth {
            return Err(eyre::eyre!(
                "Number of reorged blocks {} is greater than finality depth {}, reorged blocks: {:?}. If true, increase finality depth and resync the chain",
                new_blocks.len() - 1,
                finality_depth,
                new_blocks
            )
            .into());
        }
```

**File:** core/src/bitcoin_syncer.rs (L535-576)
```rust
                while self
                    .paramset
                    .is_block_finalized(expected_next_finalized, new_block_height)
                {
                    if new_tip && !warned {
                        warned = true;
                        // this event is multiple blocks away, report
                        tracing::warn!("Received event with multiple finalized blocks, expected 1 for ordered events. Got a new block with height {new_block_height}, expected next finalized block {}", self.next_finalized_height);
                    }
                    new_tip = true;

                    let block = self
                        .db
                        .get_full_block(Some(&mut dbtx), expected_next_finalized)
                        .await?
                        .ok_or(eyre::eyre!(
                            "Block at height {} not found in BlockFetcherTask, current tip height is {}",
                            expected_next_finalized, new_block_height
                        ))?;

                    let new_block_id = self
                        .db
                        .get_canonical_block_id_from_height(
                            Some(&mut dbtx),
                            expected_next_finalized,
                        )
                        .await?;

                    let Some(new_block_id) = new_block_id else {
                        tracing::error!("Block at height {} not found in BlockFetcherTask, current tip height is {}", expected_next_finalized, new_block_height);
                        return Err(eyre::eyre!(
                            "Block at height {} not found in BlockFetcherTask, current tip height is {}",
                            expected_next_finalized, new_block_height
                        ).into());
                    };

                    self.handler
                        .handle_new_block(&mut dbtx, new_block_id, block, expected_next_finalized)
                        .await?;

                    expected_next_finalized += 1;
                }
```

**File:** crates/clementine-config/src/protocol.rs (L165-173)
```rust
    /// Checks if a block is finalized. In clementine and citrea, finality depth means the amount of confirmations needed for a block to be considered finalized.
    /// The chain tip has 1 confirmation.
    pub fn is_block_finalized(&self, block_height: u32, chain_tip_height: u32) -> bool {
        if block_height > chain_tip_height {
            return false;
        }

        chain_tip_height - block_height + 1 >= self.finality_depth
    }
```

**File:** core/src/task/payout_checker.rs (L53-79)
```rust
        let (citrea_idx, move_to_vault_txid, payout_tx_blockhash) =
            unhandled_payout.expect("Must be Some");

        tracing::info!(
            "Unhandled payout found for withdrawal {}, move_txid: {}",
            citrea_idx,
            move_to_vault_txid
        );

        let deposit_data = self
            .db
            .get_deposit_data_with_move_tx(Some(&mut dbtx), move_to_vault_txid)
            .await?;
        if deposit_data.is_none() {
            return Err(eyre::eyre!("Fronted withdrawal for move tx {move_to_vault_txid} found, but the signatures for the deposit are not found in the db.").into());
        }

        let deposit_data = deposit_data.expect("Must be Some");

        let kickoff_txid = self
            .operator
            .handle_finalized_payout(
                &mut dbtx,
                deposit_data.get_deposit_outpoint(),
                payout_tx_blockhash,
            )
            .await?;
```
