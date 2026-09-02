### Title
Unauthenticated OP_RETURN + naive previous_output matching lets an attacker impersonate an operator's payout, causing false payout attribution and unfunded reimbursement claims - (File: core/src/verifier.rs, core/src/database/verifier.rs, circuits-lib/src/bridge_circuit/mod.rs)

### Summary
The pipeline that decides "which Bitcoin transaction is operator X's payout for withdrawal N" trusts only (a) whichever transaction happens to spend the withdrawal outpoint, and (b) an unsigned OP_RETURN push claimed to be the operator's x-only pubkey. Since the withdrawal UTXO's key is controlled by the withdrawer (an unprivileged attacker), the attacker can race the operator's honest payout with their own conflicting spend of the same outpoint, embedding any operator's real (public) x-only pubkey in an OP_RETURN, causing the automated pipeline to attribute an unfunded/incorrect transaction as that operator's completed payout.

### Finding Description
The binding that must hold is: `payout_spv.transaction` (the tx fed into `bridge_circuit`) must be the exact transaction the operator itself constructed via `create_payout_txhandler` and broadcast to pay `output_txout` to the withdrawer — not merely any transaction whose input happens to reference the same `previous_output`.

Tracing the path:
- `Operator::withdraw` (`core/src/operator.rs:560-692`) builds the payout tx via `create_payout_txhandler` (`core/src/builder/transaction/operator_reimburse.rs:407-436`), using the withdrawer's own `SinglePlusAnyoneCanPay` signature over the input UTXO that the withdrawer itself owns and chose (`in_outpoint`). This signature only binds the output at the same index (SIGHASH_SINGLE) and permits ANYONECANPAY, but crucially the withdrawer's private key already controls this UTXO independently of any of this — the withdrawer can spend it however they like at any time.
- Payout attribution is derived later purely from chain-scan data: `get_payout_txs_for_withdrawal_utxos` (`core/src/database/verifier.rs:168-196`) just joins on `bsu.txid = w.withdrawal_utxo_txid AND bsu.vout = w.withdrawal_utxo_vout` — i.e. whichever transaction spent that outpoint, with no signature or output-value check.
- `update_finalized_payouts` (`core/src/verifier.rs:2283-2353`) then extracts `operator_xonly_pk` purely by parsing the first OP_RETURN output (`get_first_op_return_output`/`parse_op_return_data`) — an unsigned, freely-choosable 32-byte push, not a signature.
- `PayoutCheckerTask::run_once` (`core/src/task/payout_checker.rs:31-111`) reads this DB-attributed record for the local operator's own xonly pubkey and, if automation is enabled, calls `handle_finalized_payout`, leading to kickoff construction/signing (`core/src/operator.rs:886-916`) that commits `payout_tx_blockhash` toward a reimbursement claim — with no re-check that this transaction is the one the operator itself signed/broadcast for that withdrawal.
- `validate_payer_is_operator` (`core/src/operator.rs:1686-1740`) and the verifier's `is_kickoff_malicious` (`core/src/verifier.rs:1857-1915`) only compare the recorded `operator_xonly_pk` (from the same untrusted OP_RETURN parse) and committed blockhash — never the payout output amount/script or a proof that the tx was signed under the intended payout witness.
- Finally, `bridge_circuit` (`circuits-lib/src/bridge_circuit/mod.rs:137-236`) only asserts `payout_spv.transaction.input[payout_input_index].previous_output` equals the withdrawal outpoint from the storage proof and pulls `operator_xonlypk` from the OP_RETURN for `deposit_constant` — it never checks that the input's witness corresponds to the operator's actual payout signing flow, nor that `output_txout` matches anything committed on the Citrea side.

An attacker who owns the withdrawal UTXO's key (which is exactly what an unprivileged withdrawer controls) can broadcast a competing spend of `in_outpoint` with an arbitrary output (e.g., paying themselves) and an OP_RETURN copied from any real operator's public x-only pubkey (visible in that operator's own kickoff/round outputs). If this attacker transaction confirms instead of (or before) the operator's honest payout, every downstream step above accepts it as "operator X's payout" purely from `previous_output` + OP_RETURN matching.

### Impact Explanation
This breaks payout attribution integrity for the whole reimbursement pipeline: the automated `PayoutCheckerTask` can drive an operator into signing and broadcasting a kickoff/reimbursement claim for a transaction it never constructed, matching the Critical category "an operator reimbursed for a payout it never funded." Symmetrically, the honest operator's own correctly-funded payout can be starved of attribution (the DB row for that withdrawal only ever stores one `spending_txid`), matching "an honest operator permanently unable to be reimbursed." The attack is repeatable per withdrawal and does not require compromising any key, verifier, or aggregator — only the withdrawer's own UTXO key, which every withdrawer already controls. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

### Likelihood Explanation
The attacker only needs to be the withdrawer (any unprivileged user who calls `withdraw`/registers a Citrea withdrawal), controls the private key for the withdrawal UTXO's scriptPubkey by construction, and needs to win a normal mempool/mining race against the operator's broadcast — a capability every Bitcoin holder has (RBF, fee bumping). No verifier or aggregator collusion, no key compromise, and no majority hashrate are required — only standard fee-based transaction replacement. Cost is limited to Bitcoin transaction fees. This is repeatable across every withdrawal and every operator, since the OP_RETURN pubkey values are all public.

### Recommendation
Do not attribute a payout to an operator based solely on `previous_output` matching plus an unauthenticated OP_RETURN value. Instead, when the operator's `withdraw()` flow signs and broadcasts a payout tx, persist the resulting txid (and ideally the exact serialized transaction) as the sole trusted reference; `update_finalized_payouts`/`get_payout_txs_for_withdrawal_utxos` and `bridge_circuit` should require that the confirmed transaction's txid matches this operator-committed txid (or otherwise cryptographically prove the output/witness matches the operator-approved payout), not merely that some transaction spent the same outpoint and carries a copyable OP_RETURN.

### Proof of Concept
```
# cargo test plan (regtest, no mainnet, no live Citrea):
1. Set up a withdrawal UTXO owned by a test "attacker" key (P2TR), register it as
   the Citrea withdrawal outpoint via the existing test DB helpers used in
   core/src/test/deposit_and_withdraw_e2e.rs.
2. Have the honest operator call `withdraw()` (core/src/operator.rs::withdraw),
   producing payout_tx_honest that pays the correct out_amount/out_script_pubkey
   and embeds the operator's real xonly pubkey OP_RETURN, but do NOT broadcast it yet
   (capture the signed tx).
3. As the attacker, craft payout_tx_evil spending the same in_outpoint with the
   attacker's own key (standard keypath spend, no relation to operator's signature),
   an output paying the attacker (not the withdrawer), and an OP_RETURN containing
   the same operator's real xonly pubkey bytes (copied from the operator's own
   round/kickoff tx outputs on-chain).
4. Broadcast payout_tx_evil first and mine it; then attempt to broadcast
   payout_tx_honest and observe it is rejected (double-spend).
5. Assert (binding check, left side): the actual output paid in the mined
   transaction (block.txdata[i].output[0]) does NOT equal output_txout that the
   operator constructed/signed in step 2.
   Assert (binding check, right side): despite this, 
   core::database::verifier::get_payout_txs_for_withdrawal_utxos returns
   payout_tx_evil's txid for this withdrawal idx, and
   core::verifier::update_finalized_payouts records
   payout_payer_operator_xonly_pk == honest operator's xonly pubkey.
6. Assert that PayoutCheckerTask::run_once for the honest operator proceeds to
   call handle_finalized_payout / build a kickoff referencing payout_tx_evil's
   blockhash, without any assertion failing that would have compared
   payout_tx_evil's output against the operator's own signed output_txout.
```

### Citations

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

**File:** core/src/verifier.rs (L2311-2342)
```rust
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
