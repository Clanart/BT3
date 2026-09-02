### Title
Missing uniqueness constraint on `withdrawal_utxo_txid`/`withdrawal_utxo_vout` lets a single payout transaction satisfy two withdrawal indices, letting an operator be reimbursed twice for one payout - ([File: core/src/database/verifier.rs])

### Summary
The `withdrawals` table has no uniqueness constraint on `(withdrawal_utxo_txid, withdrawal_utxo_vout)` across different `idx` rows, and `get_payout_txs_for_withdrawal_utxos` joins solely on `(txid, vout)`. If Citrea registers the same withdrawal outpoint for two different `idx` values (permitted since this repo's writer `update_withdrawal_utxo_from_citrea_withdrawal` blindly stores whatever outpoint Citrea reports per index with no cross-index uniqueness check), a single confirmed payout transaction gets matched to both withdrawal indices, causing the operator that funded only one payout to be credited and reimbursed for two.

### Finding Description
The claimed binding is: `withdrawal_utxo(idx=1) == withdrawal_utxo(idx=2)` must never allow one on-chain payout to satisfy two withdrawal indices.

- `withdrawals` schema: `idx int primary key`, plus plain columns `withdrawal_utxo_txid`, `withdrawal_utxo_vout` with **no unique constraint** across rows [1](#0-0) .
- `update_withdrawal_utxo_from_citrea_withdrawal` writes whatever outpoint Citrea's `collect_withdrawal_utxos` reports for a given `idx`, without checking if that outpoint is already used by another `idx` [2](#0-1) .
- `get_payout_txs_for_withdrawal_utxos` finds, for a spent-UTXO block, all withdrawal rows whose `(withdrawal_utxo_txid, withdrawal_utxo_vout)` match a spent outpoint, joined purely by `(txid, vout)`—if two `idx` rows share the same outpoint, **both** rows match the single spending transaction [3](#0-2) .
- `update_finalized_payouts` (verifier sync) calls this join, then for every returned `(idx, payout_txid)` pair extracts the operator xonly pubkey from the payout tx's OP_RETURN and writes it into `payout_payer_operator_xonly_pk` for that `idx` via `update_payout_txs_and_payer_operator_xonly_pk` [4](#0-3) . Both `idx=1` and `idx=2` receive the same `payout_txid` and same operator credit, even though only one real payout transaction was broadcast.
- `PayoutCheckerTask` then independently discovers **each** unhandled payout by operator xonly pk (`get_first_unhandled_payout_by_operator_xonly_pk`) and calls `handle_finalized_payout` for each `idx`, which allocates a **separate kickoff/reimbursement flow per deposit index** [5](#0-4) [6](#0-5) .
- The bridge circuit's storage-proof check (`verify_storage_proofs`) verifies the withdrawal outpoint recorded on Citrea for the *specific* `storage_proof.index` (i.e., deposit idx) matches the input spent by the payout tx [7](#0-6) [8](#0-7) . Since the attacker deliberately registered the identical outpoint under both idx=1 and idx=2 on the Citrea side, this per-index storage proof is legitimately satisfied for idx=2 by the very same on-chain payout tx used for idx=1 — nothing in the circuit checks that this outpoint/payout hasn't already been consumed for a different deposit index.
- Result: the operator obtains a valid kickoff/reimbursement path for deposit idx=2 backed by a payout transaction that never funded that specific withdrawal.

### Impact Explanation
An operator is reimbursed for a payout it never funded: two deposits' move-to-vault UTXOs (idx=1 and idx=2) are unlocked for operator reimbursement while the operator broadcast only one payout transaction (spending one dust UTXO once). This directly moves protocol-held BTC value into the operator's reimbursement path without a matching fronted withdrawal for one of the two deposits, matching the Critical category "an operator reimbursed for a payout it never funded." The attack is repeatable across any number of additional deposits the attacker chooses to alias to the same outpoint, and the loss scales with the number of aliased deposits.

### Likelihood Explanation
This requires the attacker to make two (or more) separate deposits and to register identical withdrawal-outpoint bytes for two different withdrawal indices when calling Citrea's `withdraw`, which per the threat model the attacker fully controls (they choose the bytes of the withdrawal UTXO). The DB layer in this repo does nothing to prevent or detect the collision (no unique constraint, no idx-scoped join), so the exploit is fully reachable purely through this repo's normal sync and reimbursement pipeline once the collision exists in Citrea state. Attacker cost is limited to bridging in twice (funding two deposits) plus standard Bitcoin fees for one payout transaction; no privileged role is required.

### Recommendation
- Add a unique constraint (or partial unique index where `withdrawal_utxo_txid IS NOT NULL`) on `(withdrawal_utxo_txid, withdrawal_utxo_vout)` in the `withdrawals` table, and reject/flag any Citrea-reported withdrawal collision instead of silently storing it.
- In `get_payout_txs_for_withdrawal_utxos`/`update_finalized_payouts`, before crediting an operator for an `idx`, verify that `payout_txid` has not already been used to satisfy a different `idx`; if a collision is detected, treat all but the first-confirmed `idx` as invalid, mark the withdrawal as unpayable, or make bridge circuit's storage-proof verification enforce global outpoint uniqueness per payout across deposit indices.

### Proof of Concept
```rust
// core/src/database/verifier.rs - add near existing `update_get_payout_txs_from_citrea_withdrawal` test
#[tokio::test]
async fn duplicate_withdrawal_utxo_across_idx_double_credits_operator() {
    let config = create_test_config_with_thread_name().await;
    let db = Database::new(&config).await.unwrap();
    let mut dbtx = db.begin_transaction().await.unwrap();

    let shared_utxo = bitcoin::OutPoint {
        txid: bitcoin::Txid::from_byte_array([0xAA; 32]),
        vout: 0,
    };
    let payout_txid = Txid::from_byte_array([0xBB; 32]);
    let operator_pk = generate_random_xonly_pk();

    let block_id = db.insert_block_info(Some(&mut dbtx), &BlockHash::all_zeros(), &BlockHash::all_zeros(), 0).await.unwrap();
    db.insert_txid_to_block(&mut dbtx, block_id, &payout_txid).await.unwrap();
    // Only ONE spend of shared_utxo is ever recorded on-chain.
    db.insert_spent_utxo(&mut dbtx, block_id, &payout_txid, &shared_utxo.txid, shared_utxo.vout.into()).await.unwrap();

    let idx1 = 1u32;
    let idx2 = 2u32;
    let move_txid1 = Txid::from_byte_array([0x01; 32]);
    let move_txid2 = Txid::from_byte_array([0x02; 32]);

    db.upsert_move_to_vault_txid_from_citrea_deposit(Some(&mut dbtx), idx1, &move_txid1).await.unwrap();
    db.upsert_move_to_vault_txid_from_citrea_deposit(Some(&mut dbtx), idx2, &move_txid2).await.unwrap();

    // Attacker registers the SAME outpoint for both withdrawal indices.
    db.update_withdrawal_utxo_from_citrea_withdrawal(Some(&mut dbtx), idx1, shared_utxo, block_id).await.unwrap();
    db.update_withdrawal_utxo_from_citrea_withdrawal(Some(&mut dbtx), idx2, shared_utxo, block_id).await.unwrap();

    // The join used by verifier sync to detect payouts:
    let txs = db.get_payout_txs_for_withdrawal_utxos(Some(&mut dbtx), block_id).await.unwrap();

    // BUG: both idx1 and idx2 are matched to the SAME single payout_txid,
    // even though only one payout transaction was ever broadcast/confirmed.
    assert_eq!(txs.len(), 2, "expected the single payout to be double-matched (this is the bug)");
    assert!(txs.iter().any(|(idx, txid)| *idx == idx1 && *txid == payout_txid));
    assert!(txs.iter().any(|(idx, txid)| *idx == idx2 && *txid == payout_txid));

    // Confirms both withdrawal rows would be credited to the same operator,
    // enabling reimbursement kickoffs for idx2 without a matching fronted payout.
    db.update_payout_txs_and_payer_operator_xonly_pk(
        Some(&mut dbtx),
        vec![(idx1, payout_txid, Some(operator_pk), BlockHash::all_zeros()),
             (idx2, payout_txid, Some(operator_pk), BlockHash::all_zeros())],
    ).await.unwrap();

    let info1 = db.get_payout_info_from_move_txid(Some(&mut dbtx), move_txid1).await.unwrap().unwrap();
    let info2 = db.get_payout_info_from_move_txid(Some(&mut dbtx), move_txid2).await.unwrap().unwrap();
    assert_eq!(info1.0, Some(operator_pk));
    assert_eq!(info2.0, Some(operator_pk)); // operator credited for a payout it never separately funded
}
```
This demonstrates the binding violation directly at the database layer that both `update_finalized_payouts` and `PayoutCheckerTask` rely on for crediting operator reimbursements, without requiring a live Citrea network.

### Citations

**File:** core/src/database/schema.sql (L269-281)
```sql
create table if not exists withdrawals (
    idx int primary key,
    move_to_vault_txid bytea not null,
    withdrawal_utxo_txid bytea,
    withdrawal_utxo_vout int,
    withdrawal_batch_proof_bitcoin_block_height int,
    payout_txid bytea,
    payout_payer_operator_xonly_pk text,
    payout_tx_blockhash text check (payout_tx_blockhash ~ '^[a-fA-F0-9]{64}'),
    is_payout_handled boolean not null default false,
    kickoff_txid bytea,
    created_at timestamp not null default now()
);
```

**File:** core/src/database/verifier.rs (L108-135)
```rust
    pub async fn update_withdrawal_utxo_from_citrea_withdrawal(
        &self,
        tx: Option<DatabaseTransaction<'_>>,
        citrea_idx: u32,
        withdrawal_utxo: OutPoint,
        withdrawal_batch_proof_bitcoin_block_height: u32,
    ) -> Result<(), BridgeError> {
        let query = sqlx::query(
            "UPDATE withdrawals
             SET withdrawal_utxo_txid = $2,
                 withdrawal_utxo_vout = $3,
                 withdrawal_batch_proof_bitcoin_block_height = $4
             WHERE idx = $1",
        )
        .bind(i32::try_from(citrea_idx).wrap_err("Failed to convert citrea index to i32")?)
        .bind(TxidDB(withdrawal_utxo.txid))
        .bind(
            i32::try_from(withdrawal_utxo.vout)
                .wrap_err("Failed to convert withdrawal utxo vout to i32")?,
        )
        .bind(
            i32::try_from(withdrawal_batch_proof_bitcoin_block_height)
                .wrap_err("Failed to convert withdrawal batch proof bitcoin block height to i32")?,
        );

        execute_query_with_tx!(self.connection, tx, query, execute)?;
        Ok(())
    }
```

**File:** core/src/database/verifier.rs (L168-196)
```rust
    /// Returns the withdrawal indexes and their spending txid for the given
    /// block id.
    pub async fn get_payout_txs_for_withdrawal_utxos(
        &self,
        tx: Option<DatabaseTransaction<'_>>,
        block_id: u32,
    ) -> Result<Vec<(u32, Txid)>, BridgeError> {
        let query = sqlx::query_as::<_, (i32, TxidDB)>(
            "SELECT w.idx, bsu.spending_txid
             FROM withdrawals w
             JOIN bitcoin_syncer_spent_utxos bsu
                ON bsu.txid = w.withdrawal_utxo_txid
                AND bsu.vout = w.withdrawal_utxo_vout
             WHERE bsu.block_id = $1",
        )
        .bind(i32::try_from(block_id).wrap_err("Failed to convert block id to i32")?);

        let results = execute_query_with_tx!(self.connection, tx, query, fetch_all)?;

        results
            .into_iter()
            .map(|(idx, txid)| {
                Ok((
                    u32::try_from(idx).wrap_err("Failed to convert withdrawal index to u32")?,
                    txid.0,
                ))
            })
            .collect()
    }
```

**File:** core/src/verifier.rs (L2283-2350)
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

        let mut payout_txs_and_payer_operator_idx = vec![];
        for (idx, payout_txid) in payout_txids {
            let payout_tx_idx = block_cache.txids.get(&payout_txid);
            if payout_tx_idx.is_none() {
                tracing::error!(
                    "Payout tx not found in block cache: {:?} and in block: {:?}",
                    payout_txid,
                    block_id
                );
                tracing::error!("Block cache: {:?}", block_cache);
                return Err(eyre::eyre!("Payout tx not found in block cache").into());
            }
            let payout_tx_idx = payout_tx_idx.expect("Payout tx not found in block cache");
            let payout_tx = &block.txdata[*payout_tx_idx];
            // Find the first output that contains OP_RETURN
            let circuit_payout_tx = CircuitTransaction::from(payout_tx.clone());
            let op_return_output = get_first_op_return_output(&circuit_payout_tx);

            // If OP_RETURN doesn't exist in any outputs, or the data in OP_RETURN is not a valid xonly_pubkey,
            // operator_xonly_pk will be set to None, and the corresponding column in DB set to NULL.
            // This can happen if optimistic payout is used, or an operator constructs the payout tx wrong.
            let operator_xonly_pk = op_return_output
                .and_then(|output| parse_op_return_data(&output.script_pubkey))
                .and_then(|bytes| XOnlyPublicKey::from_slice(bytes).ok());

            if operator_xonly_pk.is_none() {
                tracing::info!(
                    "No valid operator xonly pk found in payout tx {:?} OP_RETURN. Either it is an optimistic payout or the operator constructed the payout tx wrong",
                    payout_txid
                );
            }

            tracing::info!(
                "A new payout tx detected for withdrawal {}, payout txid: {:?}, operator xonly pk: {:?}",
                idx,
                payout_txid,
                operator_xonly_pk
            );

            payout_txs_and_payer_operator_idx.push((
                idx,
                payout_txid,
                operator_xonly_pk,
                block_hash,
            ));
        }

        self.db
            .update_payout_txs_and_payer_operator_xonly_pk(
                Some(dbtx),
                payout_txs_and_payer_operator_idx,
            )
            .await?;
```

**File:** core/src/task/payout_checker.rs (L39-79)
```rust
    async fn run_once(&mut self) -> Result<Self::Output, BridgeError> {
        let mut dbtx = self.db.begin_transaction().await?;
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

**File:** core/src/operator.rs (L839-860)
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
```

**File:** circuits-lib/src/bridge_circuit/storage_proof.rs (L44-132)
```rust
pub fn verify_storage_proofs(
    storage_proof: &StorageProof,
    state_root: [u8; 32],
) -> (WithdrawalOutpointTxid, u32, MoveTxid) {
    let utxo_storage_proof: EIP1186StorageProof =
        serde_json::from_str(&storage_proof.storage_proof_utxo)
            .expect("Failed to deserialize UTXO storage proof");

    let vout_storage_proof: EIP1186StorageProof =
        serde_json::from_str(&storage_proof.storage_proof_vout)
            .expect("Failed to deserialize vout storage proof");

    let deposit_storage_proof: EIP1186StorageProof =
        serde_json::from_str(&storage_proof.storage_proof_deposit_txid)
            .expect("Failed to deserialize deposit storage proof");

    let storage_address: U256 = {
        let mut keccak = Keccak256::new();
        keccak.update(UTXOS_STORAGE_INDEX);
        let hash = keccak.finalize();
        U256::from_be_bytes(
            <[u8; 32]>::try_from(&hash[..]).expect("Hash slice has incorrect length"),
        )
    };

    let storage_key_utxo: alloy_primitives::Uint<256, 4> =
        storage_address + U256::from(storage_proof.index * 2);

    let storage_key_vout: alloy_primitives::Uint<256, 4> =
        storage_address + U256::from(storage_proof.index * 2 + 1);

    let storage_address_deposit: U256 = {
        let mut keccak = Keccak256::new();
        keccak.update(DEPOSIT_STORAGE_INDEX);
        let hash = keccak.finalize();
        U256::from_be_bytes(
            <[u8; 32]>::try_from(&hash[..]).expect("Hash slice has incorrect length"),
        )
    };

    let deposit_storage_key: alloy_primitives::Uint<256, 4> =
        storage_address_deposit + U256::from(storage_proof.index);

    let deposit_storage_key_bytes = deposit_storage_key.to_be_bytes::<32>();

    if deposit_storage_key_bytes != deposit_storage_proof.key.as_b256().0 {
        panic!(
            "Invalid deposit storage key. left: {:?} right: {:?}",
            deposit_storage_key_bytes,
            deposit_storage_proof.key.as_b256().0
        );
    }

    if storage_key_utxo.to_be_bytes() != utxo_storage_proof.key.as_b256().0 {
        panic!(
            "Invalid withdrawal UTXO storage key. left: {:?} right: {:?}",
            storage_key_utxo.to_be_bytes::<32>(),
            utxo_storage_proof.key.as_b256().0
        );
    }

    if storage_key_vout.to_be_bytes() != vout_storage_proof.key.as_b256().0 {
        panic!(
            "Invalid withdrawal vout storage key. left: {:?} right: {:?}",
            storage_key_vout.to_be_bytes::<32>(),
            vout_storage_proof.key.as_b256().0
        );
    }

    storage_verify(&utxo_storage_proof, state_root);

    storage_verify(&deposit_storage_proof, state_root);

    storage_verify(&vout_storage_proof, state_root);

    let buf: [u8; 32] = vout_storage_proof.value.to_be_bytes();

    // ENDIANNESS SHOULD BE CHECKED THIS FIELD IS 4 BYTES in the contract
    let vout = u32::from_le_bytes(
        buf[28..32]
            .try_into()
            .expect("Vout value conversion failed"),
    );

    let wd_outpoint = WithdrawalOutpointTxid(utxo_storage_proof.value.to_be_bytes());

    let move_txid = MoveTxid(deposit_storage_proof.value.to_be_bytes());

    (wd_outpoint, vout, move_txid)
```

**File:** circuits-lib/src/bridge_circuit/mod.rs (L182-204)
```rust
    // Storage proof verification for deposit tx index and withdrawal outpoint
    let (user_wd_outpoint, vout, move_txid) =
        verify_storage_proofs(&input.sp, light_client_circuit_output.l2_state_root);

    let user_wd_txid = bitcoin::Txid::from_byte_array(*user_wd_outpoint);

    let payout_input_index: usize = input.payout_input_index as usize;

    assert_eq!(
        user_wd_txid,
        input.payout_spv.transaction.input[payout_input_index]
            .previous_output
            .txid,
        "Invalid withdrawal transaction ID"
    );

    assert_eq!(
        vout,
        input.payout_spv.transaction.input[payout_input_index]
            .previous_output
            .vout,
        "Invalid withdrawal transaction output index"
    );
```
