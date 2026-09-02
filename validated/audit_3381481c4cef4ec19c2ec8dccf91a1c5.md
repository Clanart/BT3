[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** core/src/database/verifier.rs (L50-67)
```rust
    pub async fn upsert_move_to_vault_txid_from_citrea_deposit(
        &self,
        tx: Option<DatabaseTransaction<'_>>,
        citrea_idx: u32,
        move_to_vault_txid: &Txid,
    ) -> Result<(), BridgeError> {
        let query = sqlx::query(
            "INSERT INTO withdrawals (idx, move_to_vault_txid)
             VALUES ($1, $2)
             ON CONFLICT (idx) DO UPDATE
             SET move_to_vault_txid = $2",
        )
        .bind(i32::try_from(citrea_idx).wrap_err("Failed to convert citrea index to i32")?)
        .bind(TxidDB(*move_to_vault_txid));

        execute_query_with_tx!(self.connection, tx, query, execute)?;
        Ok(())
    }
```

**File:** core/src/database/verifier.rs (L85-106)
```rust
    pub async fn update_replacement_deposit_move_txid(
        &self,
        tx: DatabaseTransaction<'_>,
        idx: u32,
        new_move_txid: Txid,
    ) -> Result<(), BridgeError> {
        let query = sqlx::query(
            "UPDATE withdrawals
             SET move_to_vault_txid = $2
             WHERE idx = $1
             RETURNING idx",
        )
        .bind(i32::try_from(idx).wrap_err("Failed to convert idx to i32")?)
        .bind(TxidDB(new_move_txid))
        .fetch_optional(tx.deref_mut())
        .await?;

        if query.is_none() {
            return Err(eyre::eyre!("Replacement move txid not found: {}", idx).into());
        }
        Ok(())
    }
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
