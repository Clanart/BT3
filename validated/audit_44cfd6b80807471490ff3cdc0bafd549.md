### No vulnerability found for this question.

The premise assumes a time gap where the operator's WOTS commitment predates confirmation of the payout tx. That is not how the code works: `payout_tx_blockhash` passed into `Operator::handle_finalized_payout` is *sourced from* `Database::get_first_unhandled_payout_by_operator_xonly_pk`, which is populated only after Citrea sync has already observed the payout transaction as finalized on a specific, already-mined Bitcoin block [1](#0-0) . The operator only calls `handle_finalized_payout` (and only then WOTS-commits `payout_tx_blockhash.last_20_bytes()`) with the block hash of the block that *already* contains the confirmed payout tx [2](#0-1) .

There is therefore no window in which the operator commits to a block hash before the payout tx is mined into it — commitment strictly follows confirmation, not the other way around. An attacker flooding low-fee competing transactions or fee-sniping can at most delay *when* `handle_finalized_payout` is eventually invoked (by delaying confirmation of the payout tx itself), but cannot cause the WOTS-committed hash to diverge from the block that actually contains the payout tx, since the hash is only read and committed after that block is known. Once confirmed, `payout_spv.block_header.compute_block_hash()` and the WOTS-committed hash are derived from the same confirmed block by construction, and `lc_l1_block_hash != spv_l1_block_hash` in `bridge_circuit` (`circuits-lib/src/bridge_circuit/mod.rs:178-180`) only fails if the LCP/SPV data don't correspond to that same block — which is a data-availability precondition already enforced by `fetch_validate_and_store_lcp` fetching the LCP for that exact `payout_block_height` [3](#0-2) .

The described attack is fundamentally a confirmation-delay/fee-sniping scenario affecting *when* the operator can act, not a way to force a mismatched commitment — this falls under denial-of-service / delay behavior, which is explicitly out of scope for this audit.

### Citations

**File:** core/src/task/payout_checker.rs (L41-54)
```rust
        let unhandled_payout = self
            .db
            .get_first_unhandled_payout_by_operator_xonly_pk(
                Some(&mut dbtx),
                self.operator.signer.xonly_public_key,
            )
            .await?;

        if unhandled_payout.is_none() {
            return Ok(false);
        }

        let (citrea_idx, move_to_vault_txid, payout_tx_blockhash) =
            unhandled_payout.expect("Must be Some");
```

**File:** core/src/operator.rs (L839-899)
```rust
    pub async fn handle_finalized_payout<'a>(
        &'a self,
        dbtx: DatabaseTransaction<'a>,
        deposit_outpoint: OutPoint,
        payout_tx_blockhash: BlockHash,
    ) -> Result<bitcoin::Txid, BridgeError> {
        let (deposit_id, deposit_data) = self
            .db
            .get_deposit_data(Some(dbtx), deposit_outpoint)
            .await?
            .ok_or(BridgeError::DatabaseError(sqlx::Error::RowNotFound))?;

        // get unused kickoff connector
        let (round_idx, kickoff_idx) = self
            .db
            .get_unused_and_signed_kickoff_connector(
                Some(dbtx),
                deposit_id,
                self.signer.xonly_public_key,
            )
            .await?
            .ok_or(BridgeError::DatabaseError(sqlx::Error::RowNotFound))?;

        let current_round_index = self.db.get_current_round_index(Some(dbtx)).await?;
        tracing::info!(
            "Operator: Current round index: {}, round idx for kickoff: {}",
            current_round_index,
            round_idx
        );
        #[cfg(feature = "automation")]
        if current_round_index != round_idx {
            // we currently have no free kickoff connectors in the current round, so we need to end round first
            // if current_round_index should only be smaller than round_idx, and should not be smaller by more than 1
            // so sanity check:
            if current_round_index.next_round() != round_idx {
                return Err(eyre::eyre!(
                    "Internal error: Expected the current round ({:?}) to be equal to or 1 less than the round of the first available kickoff for deposit reimbursement ({:?}) for deposit {:?}. If the round is less than the current round, there is an issue with the logic of the fn that gets the first available kickoff. If the round is greater, that means the next round do not have any kickoff connectors available for reimbursement, which should not be possible.",
                    current_round_index, round_idx, deposit_outpoint
                ).into());
            }
            tracing::info!(
                "Operator: Starting next round to be able to get reimbursement for the payout"
            );
            // start the next round to be able to get reimbursement for the payout
            self.end_round(dbtx).await?;
        }

        // get signed txs,
        let kickoff_data = KickoffData {
            operator_xonly_pk: self.signer.xonly_public_key,
            round_idx,
            kickoff_idx,
        };

        let payout_tx_blockhash = payout_tx_blockhash.as_byte_array().last_20_bytes();

        #[cfg(test)]
        let payout_tx_blockhash = self
            .config
            .test_params
            .maybe_disrupt_payout_tx_block_hash_commit(payout_tx_blockhash);
```

**File:** core/src/citrea.rs (L326-360)
```rust
    async fn fetch_validate_and_store_lcp(
        &self,
        payout_block_height: u64,
        deposit_index: u32,
        db: &Database,
        mut dbtx: Option<DatabaseTransaction<'_>>,
        paramset: &'static ProtocolParamset,
    ) -> Result<Receipt, BridgeError> {
        let saved_data = db
            .get_lcp_for_assert(dbtx.as_deref_mut(), deposit_index)
            .await?;
        if let Some(lcp) = saved_data {
            // if already saved, do nothing
            return Ok(lcp);
        };

        let lcp_result = self
            .get_light_client_proof(payout_block_height, paramset)
            .await?;
        let (_lcp, lcp_receipt, _l2_height) = match lcp_result {
            Some(lcp) => lcp,
            None => {
                return Err(eyre::eyre!(
                    "Light client proof could not be fetched found for block height {}",
                    payout_block_height
                )
                .into())
            }
        };

        // save the LCP for assert
        db.insert_lcp_for_assert(dbtx, deposit_index, lcp_receipt.clone())
            .await?;

        Ok(lcp_receipt)
```
