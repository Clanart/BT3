### Title
Payout attribution forged via unauthenticated OP_RETURN bytes lets any withdrawer credit an uninvolved operator's kickoff/Reimburse for a payout it never funded - ([File: core/src/verifier.rs], [File: core/src/task/payout_checker.rs], [File: core/src/operator.rs])

### Summary
Payout attribution (which operator "fronted" a withdrawal) is derived purely from raw OP_RETURN bytes in whichever transaction happens to spend the committed withdrawal UTXO, with no cryptographic binding of that OP_RETURN to the credited operator's key, wallet, or actual value contribution. Because the payout input is signed by the withdrawer with `SinglePlusAnyoneCanPay`, anyone (the withdrawer themselves) can complete the payout transaction using their own funds while stamping an arbitrary operator's xonly public key into the OP_RETURN, causing that operator's `PayoutCheckerTask` to auto-consume a kickoff connector and drive `Reimburse` for a payout it never funded.

### Finding Description
The broken equality: `payout_payer_operator_xonly_pk (DB attribution)` should equal `xonly_pk of the party whose funds actually paid the withdrawal output`. In practice it only equals "whatever bytes appear in the first OP_RETURN output of the transaction that spends the tracked withdrawal UTXO."

Path:
1. Verifier's `update_finalized_payouts` (`core/src/verifier.rs:2283`) finds the transaction that spends a tracked withdrawal UTXO, extracts the first OP_RETURN output via `get_first_op_return_output`/`parse_op_return_data`, and interprets those raw bytes as an `XOnlyPublicKey` with zero signature check: ` [1](#0-0) `. This is persisted via `update_payout_txs_and_payer_operator_xonly_pk` (`core/src/database/verifier.rs:198`).
2. `Verifier::is_kickoff_malicious` (`core/src/verifier.rs:1859`) only checks that this stored `operator_xonly_pk` equals the kickoff's operator and that the committed blockhash matches - it never checks that the credited operator's wallet funded the payout: ` [2](#0-1) `.
3. `PayoutCheckerTask::run_once` (`core/src/task/payout_checker.rs:39`) polls `get_first_unhandled_payout_by_operator_xonly_pk` for the operator's own key and, upon a match, automatically calls `Operator::handle_finalized_payout` (`core/src/operator.rs:839`), which only checks `deposit_outpoint` and `payout_tx_blockhash` before consuming `get_unused_and_signed_kickoff_connector` and building the real kickoff/reimbursement chain: ` [3](#0-2) `.
4. The intended design (`Operator::withdraw`, `core/src/operator.rs:560-626`) hardcodes the OP_RETURN to `self.signer.xonly_public_key` when the *legitimate* operator builds the payout via gRPC: ` [4](#0-3) `. But `create_payout_txhandler` (`core/src/builder/transaction/operator_reimburse.rs:407`) shows the OP_RETURN is just `PushBytesBuf::from(operator_xonly_pk.serialize())` - unsigned public bytes, not a proof of funding: ` [5](#0-4) `. The user's signature (`SinglePlusAnyoneCanPay`) only commits to their own input and the payout output, not to any other input/output including OP_RETURN, so anyone completing the transaction can set the OP_RETURN bytes freely.

Exploit: attacker deposits and calls `withdraw` on the Citrea Bridge contract with self-chosen withdrawal UTXO/output. They sign the input themselves with `SinglePlusAnyoneCanPay`. Instead of calling any operator's gRPC `withdraw`, they directly construct and broadcast a Bitcoin transaction spending the withdrawal UTXO, adding their own funding input(s) to cover the required output amount/fee, and set the OP_RETURN to victim operator0's public xonly key (public information, no signature needed). Verifiers' chain sync attributes this payout to operator0. Operator0's own `PayoutCheckerTask` then automatically calls `handle_finalized_payout`, consuming operator0's kickoff connector and proceeding toward `Reimburse`, crediting operator0's bridge_amount collateral release for a payout operator0 never funded (`is_kickoff_malicious` sees a matching attribution and does not flag it malicious).

### Impact Explanation
Vault BTC (`bridge_amount`) becomes releasable via the presigned `Reimburse` transaction (`core/src/builder/transaction/operator_reimburse.rs:341-385`) to operator0's `reimburse_addr`, even though operator0 contributed no capital to the actual payout - matching the explicitly listed Critical category "an operator reimbursed for a payout it never funded." It also consumes one of operator0's finite per-round kickoff connectors for a deposit operator0 never intended to service, without any operator0 action or private key use. Repeatable per deposit/withdrawal that any attacker chooses to self-front, and against any operator whose public xonly key the attacker copies into the OP_RETURN.

### Likelihood Explanation
Fully unprivileged: the attacker only needs to be a normal depositor/withdrawer (deposit BTC, call `withdraw` on Citrea, sign with `SinglePlusAnyoneCanPay`, fund and broadcast a raw Bitcoin transaction). No aggregator, verifier, or operator credential is required, and no key compromise of operator0 is needed since the OP_RETURN field is public bytes, not a signature. Cost is limited to funding their own withdrawal payout (which they must pay in some form) plus standard Bitcoin fees. Fully repeatable across deposits and against any operator whose xonly public key is known (all operator keys are public/registered).

### Recommendation
Bind payout attribution to an authenticated commitment from the credited operator (e.g., require the OP_RETURN payload to be signed by, or otherwise cryptographically tied to, the operator's key/wallet that actually funded the additional inputs), or verify that the transaction's non-signed inputs are spendable/owned by the attributed operator's registered wallet before writing `payout_payer_operator_xonly_pk` and before `handle_finalized_payout`/`is_kickoff_malicious` treat the attribution as valid.

### Proof of Concept
`cargo test` plan (bitcoind regtest, no mainnet/live Citrea, using existing e2e test harness in `core/src/test/deposit_and_withdraw_e2e.rs` / `manual_reimbursement.rs` patterns):
1. Set up actors (aggregator, N verifiers, ≥2 operators) and perform a normal deposit to get `deposit_outpoint`/`move_txid`.
2. Simulate the Citrea withdrawal registration for that deposit/`withdrawal_utxo` as in existing helpers (`update_withdrawal_utxo_from_citrea_withdrawal`), using a signature the test constructs itself (not going through any operator's `withdraw` RPC) with `SinglePlusAnyoneCanPay`.
3. Construct a raw payout transaction: input = withdrawal UTXO (spent with the test-controlled signature) + an additional funding input from a non-operator test wallet; output = payout to a test-controlled withdrawer address matching amount/script; OP_RETURN = operator0's `xonly_public_key` (copied from `actors.get_operator_by_index(0).config.signer` public key, no private key access needed). Broadcast and mine to finality.
4. Assert (equality check #1, before): confirm no operator wallet controlled/funded this transaction's non-signed inputs (all inputs traced to the test's own non-operator wallet).
5. Let bitcoin sync/`update_finalized_payouts` run; assert `db.get_payout_info_from_move_txid` returns `payout_payer_operator_xonly_pk == operator0_xonly_pk`.
6. Assert `PayoutCheckerTask::run_once` (or direct call to `operator0.handle_finalized_payout(dbtx, deposit_outpoint, payout_tx_blockhash)`) returns `Ok(kickoff_txid)` successfully (equality check #2, after: attribution says operator0 fronted it, but no operator0 funds were spent) - proving the divergence: `is_kickoff_malicious`/`handle_finalized_payout` never rejects it despite operator0 having zero economic input into the payout transaction.

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

**File:** core/src/verifier.rs (L2312-2321)
```rust
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

**File:** core/src/operator.rs (L620-626)
```rust
        let payout_txhandler = builder::transaction::create_payout_txhandler(
            input_utxo,
            output_txout,
            self.signer.xonly_public_key,
            in_signature,
            self.config.protocol_paramset().network,
        )?;
```

**File:** core/src/operator.rs (L851-860)
```rust
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

**File:** core/src/builder/transaction/operator_reimburse.rs (L416-418)
```rust
    let output_txout = UnspentTxOut::from_partial(output_txout.clone());

    let op_return_txout = op_return_txout(PushBytesBuf::from(operator_xonly_pk.serialize()));
```
