This confirms the design: the header chain prover only ever receives blocks via `handle_finalized_block`, which is only invoked once `is_block_finalized` confirms `finality_depth` confirmations have passed [1](#0-0) . Reorgs deeper than `finality_depth` cause `fetch_new_blocks` to explicitly error out rather than silently reprocessing state [2](#0-1) .

## Analysis

**Binding claimed broken:** "bridge's confirmed-tx view == Bitcoin's active chain" after a reorg, allegedly broken inside `prove_with_input`.

**What `prove_with_input` actually is:** a stateless function that serializes a `HeaderChainCircuitInput` into a RISC0 executor environment and calls `prover.prove(env, elf)` [3](#0-2) . It performs no database reads/writes, no height/hash caching, and no reorg-related branching whatsoever — it is a pure proving call parameterized entirely by its `input` and `prev_receipt` arguments supplied by its caller.

**Where reorg logic actually lives:** `fetch_new_blocks` in `bitcoin_syncer.rs` walks backwards to find the common ancestor and explicitly errors if the reorg depth exceeds `finality_depth` [2](#0-1) , and `handle_reorg_events` marks the orphaned blocks non-canonical and emits `ReorgedBlock` events transactionally with the insertion of new canonical blocks [4](#0-3) . Crucially, the header-chain-prover pipeline (`save_unproven_block_cache` → `prove_if_ready` → `prove_and_save_block` → `prove_with_input`) is only fed blocks through `FinalizedBlockFetcherTask`/`handle_finalized_block`, which only advances once a block satisfies `paramset.is_block_finalized(...)`, i.e. has already accrued `finality_depth` confirmations [1](#0-0) [5](#0-4) .

**Why the attack doesn't work:** the attacker (unprivileged, no hashrate) can only choose transaction placement/fees within the unconfirmed window; they cannot cause a block that has already passed `finality_depth` confirmations to be orphaned without majority hashrate, which is explicitly out of scope. Since `prove_with_input`/`prove_and_save_block` only ever process blocks that are already finality-final, and any reorg attempting to exceed that depth is rejected with an explicit error requiring manual resync rather than silently double-processing or caching stale state, the claimed "event processed twice across rollback" or "height/hash cached across reorg" scenario has no code path: `prove_with_input` holds no cross-call cache, and its callers only see post-finality data.

## No vulnerability found for this question.

### Citations

**File:** core/src/bitcoin_syncer.rs (L279-290)
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
    }
```

**File:** core/src/bitcoin_syncer.rs (L298-339)
```rust
/// Marks blocks above the common ancestor as non-canonical and emits reorg events.
#[tracing::instrument(skip(db, dbtx), err(level = tracing::Level::ERROR), ret(level = tracing::Level::TRACE))]
async fn handle_reorg_events(
    db: &Database,
    dbtx: DatabaseTransaction<'_>,
    common_ancestor_height: u32,
) -> Result<(), BridgeError> {
    let reorg_blocks = db
        .update_non_canonical_block_hashes(Some(dbtx), common_ancestor_height)
        .await?;
    if !reorg_blocks.is_empty() {
        tracing::debug!("Reorg occurred! Block ids: {:?}", reorg_blocks);
    }

    for reorg_block_id in reorg_blocks {
        db.insert_event(Some(dbtx), BitcoinSyncerEvent::ReorgedBlock(reorg_block_id))
            .await?;
    }

    Ok(())
}

/// Processes and inserts new blocks into the database, emitting a new block event for each.
async fn process_new_blocks(
    db: &Database,
    rpc: &ExtendedBitcoinRpc,
    dbtx: DatabaseTransaction<'_>,
    new_blocks: &[BlockInfo],
) -> Result<(), BridgeError> {
    for block_info in new_blocks {
        let block = rpc
            .get_block(&block_info.hash)
            .await
            .wrap_err("Failed to get block")?;

        let block_id = save_block(db, dbtx, &block, block_info.height).await?;
        db.insert_event(Some(dbtx), BitcoinSyncerEvent::NewBlock(block_id))
            .await?;
    }

    Ok(())
}
```

**File:** core/src/bitcoin_syncer.rs (L534-544)
```rust
                // Update states to catch up to finalized chain
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
```

**File:** core/src/header_chain_prover.rs (L458-494)
```rust
    fn prove_with_input(
        input: HeaderChainCircuitInput,
        prev_receipt: Option<Receipt>,
        network: Network,
    ) -> Result<Receipt, HeaderChainProverError> {
        let mut env = ExecutorEnv::builder();

        env.write_slice(&borsh::to_vec(&input).wrap_err(BridgeError::BorshError)?);

        if let Some(prev_receipt) = prev_receipt {
            env.add_assumption(prev_receipt);
        }

        let env = env
            .build()
            .map_err(|e| eyre::eyre!(e))
            .wrap_err("Failed to build environment")?;

        let prover = risc0_zkvm::default_prover();

        let elf = match network {
            Network::Bitcoin => MAINNET_HEADER_CHAIN_ELF,
            Network::Testnet4 => TESTNET4_HEADER_CHAIN_ELF,
            Network::Signet => SIGNET_HEADER_CHAIN_ELF,
            Network::Regtest => REGTEST_HEADER_CHAIN_ELF,
            _ => Err(BridgeError::UnsupportedNetwork.into_eyre())?,
        };

        let receipt = prover.prove(env, elf).map_err(|e| eyre::eyre!(e))?.receipt;
        tracing::debug!(
            "Proof receipt for header chain circuit input {:?}: {:?}",
            input,
            receipt
        );

        Ok(receipt)
    }
```

**File:** core/src/verifier.rs (L3090-3102)
```rust
        self.update_finalized_payouts(dbtx, block_id, &block_cache)
            .await?;

        #[cfg(feature = "automation")]
        {
            // Save unproven block cache to the database
            self.header_chain_prover
                .save_unproven_block_cache(Some(&mut dbtx), &block_cache)
                .await?;
            while (self.header_chain_prover.prove_if_ready().await?).is_some() {
                // Continue until prove_if_ready returns None
                // If it doesn't return None, it means next batch_size amount of blocks were proven
            }
```
