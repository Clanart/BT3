## Title
Payout OP_RETURN operator attribution is not bound to who actually funded the payout - ([File: circuits-lib/src/bridge_circuit/mod.rs], [File: core/src/verifier.rs])

## Summary
`bridge_circuit` and `Verifier::update_finalized_payouts` both derive the "paying operator" solely from `get_first_op_return_output(&payout_spv.transaction)`, which only requires *some* OP_RETURN output containing a syntactically valid 32-byte x-only pubkey anywhere in the transaction. Neither the circuit nor the verifier checks that the operator named in that OP_RETURN is the party who actually funded the payment to the withdrawer, so the binding `operator_attributed_from_OP_RETURN == operator_who_funded_the_payout` is not enforced.

## Finding Description
The claimed binding is: `payout_payer_operator_xonly_pk (DB column, derived from OP_RETURN) == operator whose own funds paid output[0] to the withdrawer`.

Trace:
- The withdrawal's Bitcoin-side authorization is a single Schnorr signature (`in_signature`) over the withdrawer's own dust/claim UTXO, using a sighash flag the requester supplies (`WithdrawParams.input_signature` / proto comment claims `SinglePlusAnyoneCanPay`, but nothing on the L1 script level enforces this flag—key-path P2TR spends do not constrain sighash type). [1](#0-0) [2](#0-1) 
- `bridge_circuit` only verifies: HCP, watchtower work, SPV inclusion of `payout_spv.transaction`, LCP/state-root, and that the *input* txid/vout of `payout_spv.transaction` at `payout_input_index` matches the withdrawal outpoint from the storage proof. It never checks output[0]'s script/value against the withdrawal's committed `output_script_pubkey`/`output_amount`. [3](#0-2) 
- It then extracts the operator pubkey purely from the first OP_RETURN output found anywhere in the transaction: [4](#0-3) 
- `get_first_op_return_output` is a blind linear scan for any OP_RETURN script: [5](#0-4) 
- On the verifier side, `update_finalized_payouts` does the identical OP_RETURN scan/parse and writes the resulting pubkey into `withdrawals.payout_payer_operator_xonly_pk` with no cross-check against output amount/script or against who broadcast/funded the transaction: [6](#0-5) 
- Downstream, `is_kickoff_malicious` only checks that the *kicking-off operator's* pubkey matches this DB-recorded OP_RETURN pubkey and that the blockhash committed in the kickoff matches — it does not verify that the OP_RETURN operator actually spent their own money: [7](#0-6) 
- Separately, an operator's own automation (`get_first_unhandled_payout_by_operator_xonly_pk` / `PayoutCheckerTask::run_once`) reacts purely to `payout_payer_operator_xonly_pk` matching its own key, then calls `handle_finalized_payout` and eventually kicks off/claims reimbursement, without re-verifying the payout's output amount/script against the withdrawal terms: [8](#0-7) [9](#0-8) 

Because the withdrawer alone controls the signature and sighash flag for spending their own UTXO (an unprivileged capability explicitly granted per the audit rules), and the circuit/verifier only require *an* OP_RETURN with a well-formed 32-byte pubkey to be present, the withdrawer can construct and broadcast a payout transaction that: (1) spends the correct withdrawal outpoint/vout (satisfying the storage-proof/txid/vout check), (2) pays output[0] whatever amount the attacker likes (e.g., 0 sats to themselves), and (3) contains an OP_RETURN with an arbitrary, uninvolved operator B's real x-only pubkey. `bridge_circuit` will still accept this as a valid journal (no output-amount/script check exists), and `update_finalized_payouts` will record operator B as the payer.

## Impact Explanation
Once `payout_payer_operator_xonly_pk` is set to operator B, B's own `PayoutCheckerTask` (or manual `internal_finalized_payout`) will treat the withdrawal as already fronted by B and proceed to `handle_finalized_payout`/kickoff for reimbursement — crediting/reimbursing operator B for a payout it never funded, while the withdrawer received nothing (0 sats) and the true payer (if any) is not the one attributed. This matches the specified Critical category "an operator reimbursed for a payout it never funded." Because this relies only on knowledge of a public operator xonly pubkey and control over one's own withdrawal signature/sighash, it is repeatable across any withdrawal and any operator whose pubkey is public (all registered operators).

## Likelihood Explanation
Preconditions are minimal and match the stated unprivileged attacker capabilities exactly: the attacker must have an active withdrawal request (their own deposit/withdrawal), control of the withdrawal UTXO's private key/signature and sighash flag, and knowledge of a target operator's public x-only pubkey (public information). No verifier, operator, or aggregator privilege is required, and only normal Bitcoin fees are needed to broadcast the forged payout transaction. This is feasible without mainnet or live Citrea and can be demonstrated in a regtest/signet e2e test harness already present in the repo (`core/src/test/deposit_and_withdraw_e2e.rs` style).

## Recommendation
Bind the OP_RETURN-derived operator pubkey to actual fund provenance and to the withdrawal terms:
- In `bridge_circuit` (circuits-lib) and `Verifier::update_finalized_payouts`, validate that `payout_spv.transaction.output[0]` (or whichever index is defined as the user-payout output) matches the withdrawal's committed `output_script_pubkey`/`output_amount` from the storage proof/Citrea state, not just the spent outpoint/vout.
- Additionally require that the OP_RETURN output is at a fixed, well-defined index (e.g., always output index 2, immediately after payout+anchor) rather than "first OP_RETURN found anywhere," and reject transactions with OP_RETURN outputs elsewhere or with extra ambiguous outputs.
- Consider requiring cryptographic proof that the named operator supplied the additional funding input(s) (e.g., an input pubkey commitment consistent with kickoff-round funding), rather than trusting a bare pubkey dropped into OP_RETURN by an untrusted signer (the withdrawer).

## Proof of Concept
`cargo test` plan (regtest, no mainnet/live Citrea, extending `core/src/test/deposit_and_withdraw_e2e.rs` harness):
1. Set up deposit + withdrawal e2e as in existing tests, obtaining a withdrawal dust UTXO and its outpoint/vout registered on Citrea (`withdrawal_utxo`).
2. Instead of calling `operator.withdraw()`, directly construct a payout transaction with `bitcoin` primitives: input = withdrawer's own dust UTXO signed by the withdrawer's key with `TapSighashType::None | AnyoneCanPay` (or any type the withdrawer chooses); output[0] = P2TR to withdrawer's own address with `Amount::ZERO`; output[1] = anchor; output[2] = OP_RETURN containing Operator B's real x-only pubkey (an operator that never funded/signed anything for this tx).
3. Broadcast and mine this transaction to finality (`rpc.mine_blocks`), invoke `handle_finalized_block`/`update_finalized_payouts` on a verifier instance.
4. Assert `db.get_payout_info_from_move_txid(...).0 == Some(operator_B_xonly_pk)` even though Operator B's `withdraw()`/`create_payout_txhandler` was never called and Operator B never signed/funded the transaction — proving `payer_operator_idx (DB) != actual funder`.
5. Optionally continue: trigger `PayoutCheckerTask::run_once` for Operator B and assert it proceeds to `handle_finalized_payout`, demonstrating B would be queued for reimbursement despite the 0-sat payout to the real withdrawer.

### Citations

**File:** core/src/rpc/clementine.proto (L239-253)
```text
message WithdrawParams {
  // The ID of the withdrawal in Citrea
  uint32 withdrawal_id = 1;
  // User's [`bitcoin::sighash::TapSighashType::SinglePlusAnyoneCanPay`]
  // signature
  bytes input_signature = 2;
  // User's UTXO to claim the deposit
  Outpoint input_outpoint = 3;
  // The withdrawal output's script_pubkey (user's signature is only valid for
  // this pubkey)
  bytes output_script_pubkey = 4;
  // The withdrawal output's amount (user's signature is only valid for this
  // amount)
  uint64 output_amount = 5;
}
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

**File:** circuits-lib/src/bridge_circuit/mod.rs (L206-229)
```rust
    let first_op_return_output = get_first_op_return_output(&input.payout_spv.transaction)
        .expect("Payout transaction must have an OP_RETURN output");

    let round_txid = input.kickoff_tx.input[0]
        .previous_output
        .txid
        .to_byte_array();

    let kickoff_round_vout = input.kickoff_tx.input[0].previous_output.vout;

    let operator_xonlypk: [u8; 32] = parse_op_return_data(&first_op_return_output.script_pubkey)
        .expect("Invalid operator xonlypk")
        .try_into()
        .expect("Invalid xonlypk");

    let deposit_constant = deposit_constant(
        operator_xonlypk,
        input.watchtower_challenge_connector_start_idx,
        &input.all_tweaked_watchtower_pubkeys,
        *move_txid,
        round_txid,
        kickoff_round_vout,
        input.hcp.genesis_state_hash,
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

**File:** core/src/verifier.rs (L1882-1890)
```rust
        let Some(operator_xonly_pk) = operator_xonly_pk_opt else {
            tracing::warn!("No operator xonly pk found in payout tx OP_RETURN, assuming malicious");
            return Ok(true);
        };

        if operator_xonly_pk != kickoff_data.operator_xonly_pk {
            tracing::warn!("Operator xonly pk for the payout does not match with the kickoff_data");
            return Ok(true);
        }
```

**File:** core/src/verifier.rs (L2311-2343)
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
        }
```

**File:** core/src/database/verifier.rs (L282-313)
```rust
    pub async fn get_first_unhandled_payout_by_operator_xonly_pk(
        &self,
        tx: Option<DatabaseTransaction<'_>>,
        operator_xonly_pk: XOnlyPublicKey,
    ) -> Result<Option<(u32, Txid, BlockHash)>, BridgeError> {
        let query = sqlx::query_as::<_, (i32, Option<TxidDB>, Option<BlockHashDB>)>(
            "SELECT w.idx, w.move_to_vault_txid, w.payout_tx_blockhash
             FROM withdrawals w
             WHERE w.payout_txid IS NOT NULL
                AND w.is_payout_handled = FALSE
                AND w.payout_payer_operator_xonly_pk = $1
                ORDER BY w.idx ASC
             LIMIT 1",
        )
        .bind(XOnlyPublicKeyDB(operator_xonly_pk));

        let results = execute_query_with_tx!(self.connection, tx, query, fetch_optional)?;

        results
            .map(|(citrea_idx, move_to_vault_txid, payout_tx_blockhash)| {
                Ok((
                    u32::try_from(citrea_idx).wrap_err("Failed to convert citrea index to u32")?,
                    move_to_vault_txid
                        .expect("move_to_vault_txid Must be Some")
                        .0,
                    payout_tx_blockhash
                        .expect("payout_tx_blockhash Must be Some")
                        .0,
                ))
            })
            .transpose()
    }
```

**File:** core/src/task/payout_checker.rs (L39-106)
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

        // fetch and save the LCP for if we get challenged and need to provide proof of payout later
        let (_, payout_block_height) = self
            .operator
            .db
            .get_block_info_from_hash(Some(&mut dbtx), payout_tx_blockhash)
            .await?
            .ok_or_eyre("Couldn't find payout blockhash in bitcoin sync")?;

        let _ = self
            .operator
            .citrea_client
            .fetch_validate_and_store_lcp(
                payout_block_height as u64,
                citrea_idx,
                &self.operator.db,
                Some(&mut dbtx),
                self.operator.config.protocol_paramset(),
            )
            .await?;

        #[cfg(feature = "automation")]
        self.operator.end_round(&mut dbtx).await?;

        self.db
            .mark_payout_handled(Some(&mut dbtx), citrea_idx, kickoff_txid)
            .await?;
```
