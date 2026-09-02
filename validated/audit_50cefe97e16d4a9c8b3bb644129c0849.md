### Title
Unauthenticated OP_RETURN operator-attribution in payout tx lets any withdrawer credit an arbitrary operator with a payout it never funded - ([File: core/src/verifier.rs])

### Summary
`Verifier::update_finalized_payouts` derives `operator_xonly_pk` solely from the OP_RETURN bytes of the confirmed payout transaction, with no cryptographic binding to who actually constructed, broadcast, or paid fees for that transaction. Because `create_payout_txhandler` spends the withdrawal UTXO via a single key-path witness under `TapSighashType::SinglePlusAnyoneCanPay`, the OP_RETURN output (and the anchor output) are not covered by the signature, so anyone holding that signature — including the withdrawing user itself, who generates/possesses it for their own withdrawal — can assemble and broadcast the completed payout tx with an arbitrary operator's x-only pubkey in OP_RETURN.

### Finding Description
Binding claimed: `withdrawals.payout_payer_operator_xonly_pk` (set in `update_payout_txs_and_payer_operator_xonly_pk`) should equal the operator that actually fronted/funded the payout to the withdrawer.

Code path:
- `Verifier::update_finalized_payouts` [1](#0-0)  extracts `operator_xonly_pk` purely by calling `get_first_op_return_output` and `parse_op_return_data` on the mined payout transaction, with no check against who signed/funded the transaction.
- The payout transaction itself, `create_payout_txhandler`, has exactly one input, spent via `SpendPath::KeySpend` with only `user_sig` set as witness: [2](#0-1) . No operator signature is ever included in this transaction.
- The user's signature is required to be `TapSighashType::SinglePlusAnyoneCanPay`, enforced in `parse_withdrawal_sig_params`: [3](#0-2) . `SIGHASH_SINGLE|ANYONECANPAY` only commits the signature to input #0 and output #0 (the user payout output); it leaves the anchor output and, critically, the OP_RETURN output (which encodes `operator_xonly_pk`) completely unconstrained.
- Because the withdrawing user (an unprivileged attacker per the threat model) is the one who originates `in_signature`/`input_outpoint`/`output_script_pubkey` for their own withdrawal (`WithdrawParams`), they already legitimately possess a valid `SinglePlusAnyoneCanPay` signature for their own payout, without needing to go through any operator's gRPC. Using the same public transaction-building logic (`create_payout_txhandler`), the attacker can independently assemble the exact same transaction, but freely choose the OP_RETURN payload — any operator's serialized x-only pubkey, or none at all — and broadcast it themselves, fee-bumping the anchor output with their own wallet (CPFP), since no operator signature, key, or fee-source binding is present in the tx.
- `Verifier::update_finalized_payouts` then blindly records whatever pubkey is embedded as the payer, and `get_first_unhandled_payout_by_operator_xonly_pk` [4](#0-3)  lets that named operator's `PayoutCheckerTask` pick it up and call `handle_finalized_payout` [5](#0-4) , which proceeds to submit a kickoff and eventually claim the Reimburse transaction from the round's presigned N-of-N reimbursement path.
- `is_kickoff_malicious` [6](#0-5)  only checks that the operator embedding a kickoff matches the pubkey already recorded in the (attacker-controlled) OP_RETURN — it does not verify that pubkey's owner actually funded/broadcast the payout tx, so it does not catch this divergence.

Exploit flow: attacker calls Citrea's `withdraw()` for their own withdrawal, obtains/produces `in_signature` (SIGHASH_SINGLE|ANYONECANPAY) over their chosen `input_outpoint`/`output_script_pubkey`/`output_amount`, constructs the payout transaction themselves with an OP_RETURN naming operator B's x-only pubkey (B never involved), broadcasts and fee-bumps it with their own funds, and once it confirms and syncs, operator B is credited in `withdrawals.payout_payer_operator_xonly_pk` and can subsequently claim reimbursement it never earned.

### Impact Explanation
Operator B (or any named party) is credited as having fronted a payout it never funded, and can subsequently call `handle_finalized_payout`/submit a kickoff and be reimbursed from the bridge's N-of-N-controlled collateral/reimbursement path for value it did not spend — this is a direct case of "an operator reimbursed for a payout it never funded" (Critical). This is repeatable per withdrawal and can target any operator's registered x-only pubkey (public information), so the blast radius spans every withdrawal processed by the bridge and every currently registered operator.

### Likelihood Explanation
The attacker needs only: their own withdrawal request on Citrea (something any unprivileged user can do), the resulting SIGHASH_SINGLE|ANYONECANPAY signature (which they possess by construction, since it's their own withdrawal), and the ability to broadcast a Bitcoin transaction and pay its fee (cost is only the ordinary Bitcoin fee for a small transaction, no bridge collateral or special access required). No verifier, operator, or aggregator key material is needed, and the transaction-building logic is fully public. This makes the attack cheap, deterministic, and repeatable for every withdrawal the attacker initiates.

### Recommendation
Bind the OP_RETURN operator identity to an actual on-chain or cryptographic commitment from that operator (e.g., require the operator to co-sign the payout transaction, or require the fee-paying/CPFP input of the transaction to belong to the operator's registered collateral/wallet, and have `update_finalized_payouts` validate that binding), rather than trusting unauthenticated OP_RETURN bytes chosen by whichever party assembled/broadcast the transaction.

### Proof of Concept
```
cargo test — construct payout tx with mismatched OP_RETURN:
1. Set up e2e test env (deposit, withdrawal id, withdrawal_utxo owned by test "user" key).
2. Compute in_signature = SIGHASH_SINGLE|ANYONECANPAY signature over the payout tx spending
   withdrawal_utxo to output_txout, using the test's own key (simulating the attacker/withdrawer,
   who legitimately possesses this signature for their own withdrawal).
3. Call builder::transaction::create_payout_txhandler(withdrawal_utxo, output_txout,
   operator_B_xonly_pk, in_signature, network) directly (bypassing operator A's/B's gRPC entirely) —
   assert this succeeds and produces a fully valid, broadcastable transaction with no operator
   signature.
4. Broadcast this transaction using only the attacker's own wallet for fee funding (or bump the
   anchor output via CPFP) and mine it to finality.
5. Run verifier's update_finalized_payouts / block sync and assert
   db.get_payout_info_from_move_txid(...).0 == Some(operator_B_xonly_pk).
6. Assert db.get_first_unhandled_payout_by_operator_xonly_pk(operator_B_xonly_pk) returns this
   withdrawal, even though operator B never called withdraw(), never signed anything, and never
   paid the broadcast fee — demonstrating operator_xonly_pk in withdrawals is not bound to the
   actual funding/broadcasting party.
```

### Citations

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

**File:** core/src/verifier.rs (L2311-2321)
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
```

**File:** core/src/rpc/parser/operator.rs (L174-187)
```rust
    if input_signature.sighash_type == TapSighashType::Default {
        tracing::warn!(
            "Input signature for withdrawal {} has sighash type default, setting to SinglePlusAnyoneCanPay", params.withdrawal_id,
        );
        input_signature.sighash_type = TapSighashType::SinglePlusAnyoneCanPay;
    }

    // enforce sighash type here
    if input_signature.sighash_type != TapSighashType::SinglePlusAnyoneCanPay {
        return Err(Status::invalid_argument(format!(
            "Input signature has wrong sighash type, SinglePlusAnyoneCanPay expected, got {}",
            input_signature.sighash_type
        )));
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

**File:** core/src/task/payout_checker.rs (L41-79)
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
