### No vulnerability found for this question.

**Analysis summary:**

The claimed binding is: `withdrawal_utxo` fetched from the `withdrawals` table for `deposit_id` (via `Database::get_withdrawal_utxo_from_citrea_withdrawal`) must equal the actual withdrawal UTXO Citrea recorded for that same index.

Tracing the write path: `update_withdrawal_utxo_from_citrea_withdrawal` is only invoked from `Verifier::update_citrea_deposit_and_withdrawals`, which is driven by the verifier's own internal block-syncer loop, not by any attacker-reachable gRPC call. [1](#0-0) 

The `idx` and `withdrawal_utxo_outpoint` values it writes come directly from `CitreaClientT::collect_withdrawal_utxos`, which reads Citrea's on-chain `withdrawalUTXOs(idx)` mapping sequentially, starting from `last_withdrawal_idx + 1`. [2](#0-1) 

The `idx` column is the primary key of the `withdrawals` table, so there is no way for a second write to silently collide with or overwrite a different index's row; each Citrea withdrawal index maps to exactly one row. [3](#0-2) 

The same `idx`/`deposit_id` key is also used for `get_move_to_vault_txid_from_citrea_deposit`/`upsert_move_to_vault_txid_from_citrea_deposit`, meaning deposit index and withdrawal index share one authoritative counter row, both populated only from trusted Citrea RPC reads. [4](#0-3) [5](#0-4) 

The attacker's only externally-reachable input is `withdraw()` on the Citrea bridge contract itself, which assigns a new, contract-managed sequential index for the withdrawal UTXO bytes they choose — they cannot pick or collide with another `deposit_id`'s index, and this repo has no code path that lets a gRPC caller influence `update_withdrawal_utxo_from_citrea_withdrawal`'s `citrea_idx` or `withdrawal_utxo` arguments. Consequently, `Verifier::sign_optimistic_payout`'s check at core/src/verifier.rs:1646-1659 (`withdrawal_utxo != input_outpoint`) compares `input_outpoint` against a DB value that is provably sourced from Citrea's own state for that exact `deposit_id`, so the equality binding holds and cannot be broken from this repo's attack surface. [6](#0-5) 

Citrea contract-level defects (e.g., index reuse/collision inside the Bridge contract) are explicitly out of scope per the audit rules ("Citrea contract... defects with no path through this repository").

### Citations

**File:** core/src/verifier.rs (L1646-1659)
```rust
        // check if withdrawal utxo is correct
        let withdrawal_utxo = self
            .db
            .get_withdrawal_utxo_from_citrea_withdrawal(None, deposit_id)
            .await?;

        if withdrawal_utxo != input_outpoint {
            return Err(eyre::eyre!(
                "Withdrawal utxo is not correct: {:?} != {:?}",
                withdrawal_utxo,
                input_outpoint
            )
            .into());
        }
```

**File:** core/src/verifier.rs (L2224-2262)
```rust
        let new_withdrawals = self
            .citrea_client
            .collect_withdrawal_utxos(last_withdrawal_idx, l2_height_end)
            .await?;
        tracing::debug!(
            "New withdrawals received from Citrea: {:?}",
            new_withdrawals
        );

        for (idx, move_to_vault_txid) in new_deposits {
            tracing::info!(
                "Saving move to vault txid {:?} with index {} for Citrea deposits",
                move_to_vault_txid,
                idx
            );
            self.db
                .upsert_move_to_vault_txid_from_citrea_deposit(
                    Some(dbtx),
                    idx as u32,
                    &move_to_vault_txid,
                )
                .await?;
        }

        for (idx, withdrawal_utxo_outpoint) in new_withdrawals {
            tracing::info!(
                "Saving withdrawal utxo {:?} with index {} for Citrea withdrawals",
                withdrawal_utxo_outpoint,
                idx
            );
            self.db
                .update_withdrawal_utxo_from_citrea_withdrawal(
                    Some(dbtx),
                    idx as u32,
                    withdrawal_utxo_outpoint,
                    block_height,
                )
                .await?;
        }
```

**File:** core/src/citrea.rs (L458-496)
```rust
    async fn collect_withdrawal_utxos(
        &self,
        last_withdrawal_idx: Option<u32>,
        to_height: u64,
    ) -> Result<Vec<(u64, OutPoint)>, BridgeError> {
        let mut utxos = vec![];

        let mut start_idx = match last_withdrawal_idx {
            Some(idx) => idx + 1,
            None => 0,
        };

        loop {
            let withdrawal_utxo = self
                .contract
                .withdrawalUTXOs(U256::from(start_idx))
                .block(BlockId::Number(BlockNumberOrTag::Number(to_height)))
                .call()
                .await;
            match withdrawal_utxo {
                Err(e) if e.to_string().contains("execution reverted") => {
                    tracing::trace!("Withdrawal utxo not found for index, error: {:?}", e);
                    break;
                }
                Err(e) => return Err(e.into()),
                Ok(_) => {}
            }
            let withdrawal_utxo = withdrawal_utxo.expect("Failed to get withdrawal UTXO");
            let txid = withdrawal_utxo.txId.0;
            let txid =
                Txid::from_slice(txid.as_ref()).wrap_err("Failed to convert txid to Txid")?;
            let vout = withdrawal_utxo.outputId.0;
            let vout = u32::from_le_bytes(vout);
            let utxo = OutPoint { txid, vout };
            utxos.push((start_idx as u64, utxo));
            start_idx += 1;
        }
        Ok(utxos)
    }
```

**File:** core/src/database/schema.sql (L269-270)
```sql
create table if not exists withdrawals (
    idx int primary key,
```

**File:** core/src/database/verifier.rs (L50-83)
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

    pub async fn get_move_to_vault_txid_from_citrea_deposit(
        &self,
        tx: Option<DatabaseTransaction<'_>>,
        citrea_idx: u32,
    ) -> Result<Option<Txid>, BridgeError> {
        let query = sqlx::query_as::<_, (TxidDB,)>(
            "SELECT move_to_vault_txid FROM withdrawals WHERE idx = $1",
        )
        .bind(i32::try_from(citrea_idx).wrap_err("Failed to convert citrea index to i32")?);

        let result: Option<(TxidDB,)> =
            execute_query_with_tx!(self.connection, tx, query, fetch_optional)?;

        Ok(result.map(|(move_to_vault_txid,)| move_to_vault_txid.0))
    }
```

**File:** core/src/database/verifier.rs (L108-166)
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

    /// For the given deposit index, returns the withdrawal utxo associated with it
    /// If there is no withdrawal utxo set for the deposit, an error is returned
    pub async fn get_withdrawal_utxo_from_citrea_withdrawal(
        &self,
        tx: Option<DatabaseTransaction<'_>>,
        citrea_idx: u32,
    ) -> Result<OutPoint, BridgeError> {
        let query = sqlx::query_as::<_, (Option<TxidDB>, Option<i32>)>(
            "SELECT w.withdrawal_utxo_txid, w.withdrawal_utxo_vout
             FROM withdrawals w
             WHERE w.idx = $1",
        )
        .bind(i32::try_from(citrea_idx).wrap_err("Failed to convert citrea index to i32")?);

        let results = execute_query_with_tx!(self.connection, tx, query, fetch_optional)?;

        match results {
            None => Err(eyre::eyre!("Deposit with id {} is not set", citrea_idx).into()),
            Some((txid, vout)) => match (txid, vout) {
                (Some(txid), Some(vout)) => Ok(OutPoint {
                    txid: txid.0,
                    vout: u32::try_from(vout)
                        .wrap_err("Failed to convert withdrawal utxo vout to u32")?,
                }),
                _ => {
                    Err(eyre::eyre!("Withdrawal utxo is not set for deposit {}", citrea_idx).into())
                }
            },
        }
    }
```
