Based on my analysis of `core/src/database/header_chain_prover.rs`:

**The binding claimed to be broken**: "the selection `get_block_proof_by_hash` makes is a deterministic function of protocol state, not of insertion timing."

**Trace of the actual code**:

`get_block_proof_by_hash` is not a "first/next" selection function at all — it is an exact-match lookup by primary key:
```sql
SELECT proof FROM header_chain_proofs WHERE block_hash = $1
``` [1](#0-0) 

There is no ORDER BY, no LIMIT, no ambiguity between competing rows — `block_hash` is looked up by exact equality against a caller-supplied hash. Since `header_chain_proofs.block_hash` is the table's primary/unique key (enforced via `ON CONFLICT (block_hash) DO NOTHING` in `save_unproven_finalized_block`), no two rows can share the same `block_hash` value [2](#0-1) . For an attacker to "insert a colliding row" they would need a second block header hashing to the same `block_hash`, which requires breaking SHA-256d preimage/collision resistance — out of scope for this repo's threat model.

The actual "first"/"next" selection functions that use ordering (`get_next_unproven_block`, `get_latest_proven_block_info`, `get_latest_proven_block_info_until_height`) select deterministically by `height DESC/ASC` and `proof IS NULL/NOT NULL`, where `height` and `block_header` are derived from real Bitcoin block data fetched via `ExtendedBitcoinRpc::get_block_info_by_height` [3](#0-2)  — not from attacker-supplied gRPC input. An unprivileged network client sending gRPC requests or broadcasting Bitcoin transactions has no code path that lets them insert arbitrary rows into `header_chain_proofs`; that table is populated only from the node's own view of the finalized Bitcoin chain [4](#0-3) .

The question's premise conflates unrelated "first/next" selection primitives (unhandled payout, unused connector, next round — found in `core/src/operator.rs` and `core/src/states/round.rs`) with this module's block-hash-keyed lookup, but `get_block_proof_by_hash` itself has no insertion-order dependency and no collision surface reachable by an unprivileged attacker.

### No vulnerability found for this question.

### Citations

**File:** core/src/database/header_chain_prover.rs (L29-33)
```rust
        let query = sqlx::query(
                "INSERT INTO header_chain_proofs (block_hash, block_header, prev_block_hash, height) VALUES ($1, $2, $3, $4)
                ON CONFLICT (block_hash) DO NOTHING",
            )
            .bind(BlockHashDB(block_hash)).bind(BlockHeaderDB(block_header)).bind(BlockHashDB(block_header.prev_blockhash)).bind(block_height as i64);
```

**File:** core/src/database/header_chain_prover.rs (L41-59)
```rust
    async fn save_block_infos_within_range(
        &self,
        mut dbtx: Option<DatabaseTransaction<'_>>,
        rpc: &ExtendedBitcoinRpc,
        height_start: u32,
        height_end: u32,
    ) -> Result<(), BridgeError> {
        const BATCH_SIZE: u32 = 100;

        for batch_start in (height_start..=height_end).step_by(BATCH_SIZE as usize) {
            let batch_end = std::cmp::min(batch_start + BATCH_SIZE - 1, height_end);

            // Collect all block headers in this batch
            let mut block_infos = Vec::with_capacity((batch_end - batch_start + 1) as usize);
            for height in batch_start..=batch_end {
                let (block_hash, block_header) =
                    rpc.get_block_info_by_height(height as u64).await?;
                block_infos.push((block_hash, block_header, height));
            }
```

**File:** core/src/database/header_chain_prover.rs (L85-114)
```rust
    pub async fn fetch_and_save_missing_blocks(
        &self,
        mut dbtx: Option<DatabaseTransaction<'_>>,
        rpc: &ExtendedBitcoinRpc,
        genesis_height: u32,
        until_height: u32,
    ) -> Result<(), BridgeError> {
        if until_height == 0 {
            return Ok(());
        }
        let max_height = self
            .get_latest_finalized_block_height(dbtx.as_deref_mut())
            .await?;
        if let Some(max_height) = max_height {
            if max_height < until_height as u64 {
                self.save_block_infos_within_range(
                    dbtx.as_deref_mut(),
                    rpc,
                    max_height as u32 + 1,
                    until_height - 1,
                )
                .await?;
            }
        } else {
            tracing::debug!("Saving blocks from start until {}", until_height);
            self.save_block_infos_within_range(dbtx, rpc, genesis_height, until_height - 1)
                .await?;
        }
        Ok(())
    }
```

**File:** core/src/database/header_chain_prover.rs (L396-415)
```rust
    /// Gets a block's proof by referring to it by it's hash.
    pub async fn get_block_proof_by_hash(
        &self,
        tx: Option<DatabaseTransaction<'_>>,
        hash: block::BlockHash,
    ) -> Result<Option<Receipt>, BridgeError> {
        let query = sqlx::query_as("SELECT proof FROM header_chain_proofs WHERE block_hash = $1")
            .bind(BlockHashDB(hash));

        let receipt: (Option<Vec<u8>>,) =
            execute_query_with_tx!(self.connection, tx, query, fetch_one)?;
        let receipt = match receipt.0 {
            Some(r) => r,
            None => return Ok(None),
        };

        let receipt: Receipt = borsh::from_slice(&receipt).wrap_err(BridgeError::BorshError)?;

        Ok(Some(receipt))
    }
```
