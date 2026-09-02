Now confirmed: the payout tx is funded via `fund_raw_transaction`/wallet PSBT with `add_inputs: true`, meaning **the operator adds its own wallet input(s)** to the withdrawal_utxo to cover `output_txout.value` (which is close to `bridge_amount`), since the withdrawal_utxo itself is typically a small/dust amount (see `dust_utxo` naming in tests). The user's presigned signature uses `SinglePlusAnyoneCanPay` sighash, which only commits to the withdrawal_utxo input and the payout output — it explicitly allows the operator to append additional inputs (their own funding) and a change output, per the comment at `crates/clementine-tx-sender/src/rbf.rs:162`. This confirms the operator genuinely fronts most of the `bridge_amount` value from its own wallet, and the reimbursement claim is legitimate economically.

This validates the premise in the question: the operator's payout tx is the transaction that actually fronts `bridge_amount` (mostly from operator funds, only using the withdrawal UTXO as an anchor/authorization input), and the OP_RETURN in that tx is the sole on-chain attribution mechanism tying the fronting act to `payout_payer_operator_xonly_pk`. Since the withdrawal UTXO is owned by the withdrawer's own key (`try_get_taproot_pk()` at `core/src/operator.rs:614-618`), the withdrawer can indeed sign and broadcast an entirely different, conflicting transaction spending the same outpoint with no OP_RETURN — RBF/double-spending the operator's genuine payout before `finality_depth` — with no protocol check preventing this, since `withdraw()`/`is_profitable` only check state at request time, not at confirmation time, and `Verifier::update_finalized_payouts` (`core/src/verifier.rs:2283-2350`) attributes whichever txid was actually mined for that outpoint per `bitcoin_syncer_spent_utxos.spending_txid`, with no cross-check against the operator's stored/broadcast payout txid. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

### Title
Withdrawer can replace-by-fee the withdrawal UTXO to strip operator attribution and permanently block reimbursement - (File: core/src/verifier.rs)

### Summary
The withdrawal UTXO used as the payout transaction's sole authorizing input is owned solely by the withdrawer's own key (`try_get_taproot_pk` in `core/src/operator.rs:614-618`), not by any N-of-N or operator-controlled script. Because the operator's genuine payout transaction is funded via RBF (`FeePayingType::RBF`, `core/src/tx_sender_queue.rs:92-104`) rather than atomically confirmed, the withdrawer can broadcast a higher-fee conflicting transaction spending the same outpoint before `finality_depth`, causing the chain to finalize the withdrawer's transaction instead of the operator's, while stripping the operator's identifying OP_RETURN output.

### Finding Description
The broken binding: `withdrawals.payout_txid` (as set by `update_payout_txs_and_payer_operator_xonly_pk`) should equal the txid of the transaction the *operator* broadcast that fronted `bridge_amount` to the withdrawer, and `payout_payer_operator_xonly_pk` should equal that operator's xonly pubkey.

Path: `Operator::withdraw` (`core/src/operator.rs:560-674`) builds `create_payout_txhandler` (`core/src/builder/transaction/operator_reimburse.rs:407-436`) with a single presigned input — the withdrawal UTXO, owned by the withdrawer's own taproot key — signed with `SinglePlusAnyoneCanPay`, which by design permits *anyone* funding the transaction to append extra inputs/outputs (`crates/clementine-tx-sender/src/rbf.rs:162`, "change output at last index (so that SinglePlusAnyoneCanPay signatures stay valid)"). The operator's wallet then adds its own input(s) via `fund_raw_transaction` (`core/src/operator.rs:652-673`) to cover the difference between the withdrawal UTXO's (often dust) value and `output_txout.value` (near `bridge_amount`), and the tx is queued as `FeePayingType::RBF` (`core/src/tx_sender_queue.rs:92-104`), meaning it is bumped, not fixed, until confirmation.

Since the withdrawal UTXO belongs solely to the withdrawer's private key, the withdrawer can independently construct and broadcast a fee-bumped, conflicting transaction spending the same `withdrawal_utxo_txid:vout`, without any OP_RETURN. Once mined (per the scenario's precondition that miners include the attacker's replacement instead of the operator's tx), `bitcoin_syncer_spent_utxos.spending_txid` records the attacker's txid for that outpoint. `Database::get_payout_txs_for_withdrawal_utxos` (`core/src/database/verifier.rs:170-196`) joins purely on `(txid, vout)` of the withdrawal UTXO, with no verification that the spending tx matches any operator-specific record — it simply returns whatever txid actually spent the outpoint. `Verifier::update_finalized_payouts` (`core/src/verifier.rs:2283-2350`) then parses that (attacker) tx's OP_RETURN, finds none, and sets `payout_payer_operator_xonly_pk = NULL` (`core/src/verifier.rs:2319-2328`), which the `update_payout_txs_and_payer_operator_xonly_pk` query writes unconditionally (`core/src/database/verifier.rs:226-245`).

No guard in the reachable path (`Operator::withdraw`'s profitability/signature checks, `is_kickoff_malicious`, or the finalized-payout sync logic) verifies that the confirmed spending transaction is the one the operator itself broadcast; the system trusts whichever transaction actually spends the outpoint on-chain, and only distinguishes operators via the OP_RETURN convention that any key holder of the input can omit.

### Impact Explanation
If the operator genuinely funded the withdrawer's payout output (adding its own bridge_amount-equivalent wallet input) and the withdrawer's own replacement transaction happens to pay the same output/amount (satisfying the presigned `SinglePlusAnyoneCanPay` commitment) while omitting the OP_RETURN, the operator's payout is permanently unattributed: `get_first_unhandled_payout_by_operator_xonly_pk` (`core/src/database/verifier.rs:282-313`) filters on `payout_payer_operator_xonly_pk = $1`, so a NULL value never surfaces for any operator, and `PayoutCheckerTask` (`core/src/task/payout_checker.rs`) never triggers `handle_finalized_payout`/kickoff/reimburse for that withdrawal. The operator that funded the withdrawal is permanently unable to claim reimbursement from the move-to-vault UTXO for this deposit — matching the Critical category "an honest operator permanently unable to be reimbursed." This is repeatable per withdrawal (each withdrawal has its own UTXO and outpoint), and the attacker (the withdrawer) can perform it at will since they hold the sole private key for the input.

### Likelihood Explanation
The precondition that miners choose to confirm the attacker's replacement over the operator's already-broadcast tx requires the attacker's replacement to have a materially higher feerate (standard RBF economics) and to arrive before `finality_depth` confirmations accrue on the operator's tx — entirely within reach of an unprivileged withdrawer who already controls the spending key and simply needs to pay a marginally higher fee. No collusion, hashrate majority, or protocol privilege is required; this only needs normal Bitcoin mempool/RBF policy. The withdrawer must have already registered the withdrawal via Citrea's `withdraw()` call and pointed at a UTXO they control, both explicitly within the stated attacker capabilities.

### Recommendation
Do not attribute a finalized payout purely from the outpoint-join result. Cross-check the confirmed spending txid against the specific txid the operator recorded/broadcast for that withdrawal (e.g., store the operator's payout txid at `withdraw()` time and compare, or require the operator's own registered spend before honoring any OP_RETURN-based attribution), and treat mismatches (a different, non-operator-originated transaction spending the withdrawal UTXO) as a signal to re-open/retry the withdrawal for another operator rather than silently nulling attribution. Alternatively, harden the payout tx construction so no third party (including the withdrawer, once they've committed to the payout via the Citrea `withdraw()` call) can independently redirect the UTXO without invalidating the withdrawal claim itself.

### Proof of Concept
`cargo test` (regtest, `MockCitreaClient`) plan:
1. Run a deposit + `withdraw()` flow identical to `core/src/test/manual_reimbursement.rs`, obtaining `withdrawal_utxo` (owned by test's `user_sk`) and calling `operator0.withdraw(...)` to get and broadcast the genuine `payout_tx` (assert it contains the operator OP_RETURN).
2. Before mining `finality_depth` confirmations, using `user_sk`, construct and broadcast a second, higher-fee transaction spending the same `withdrawal_utxo` outpoint to an arbitrary destination with no OP_RETURN output (RBF-signaled).
3. Mine blocks so the attacker's transaction (not the operator's) is the one included/finalized (may require manually invalidating/omitting the operator's tx from the miner's selection, or crafting a strictly higher-feerate conflicting tx and letting standard mempool policy prefer it).
4. Assert `bitcoin_syncer_spent_utxos.spending_txid` for the withdrawal outpoint equals the attacker's txid, not the operator's payout txid.
5. After the finalized-block syncer runs, assert `withdrawals.payout_payer_operator_xonly_pk IS NULL` (via `Database::get_payout_info_from_move_txid`), and assert `Database::get_first_unhandled_payout_by_operator_xonly_pk` never returns this withdrawal for the operator's xonly pubkey, proving the binding `payout_payer_operator_xonly_pk == operator_that_funded` is violated.

### Citations

**File:** core/src/operator.rs (L614-626)
```rust
        let user_xonly_pk = &input_utxo
            .txout
            .script_pubkey
            .try_get_taproot_pk()
            .wrap_err("Input utxo script pubkey is not a valid taproot script")?;

        let payout_txhandler = builder::transaction::create_payout_txhandler(
            input_utxo,
            output_txout,
            self.signer.xonly_public_key,
            in_signature,
            self.config.protocol_paramset().network,
        )?;
```

**File:** core/src/operator.rs (L651-674)
```rust
        // send payout tx using RBF
        let funded_tx = self
            .rpc
            .fund_raw_transaction(
                payout_txhandler.get_cached_tx(),
                Some(&bitcoincore_rpc::json::FundRawTransactionOptions {
                    add_inputs: Some(true),
                    include_unsafe: Some(false),
                    change_address: None,
                    change_position: Some(1),
                    change_type: None,
                    include_watching: None,
                    lock_unspents: Some(false),
                    fee_rate: Some(Amount::from_sat(fee_rate.to_sat_per_kvb())),
                    subtract_fee_from_outputs: None,
                    replaceable: None,
                    conf_target: None,
                    estimate_mode: None,
                }),
                None,
            )
            .await
            .wrap_err("Failed to fund raw transaction")?
            .hex;
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
