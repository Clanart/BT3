Confirmed: this is a valid, critical finding.

### Title
Payout circuit and on-chain payout-attribution never check that the withdrawer actually received the withdrawal amount, letting an unprivileged withdrawer frame any operator into being "reimbursed for a payout it never funded" - (circuits-lib/src/bridge_circuit/mod.rs, core/src/verifier.rs)

### Summary
`bridge_circuit` and the on-chain payout indexer `update_finalized_payouts` only check that a `TransactionType::Payout`-shaped transaction spends the exact registered withdrawal outpoint/vout and that it carries *some* OP_RETURN xonly-pubkey; they never verify that the transaction's output actually paid the withdrawer the registered withdrawal amount. Since the withdrawal UTXO is spent purely with the withdrawer's own signature (key-spend, `SpendPath::KeySpend` in `create_payout_txhandler`), any withdrawer can single-handedly construct and broadcast a payout that pays themselves 1 sat and stamps an arbitrary (including some other, honest) operator's `xonly_pk` into the OP_RETURN, causing the protocol to permanently attribute the withdrawal to that operator.

### Finding Description
The claimed binding is: `value the withdrawer actually received == the withdrawal amount recorded by the Citrea Bridge contract for this index`. Tracing both the ZK circuit and the off-chain indexer shows neither side of this equality is ever compared.

- In `bridge_circuit` (circuits-lib/src/bridge_circuit/mod.rs:182-204), after `verify_storage_proofs` returns `(user_wd_outpoint, vout, move_txid)`, the code only asserts `user_wd_txid == input[payout_input_index].previous_output.txid` and `vout == input[payout_input_index].previous_output.vout` [1](#0-0) . There is no check anywhere in the function against the value of `input.payout_spv.transaction.output[...]` versus the bridge/withdrawal amount.
- `get_first_op_return_output`/`parse_op_return_data` extract whatever xonly-pubkey bytes are pushed in the OP_RETURN with no cryptographic binding to the operator that actually authored the transaction [2](#0-1) .
- The real-world attribution logic that decides "who is credited with fronting this withdrawal" is `update_finalized_payouts` (core/src/verifier.rs:2283-2353). It looks up any transaction spending the tracked withdrawal UTXO (`get_payout_txs_for_withdrawal_utxos`), pulls the first OP_RETURN output, and calls `parse_op_return_data`/`XOnlyPublicKey::from_slice` on it to decide `operator_xonly_pk`, storing that into `withdrawals.payout_payer_operator_xonly_pk` with **no check of the payout's output value** [3](#0-2) .
- The withdrawal UTXO itself belongs to the user/withdrawer and is spent purely by the user's own key-spend signature in `create_payout_txhandler` (`SpendPath::KeySpend`, input signed only with `user_sig`) — no operator signature or cooperation is required to spend it [4](#0-3) . Any withdrawer therefore fully controls the shape of the "payout" transaction, including its OP_RETURN payload and output amounts.
- Once `payout_payer_operator_xonly_pk` is set to a given operator, `PayoutCheckerTask::run_once` (core/src/task/payout_checker.rs:39-111) picks it up via `get_first_unhandled_payout_by_operator_xonly_pk` filtered on **that operator's own** `signer.xonly_public_key`, and unconditionally calls `Operator::handle_finalized_payout`, which allocates an unused kickoff connector, signs, and queues the Kickoff transaction — again with no re-verification of output value [5](#0-4) [6](#0-5) .
- `Verifier::is_kickoff_malicious` corroborates the same attacker-controlled attribution: it only checks that `operator_xonly_pk` recorded in the DB (from the OP_RETURN) matches `kickoff_data.operator_xonly_pk` and that the committed payout blockhash matches — it never checks payout value either [7](#0-6) .

Exploit flow: the attacker (as the registered withdrawer for a given Citrea `deposit_id`/withdrawal index) constructs `in_outpoint` as a dust UTXO they control, calls `withdraw` on Citrea themselves, then broadcasts a Bitcoin transaction spending that UTXO with their own key, paying 1 sat to `output_script_pubkey`, and puts a targeted honest operator's `xonly_pk` bytes in the OP_RETURN. Once mined and finalized, `update_finalized_payouts` durably attributes this withdrawal to the honest operator. `PayoutCheckerTask` running on that honest operator's own node then autonomously treats this as its own completed fronting payment and drives the state machine to build and queue a Kickoff/Reimburse transaction chain that spends `bridge_amount` out of the move-to-vault UTXO into the operator's `reimburse_addr` — for a withdrawal the operator never actually funded (the withdrawer received essentially nothing). None of the listed guards (`verify_storage_proofs`, `SPV::verify`, `lc_proof_verifier`, `is_kickoff_malicious`, the presigned tx graph) check payout value; they only check block inclusion, storage-proof consistency of the withdrawal outpoint/vout, and OP_RETURN-operator-vs-kickoff-operator matching, none of which touch the actual sats delivered.

### Impact Explanation
This is squarely the explicitly listed Critical category "an operator reimbursed for a payout it never funded." `bridge_amount` BTC leaves the move-to-vault UTXO via `create_reimburse_txhandler` (core/src/builder/transaction/operator_reimburse.rs:341-385) into the named operator's `operator_reimbursement_address`, while the withdrawer received essentially nothing (or an amount the operator never paid). This is repeatable per deposit/withdrawal since it only requires being the registered withdrawer for that specific index; it can target any operator whose `xonly_pk` is public knowledge (all operator pubkeys are protocol-public data). Practically this also mis-marks a legitimate withdrawal as handled (`is_payout_handled`) permanently, denying the real withdrawer their funds and potentially locking the honest operator into an on-chain kickoff/assert/disprove cycle for a payment it never made, wasting its own kickoff connector/round slot and possibly its whole automation pipeline for that deposit.

### Likelihood Explanation
Preconditions are minimal and entirely within an unprivileged withdrawer's control: they must simply be the party who registered the Citrea `withdraw` call for that deposit (a normal, permissionless action), and be able to broadcast a low-fee Bitcoin transaction (costs a fraction of a cent in fees plus the withdrawal dust UTXO). No verifier, operator, or aggregator cooperation is needed since the input UTXO is entirely user-key-spend. This is fully reproducible in regtest with no mainnet or live Citrea dependency (Citrea `withdraw` semantics can be simulated via the same DB/RPC seams the test suite already uses, e.g. `update_withdrawal_utxo_from_citrea_withdrawal`).

### Recommendation
Add an explicit value check in both places:
1. In `bridge_circuit` (circuits-lib/src/bridge_circuit/mod.rs), after locating `payout_input_index`, assert that `input.payout_spv.transaction.output[<withdrawal output index>].value` equals the withdrawal amount attested by the storage proof (extend `verify_storage_proofs`/`StorageProof` to also commit to and return the registered withdrawal amount from the Bridge contract storage, analogous to how UTXO/vout are already read from storage slots), and panic otherwise.
2. In `update_finalized_payouts` (core/src/verifier.rs), before attributing `operator_xonly_pk` to a payout tx, verify the payout's designated output value against the expected withdrawal amount (`bridge_amount - fees`) fetched from Citrea/DB, and only record attribution (and let `PayoutCheckerTask` proceed) when this matches; otherwise mark the payout as invalid/optimistic-only with `operator_xonly_pk = None`.

### Proof of Concept
```
// circuits-lib tests (cargo test -p circuits-lib)
#[test]
fn bridge_circuit_accepts_underpaid_payout_should_fail_but_does_not() {
    // Build a BridgeCircuitInput where:
    // - verify_storage_proofs mocked/constructed to return (wd_txid, vout, move_txid)
    //   matching a real withdrawal registered for `bridge_amount` sats.
    // - payout_spv.transaction.input[payout_input_index].previous_output == (wd_txid, vout)  [correct]
    // - payout_spv.transaction.output[0].value == Amount::from_sat(1)  [attacker pays 1 sat instead of bridge_amount]
    // - payout_spv.transaction has an OP_RETURN output containing an honest operator's real xonly_pk bytes.
    //
    // ASSERT (binding under test):
    //   left  = payout_spv.transaction.output[withdrawal_output_index].value  // 1 sat
    //   right = registered_withdrawal_amount (bridge_amount)
    // assert_ne!(left, right); // demonstrates values differ
    //
    // Then call bridge_circuit(&guest, work_only_image_id) and observe:
    // - No panic occurs; a journal_hash is committed crediting the honest operator's xonly_pk,
    //   proving the circuit does not enforce left == right.
}

// core integration test (cargo test -p clementine-core, regtest, no live Citrea)
// 1. Simulate a Citrea withdrawal registration for deposit_id X with bridge_amount, using
//    db.update_withdrawal_utxo_from_citrea_withdrawal to point to an attacker-controlled dust UTXO.
// 2. Attacker signs and broadcasts a Payout-shaped tx spending that UTXO, paying 1 sat to
//    withdrawal script + OP_RETURN(honest_operator.xonly_pk).
// 3. Mine to finality; run update_finalized_payouts (via BitcoinSyncer/StateManager).
// 4. Assert: db.get_payout_info_from_move_txid(...) returns Some(operator_xonly_pk == honest_operator),
//    despite the payout tx's user-facing output value (1 sat) != bridge_amount, proving the CUSTODY
//    equality (paid amount == registered bridge_amount) is not enforced before attribution.
```

### Citations

**File:** circuits-lib/src/bridge_circuit/mod.rs (L190-204)
```rust
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

**File:** circuits-lib/src/bridge_circuit/mod.rs (L686-692)
```rust
/// Retrieves the first output of a transaction that is an OP_RETURN script. Used in various
/// contexts to extract metadata or constants from transactions.
pub fn get_first_op_return_output(tx: &CircuitTransaction) -> Option<&TxOut> {
    tx.output
        .iter()
        .find(|out| out.script_pubkey.is_op_return())
}
```

**File:** core/src/verifier.rs (L1882-1914)
```rust
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

**File:** core/src/builder/transaction/operator_reimburse.rs (L407-436)
```rust
pub fn create_payout_txhandler(
    input_utxo: UTXO,
    output_txout: TxOut,
    operator_xonly_pk: XOnlyPublicKey,
    user_sig: taproot::Signature,
    _network: bitcoin::Network,
) -> Result<TxHandler<Signed>, BridgeError> {
    let txin = SpendableTxIn::new_partial(input_utxo.outpoint, input_utxo.txout);

    let output_txout = UnspentTxOut::from_partial(output_txout.clone());

    let op_return_txout = op_return_txout(PushBytesBuf::from(operator_xonly_pk.serialize()));

    let mut txhandler = TxHandlerBuilder::new(TransactionType::Payout)
        .with_version(NON_STANDARD_V3)
        .add_input(
            NormalSignatureKind::NotStored,
            txin,
            SpendPath::KeySpend,
            DEFAULT_SEQUENCE,
        )
        .add_output(output_txout)
        .add_output(UnspentTxOut::from_partial(anchor_output(
            NON_EPHEMERAL_ANCHOR_AMOUNT,
        )))
        .add_output(UnspentTxOut::from_partial(op_return_txout))
        .finalize();
    txhandler.set_p2tr_key_spend_witness(&user_sig, 0)?;
    txhandler.promote()
}
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

**File:** core/src/operator.rs (L839-915)
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

        let context = ContractContext::new_context_for_kickoff(
            kickoff_data,
            deposit_data,
            self.config.protocol_paramset(),
        );

        let signed_txs = create_and_sign_txs(
            self.db.clone(),
            &self.signer,
            self.config.clone(),
            context,
            Some(payout_tx_blockhash),
            Some(dbtx),
        )
        .await?;
```
