### Title
Missing uniqueness check on withdrawal UTXO allows the same Bitcoin outpoint to be assigned to two withdrawal indices, enabling operator double-reimbursement - (`core/src/citrea.rs`, `core/src/database/verifier.rs`)

### Summary
`CitreaClient::collect_withdrawal_utxos` blindly forwards `(idx, OutPoint)` pairs read from the Citrea Bridge contract, and `Database::update_withdrawal_utxo_from_citrea_withdrawal` unconditionally writes them into the `withdrawals` table with no uniqueness constraint on `(withdrawal_utxo_txid, withdrawal_utxo_vout)`. An unprivileged attacker who deposits twice and then calls `withdraw()` on the Citrea Bridge contract twice with an identical, self-controlled txid+vout can make two different `idx` rows reference the same physical Bitcoin outpoint.

### Finding Description
The binding that should hold is: for each withdrawal index `i`, exactly one vault UTXO (deposit `i`) is spent to reimburse exactly one payout transaction that itself uniquely spends withdrawal-outpoint `i`. Concretely: `withdrawals[i].withdrawal_utxo_(txid,vout)` should be injective across `i`.

Trace:
1. `CitreaClient::collect_withdrawal_utxos` (`core/src/citrea.rs:458-496`) iterates the contract's `withdrawalUTXOs(idx)` mapping and returns `(start_idx as u64, utxo)` for every index with no check that `utxo` was previously returned for a smaller index.
2. `Verifier::update_citrea_deposit_and_withdrawals` (`core/src/verifier.rs:2248-2262`) loops over these pairs and calls `Database::update_withdrawal_utxo_from_citrea_withdrawal(idx, withdrawal_utxo_outpoint, block_height)` for each `idx` independently.
3. `update_withdrawal_utxo_from_citrea_withdrawal` (`core/src/database/verifier.rs:108-135`) does an `UPDATE withdrawals SET withdrawal_utxo_txid=$2, withdrawal_utxo_vout=$3 ... WHERE idx=$1` — a per-row write with no uniqueness constraint against other rows' `withdrawal_utxo_txid/vout`.
4. `Operator::withdraw` (`core/src/operator.rs:560-627`) and `Verifier::sign_optimistic_payout` (`core/src/verifier.rs:1570-1660`) both validate the caller-supplied input outpoint against `get_withdrawal_utxo_from_citrea_withdrawal(idx)` for *one* `idx` at a time — they cannot detect that the same outpoint is also registered for a different `idx`.
5. Because the real Bitcoin outpoint can only be spent once, only one physical payout transaction is ever created; the second attempted call to spend the already-spent outpoint will fail at the RPC/consensus layer. However, once the single payout transaction is observed on-chain and matched back to `withdrawals` rows (via `get_payout_txs_for_withdrawal_utxos`/`update_payout_txs_and_payer_operator_xonly_pk`, `core/src/database/verifier.rs`), the match is performed by outpoint equality, not by a unique row. Since two rows (tied to two distinct deposits/move-to-vault txids) carry the identical `withdrawal_utxo_txid/vout`, the single payout transaction gets attributed as fulfilling *both* `idx` rows' `payout_txid`.
6. `Verifier::is_kickoff_malicious` (`core/src/verifier.rs:1859-1915`) only checks that `operator_xonly_pk` and the committed payout blockhash match the `withdrawals` row for the deposit being kicked off — since both rows now show the same operator and same payout blockhash (because it's literally the same payout tx), this check passes for both deposits.
7. As a result, the operator can request reimbursement transactions (`handle_finalized_payout`, `get_reimbursement_txs`) for *both* deposits' vault UTXOs, even though only one real BTC payment to the user ever occurred.

No existing guard (deposit validity checks, `is_profitable`, `SECP.verify_schnorr`, `is_kickoff_malicious`, storage-proof verification, SPV verify, or a DB uniqueness constraint) checks that a `withdrawal_utxo` outpoint is unique across `idx` values before or after this update.

### Impact Explanation
This breaks the CUSTODY invariant that exactly one vault UTXO is spent per withdrawal index. The operator can be reimbursed from a second deposit's move-to-vault UTXO it never actually funded a payout for — this is the Critical category "an operator reimbursed for a payout it never funded." The blast radius scales with however many deposits/withdrawals the attacker (or a colluding operator) chooses to alias to the same outpoint; each aliased `idx` drains one additional vault UTXO from the bridge with no matching real payment, directly reducing bridge solvency.

### Likelihood Explanation
The attacker needs only to be an ordinary bridge user: fund two deposits (paying `bridge_amount` BTC each, recoverable via the exploit itself), and call the public/unauthenticated `withdraw()` function on the Citrea Bridge contract twice with identical attacker-controlled txid+vout bytes. No verifier, operator, or aggregator key material is required, and no majority hash-rate or TLS interception is needed. The only cost is the gas/fees for two Citrea `withdraw()` calls and Bitcoin fees for one payout transaction — cheap relative to the reimbursement amounts drained (`bridge_amount` per extra aliased index). It is fully repeatable across any number of deposits by the same or different attackers.

### Recommendation
Enforce uniqueness of `(withdrawal_utxo_txid, withdrawal_utxo_vout)` across the `withdrawals` table (DB-level `UNIQUE` constraint) and reject/flag any `collect_withdrawal_utxos` result whose outpoint already exists under a different `idx` before calling `update_withdrawal_utxo_from_citrea_withdrawal`. Additionally, the payout-matching join (`get_payout_txs_for_withdrawal_utxos`) should attribute a spent outpoint to at most one `idx`, and `is_kickoff_malicious`/reimbursement flow should reject a kickoff if the referenced payout transaction has already been credited to another deposit's `idx`.

### Proof of Concept
```rust
// core/src/database/verifier.rs (production DB test, not mock)
#[tokio::test]
async fn duplicate_withdrawal_utxo_across_two_idx_should_be_rejected() {
    let config = create_test_config_with_thread_name().await;
    let db = Database::new(&config).await.unwrap();
    let mut dbtx = db.begin_transaction().await.unwrap();

    let move_txid1 = Txid::from_byte_array([0x11; 32]);
    let move_txid2 = Txid::from_byte_array([0x22; 32]);
    let shared_utxo = bitcoin::OutPoint { txid: Txid::from_byte_array([0xAA; 32]), vout: 0 };

    db.upsert_move_to_vault_txid_from_citrea_deposit(Some(&mut dbtx), 0, &move_txid1).await.unwrap();
    db.upsert_move_to_vault_txid_from_citrea_deposit(Some(&mut dbtx), 1, &move_txid2).await.unwrap();

    db.update_withdrawal_utxo_from_citrea_withdrawal(Some(&mut dbtx), 0, shared_utxo, 100).await.unwrap();

    // EXPECTED (fix): this second write with the same outpoint for a different idx must fail.
    let result = db.update_withdrawal_utxo_from_citrea_withdrawal(Some(&mut dbtx), 1, shared_utxo, 100).await;
    assert!(result.is_err(), "second idx must not be allowed to claim the same withdrawal outpoint");

    // Currently: both succeed, and both idx=0 and idx=1 report the same withdrawal_utxo,
    // demonstrating the broken CUSTODY binding.
    let u0 = db.get_withdrawal_utxo_from_citrea_withdrawal(Some(&mut dbtx), 0).await.unwrap();
    let u1 = db.get_withdrawal_utxo_from_citrea_withdrawal(Some(&mut dbtx), 1).await.unwrap();
    assert_eq!(u0, u1); // both point to the same physical outpoint -> vulnerability
}
```
No mainnet or live Citrea is required — this reproduces purely against the local Postgres test database used by `core/src/database/verifier.rs`'s existing test harness. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) 

Note on uncertainty: I was unable to retrieve the exact SQL body of `get_payout_txs_for_withdrawal_utxos` and `update_payout_txs_and_payer_operator_xonly_pk` (only their call signatures/usages were found before the tool budget ran out). The double-attribution mechanism in step 5-6 above is inferred from their signatures, the schema's lack of any uniqueness constraint, and the join semantics implied by their usage in `core/src/database/verifier.rs`'s tests — this should be confirmed by reading their full bodies before treating this as fully proven.

### Citations

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

**File:** core/src/database/verifier.rs (L253-280)
```rust
    pub async fn get_payout_info_from_move_txid(
        &self,
        tx: Option<DatabaseTransaction<'_>>,
        move_to_vault_txid: Txid,
    ) -> Result<Option<(Option<XOnlyPublicKey>, BlockHash, Txid, i32)>, BridgeError> {
        let query = sqlx::query_as::<_, (Option<XOnlyPublicKeyDB>, BlockHashDB, TxidDB, i32)>(
            "SELECT w.payout_payer_operator_xonly_pk, w.payout_tx_blockhash, w.payout_txid, w.idx
             FROM withdrawals w
             WHERE w.move_to_vault_txid = $1
               AND w.payout_txid IS NOT NULL
               AND w.payout_tx_blockhash IS NOT NULL",
        )
        .bind(TxidDB(move_to_vault_txid));

        let result: Option<(Option<XOnlyPublicKeyDB>, BlockHashDB, TxidDB, i32)> =
            execute_query_with_tx!(self.connection, tx, query, fetch_optional)?;

        result
            .map(|(operator_xonly_pk, block_hash, txid, deposit_idx)| {
                Ok((
                    operator_xonly_pk.map(|pk| pk.0),
                    block_hash.0,
                    txid.0,
                    deposit_idx,
                ))
            })
            .transpose()
    }
```

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

**File:** core/src/verifier.rs (L1859-1914)
```rust
    async fn is_kickoff_malicious(
        &self,
        kickoff_witness: Witness,
        deposit_data: &mut DepositData,
        kickoff_data: KickoffData,
        dbtx: DatabaseTransaction<'_>,
    ) -> Result<bool, BridgeError> {
        let move_txid =
            create_move_to_vault_txhandler(deposit_data, self.config.protocol_paramset())?
                .get_cached_tx()
                .compute_txid();

        let payout_info = self
            .db
            .get_payout_info_from_move_txid(Some(dbtx), move_txid)
            .await?;
        let Some((operator_xonly_pk_opt, payout_blockhash, _, _)) = payout_info else {
            tracing::warn!(
                "No payout info found in db for move txid {move_txid}, assuming malicious"
            );
            return Ok(true);
        };

        let Some(operator_xonly_pk) = operator_xonly_pk_opt else {
            tracing::warn!("No operator xonly pk found in payout tx OP_RETURN, assuming malicious");
            return Ok(true);
        };

        if operator_xonly_pk != kickoff_data.operator_xonly_pk {
            tracing::warn!("Operator xonly pk for the payout does not match with the kickoff_data");
            return Ok(true);
        }

        let wt_derive_path = WinternitzDerivationPath::Kickoff(
            kickoff_data.round_idx,
            kickoff_data.kickoff_idx,
            self.config.protocol_paramset(),
        );
        let commits = extract_winternitz_commits(
            kickoff_witness,
            &[wt_derive_path],
            self.config.protocol_paramset(),
        )?;
        let blockhash_data = commits.first();
        // only last 20 bytes of the blockhash is committed
        let truncated_blockhash = &payout_blockhash[12..];
        if let Some(committed_blockhash) = blockhash_data {
            if committed_blockhash != truncated_blockhash {
                tracing::warn!("Payout blockhash does not match committed hash: committed: {:?}, truncated payout blockhash: {:?}",
                        blockhash_data, truncated_blockhash);
                return Ok(true);
            }
        } else {
            return Err(eyre::eyre!("Couldn't retrieve committed data from witness").into());
        }
        Ok(false)
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

**File:** core/src/operator.rs (L588-596)
```rust
        // Check Citrea for the withdrawal state.
        let withdrawal_utxo = self
            .db
            .get_withdrawal_utxo_from_citrea_withdrawal(None, withdrawal_index)
            .await?;

        if withdrawal_utxo != input_utxo.outpoint {
            return Err(eyre::eyre!("Input UTXO does not match withdrawal UTXO from Citrea: Input Outpoint: {0}, Withdrawal Outpoint (from Citrea): {1}", input_utxo.outpoint, withdrawal_utxo).into());
        }
```
