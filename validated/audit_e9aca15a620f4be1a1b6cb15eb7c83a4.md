### Title
`update_finalized_payouts` records payout_txid/payer without validating amount or destination against Citrea's withdrawal record - (File: core/src/verifier.rs)

### Summary
`Verifier::update_finalized_payouts` (core/src/verifier.rs:2283-2353), invoked from `handle_finalized_block` (core/src/verifier.rs:3090), identifies "the payout" for a withdrawal solely by which transaction spends the tracked `withdrawal_utxo` outpoint, and only inspects the OP_RETURN output for a parseable xonly-pubkey. It never re-derives or checks the `bridge_amount`/destination script against Citrea's `Bridge` contract record for that withdrawal id before writing `payout_txid` and `payout_payer_operator_xonly_pk` into the database.

### Finding Description
The binding that should hold is: `db.payout_txid(withdrawal_i).amount_and_destination == Bridge.withdrawals[i].(bridge_amount, destination)`.

Tracing the code: `update_finalized_payouts` fetches `payout_txids` (candidate spends of `withdrawal_utxo` for each index) via `self.db.get_payout_txs_for_withdrawal_utxos(...)` (core/src/verifier.rs:2289-2292), locates that txid in the finalized block cache, then does:
```
let op_return_output = get_first_op_return_output(&circuit_payout_tx);
let operator_xonly_pk = op_return_output
    .and_then(|output| parse_op_return_data(&output.script_pubkey))
    .and_then(|bytes| XOnlyPublicKey::from_slice(bytes).ok());
``` [1](#0-0) 
and unconditionally commits the result via `update_payout_txs_and_payer_operator_xonly_pk` [2](#0-1) . At no point in this function are the tx's output `value` or `script_pubkey` compared to the `bridge_amount`/destination that Citrea's `Bridge` contract recorded for withdrawal `idx`. Since `withdrawal_utxo` is populated earlier from `collect_withdrawal_utxos` (Citrea) purely as an outpoint (core/src/verifier.rs:2224-2262), any transaction that spends that exact outpoint — including a wrong-amount/wrong-destination transaction crafted by an unprivileged attacker with any OP_RETURN xonly-pubkey — will be accepted as the canonical payout for that withdrawal index once it confirms in a finalized block.

### Impact Explanation
Once a bogus spend of `withdrawal_utxo` is recorded as `payout_txid`, the withdrawal UTXO is spent on-chain, so the real (fronting) operator's subsequent payout transaction can no longer spend it and can never be recognized/attributed by this bookkeeping path — matching "an honest operator permanently unable to be reimbursed." This is Critical-category impact and is repeatable for any withdrawal index the attacker can front-run with a higher-fee transaction, since the only precondition is broadcasting a standard Bitcoin transaction spending the known `withdrawal_utxo` outpoint with any OP_RETURN payload.

### Likelihood Explanation
No special privilege is required: the attacker only needs to observe the `withdrawal_utxo` outpoint (public, derived from Citrea state) and broadcast a competing spend with a higher fee before the legitimate operator's payout confirms. Cost is limited to Bitcoin transaction fees and outputting `bridge_amount`-equivalent value to an attacker-controlled address (which the attacker may recover, since it's their own tx), making this cheap and repeatable across withdrawals.

### Recommendation
In `update_finalized_payouts`, before accepting a candidate transaction as the canonical payout, validate that its outputs actually pay at least `bridge_amount` to the destination script Citrea's `Bridge` contract recorded for that withdrawal index (fetched from the same source used by `collect_withdrawal_utxos`/citrea client), rejecting and continuing to scan for a subsequent qualifying spend if the amount/destination check fails.

### Proof of Concept
1. In a `cargo test` harness (e.g. extending `core/src/test/deposit_and_withdraw_e2e.rs`-style setup but authored as a new, non-mock reachable-path test), set up a withdrawal with a known `bridge_amount` and destination address recorded via the Citrea client mock/interface used by `update_citrea_deposit_and_withdrawals`.
2. Broadcast and confirm a transaction spending `withdrawal_utxo` that sends `bridge_amount` to an attacker-controlled address (not the registered destination), including a syntactically valid OP_RETURN xonly-pubkey.
3. Call `handle_finalized_block` for that block.
4. Assert: `db.get_payout_txs_and_payer_operator_xonly_pk` (or equivalent) now returns this bogus transaction's txid as `payout_txid` for the withdrawal index, with no error or rejection, and separately assert that the transaction's output amount/destination differs from the recorded `bridge_amount`/destination — demonstrating the equality binding is violated with no check performed.

### Citations

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

**File:** core/src/verifier.rs (L2345-2350)
```rust
        self.db
            .update_payout_txs_and_payer_operator_xonly_pk(
                Some(dbtx),
                payout_txs_and_payer_operator_idx,
            )
            .await?;
```
