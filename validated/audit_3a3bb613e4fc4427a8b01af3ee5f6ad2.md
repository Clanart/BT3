### Title
Operator-payer attribution for a payout transaction is taken from an unauthenticated `OP_RETURN` output rather than proof that the credited operator actually funded it - ([File: core/src/verifier.rs])

### Summary
`update_finalized_payouts` attributes "who paid a withdrawal" purely by reading an `OP_RETURN` output embedded in the mined payout transaction, with no check that the named operator actually supplied the funds (inputs) of that transaction. Any unprivileged party who can construct/broadcast a Bitcoin transaction satisfying a withdrawal UTXO can embed an arbitrary operator's x-only pubkey in the `OP_RETURN`, causing the bridge's off-chain bookkeeping (and that operator's own automation) to believe the named operator fronted the withdrawal, exactly analogous to the ZetaChain report where an event's *emitter* was never checked, letting an attacker forge attribution data trusted downstream.

### Finding Description
When a finalized L1 block is processed, `update_finalized_payouts` locates the transaction that satisfies a registered withdrawal UTXO and extracts the payer attribution solely from the first `OP_RETURN` output's data, decoding it as an x-only public key with no signature or input-ownership check: [1](#0-0) 

This `operator_xonly_pk` is stored as the authoritative "payer" for the withdrawal: [2](#0-1) 

Downstream, an operator's own automation polls for payouts attributed to *its own* key and, upon finding one, immediately starts the reimbursement pipeline (`handle_finalized_payout`, ending the round, marking the payout handled) purely based on this DB attribution: [3](#0-2) 

The only consistency check performed later, in `send_asserts`, compares the DB-recorded payer key against the *current kickoff's* operator key - it does not verify that the recorded payer's own funds (inputs) were spent in the payout transaction: [4](#0-3) 

Because the `OP_RETURN` script is just plaintext data that anyone constructing the payout transaction can set, the binding "operator credited == party that funded the payout" is not enforced anywhere in this pipeline. The attribution is accepted purely from self-declared data in the transaction, the same class of bug as trusting an `onReceive`/`onRevert` callback's emitted `ZetaReceived` event without checking the emitting contract address.

### Impact Explanation
Because the operator's own automated `PayoutCheckerTask` treats DB attribution as ground truth and triggers the reimbursement kickoff/round-ending flow (`handle_finalized_payout`, `end_round`, `mark_payout_handled`) without any check that the operator's inputs paid for the transaction, an attacker who can get a payout-satisfying transaction mined with a forged `OP_RETURN` naming a victim operator can:
- Cause that operator's node to treat a payment it never made as its own, driving it into the kickoff/reimbursement pipeline for a payout it did not fund, and/or
- Prevent the transaction that truly fronted the withdrawal from ever being credited to its real, honest payer (since only one attribution per withdrawal is recorded), potentially leaving the honest operator that actually paid permanently unable to be reimbursed.

Both outcomes map onto the Critical impact categories "an operator reimbursed for a payout it never funded" and "an honest operator permanently unable to be reimbursed."

### Likelihood Explanation
Constructing a Bitcoin transaction with an attacker-chosen `OP_RETURN` payload is trivial and requires no special role, key, or protocol privilege - it only requires the ability to broadcast a transaction that satisfies the withdrawal's outpoint/amount requirements, which is inherent to how withdrawals work today (the payer already must fund and broadcast this transaction). The missing check (that the `OP_RETURN` key corresponds to whoever actually funded the transaction's inputs) means the vulnerability is directly reachable by any party capable of paying out a withdrawal, without needing to compromise any verifier, aggregator, or operator key.

### Recommendation
Do not treat the `OP_RETURN` payload as authoritative proof of payer identity. Bind payer attribution to a value that is cryptographically tied to the operator, e.g.:
- Require the payout transaction's inputs to be verifiably spent from that operator's registered/committed UTXO set (as already enforced elsewhere in the kickoff/round transaction chain), or
- Require an operator signature over the payout transaction (or its txid) that is checked before crediting attribution, instead of parsing unauthenticated `OP_RETURN` bytes.
At minimum, `update_finalized_payouts` and `send_asserts` should cross-check that the operator claiming/being credited for the payout is the one whose kickoff/round transaction inputs actually financed the corresponding payout output, not merely that an `OP_RETURN` field says so.

### Proof of Concept
1. A withdrawal UTXO is registered on Citrea for recipient `R` with amount `A`.
2. Any party (not necessarily an operator) constructs and broadcasts a Bitcoin transaction that spends the corresponding funds to `R` for amount `A`, but sets the transaction's `OP_RETURN` output to the x-only public key of a victim operator `V` who never signed or funded this transaction.
3. Once finalized, `update_finalized_payouts` (`core/src/verifier.rs:2311-2350`) decodes the `OP_RETURN` data and records `V` as the payer in the DB.
4. `V`'s own `PayoutCheckerTask` (`core/src/task/payout_checker.rs:39-79`) picks up this record via `get_first_unhandled_payout_by_operator_xonly_pk` and begins the reimbursement pipeline (`handle_finalized_payout`, `end_round`) for a payment `V` never made.
5. The `send_asserts` check (`core/src/operator.rs:1284-1295`) only verifies DB-payer == kickoff-operator, which is satisfied here since both reference `V`; it never checks that `V`'s own funds paid the withdrawal.

### Citations

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

**File:** core/src/verifier.rs (L2337-2350)
```rust
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

**File:** core/src/operator.rs (L1284-1295)
```rust
        let payout_op_xonly_pk = payout_op_xonly_pk_opt.ok_or_eyre(format!(
            "Payout operator xonly pk not found in payout info DB while sending asserts for deposit move txid: {move_txid}"
        ))?;

        tracing::info!("Sending asserts for deposit_idx: {deposit_idx:?}");

        if payout_op_xonly_pk != kickoff_data.operator_xonly_pk {
            return Err(eyre::eyre!(
                "Payout operator xonly pk does not match kickoff operator xonly pk in send_asserts"
            )
            .into());
        }
```
