### Title
Unauthenticated OP_RETURN operator attribution allows third-party front-running of payout tx, crediting an arbitrary/wrong operator with reimbursement it never funded - ([File: core/src/tx_sender_queue.rs], [File: core/src/verifier.rs], [File: core/src/builder/transaction/operator_reimburse.rs])

### Summary
`add_tx_to_queue`'s `TransactionType::Payout` arm performs no validation of `signed_tx`'s output layout, and `update_finalized_payouts`/`get_first_op_return_output` blindly trust whatever OP_RETURN happens to be first in whichever transaction actually spent the withdrawal outpoint on-chain. Because the user's withdrawal signature is enforced to be `SIGHASH_SINGLE|ANYONECANPAY`, it commits only to input 0 and the matching output 0 — leaving every other output (including the OP_RETURN attribution slot) completely unauthenticated and attacker-forgeable in a competing, attacker-broadcast transaction.

### Finding Description
The binding the system relies on is: `withdrawals.payout_payer_operator_xonly_pk` for withdrawal index *i* == the xonly public key of the operator whose own BTC actually funded output 0 of the transaction spending `withdrawal_utxo[i]`.

`get_payout_txs_for_withdrawal_utxos` (`core/src/database/verifier.rs:170-196`) determines the "payout tx" for a withdrawal purely by which txid is recorded in `bitcoin_syncer_spent_utxos` as having spent `withdrawal_utxo_txid:withdrawal_utxo_vout` — i.e. whichever transaction wins the race to spend that specific outpoint on the Bitcoin network, regardless of who authored it. [1](#0-0) 

`update_finalized_payouts` then calls `get_first_op_return_output` on that tx and treats the first OP_RETURN's parsed xonly pubkey as "the operator who fronted this payout," with no cross-check against the actual funding operator or any signature over that OP_RETURN output. [2](#0-1) [3](#0-2) 

`add_tx_to_queue`'s `TransactionType::Payout` arm (`core/src/tx_sender_queue.rs:92-105`) confirms there is no check anywhere in this file (or transitively before it) that `signed_tx`'s outputs match `create_payout_txhandler`'s canonical layout (payout, anchor, OP_RETURN) — it just forwards to `insert_try_to_send` with `FeePayingType::RBF`. [4](#0-3) 

Root cause: the user's withdrawal signature is enforced server-side to be exactly `SinglePlusAnyoneCanPay` (`core/src/rpc/parser/operator.rs:174-187`), which by Bitcoin's sighash semantics commits *only* to input 0 and the single matching output 0 — never to any other input or output. Since (`input_outpoint`, `input_signature`, `output_script_pubkey`, `output_amount`) become public the moment the withdrawal is registered on Citrea (attacker capability: "call `withdraw` on the Citrea Bridge contract... choose the bytes of a withdrawal UTXO, a Schnorr signature and its sighash flag"), any unprivileged party can independently reconstruct a transaction that: spends the same withdrawal outpoint with the identical signature, keeps output 0 exactly as signed (satisfying `SECP.verify_schnorr` in `Operator::withdraw`, `core/src/operator.rs:630-637`, and the equivalent check in `create_payout_txhandler`), adds its own fee-paying inputs (allowed by ANYONECANPAY), and appends an arbitrary OP_RETURN output containing any xonly pubkey it chooses — then broadcast it with higher fee/priority to be the transaction that actually confirms first.

Because `get_first_op_return_output` only inspects whichever tx won that race, and `add_tx_to_queue`/verifier code never validates output count/order/content against `create_payout_txhandler`'s canonical shape, the attacker's forged OP_RETURN (pointing at any registered operator's xonly pk, including one that never funded anything) is recorded in `withdrawals.payout_payer_operator_xonly_pk`. `PayoutCheckerTask::run_once` (`core/src/task/payout_checker.rs:41-47`) then picks this up for whichever operator's key was named and automatically drives `handle_finalized_payout` → kickoff → `Reimburse` tx, crediting that operator with the deposit's escrowed BTC even though its own collateral/funds never paid the withdrawal.

No verifier/operator/aggregator guard intercepts this: `is_deposit_valid`, `is_profitable`, and `SECP.verify_schnorr` only validate output 0 against the user's request; none of them validate the tx's remaining outputs or who broadcast it, and `update_finalized_payouts`/`get_first_op_return_output` explicitly documents this as accepted behavior ("if an operator constructs the payout tx wrong").

### Impact Explanation
An arbitrary/named operator is reimbursed via the presigned Reimburse transaction flow (`create_reimburse_txhandler`, `core/src/builder/transaction/operator_reimburse.rs:341-385`) for a payout that operator never actually funded — this matches the explicitly listed Critical impact category "an operator reimbursed for a payout it never funded." The escrowed move-to-vault BTC for that specific deposit is spent to that operator regardless of who actually paid the withdrawing user. This is repeatable per withdrawal/deposit that uses the standard (non-optimistic) Payout path, since it only depends on attacker knowledge of public Citrea withdrawal-registration data and control over fee-bumping/mempool priority to win the outpoint-spending race.

### Likelihood Explanation
Preconditions: standard automated Payout flow is enabled (not optimistic payout, which uses a different musig2-signed keypath with `TapSighashType::Default` and no operator OP_RETURN); the attacker must observe the public (`withdrawal_id`, `input_signature`, `input_outpoint`, `output_script_pubkey`, `output_amount`) tuple, which becomes visible as soon as it is submitted to any operator/aggregator or the Citrea bridge contract call is included; the attacker must fund a competing transaction (only trivial fee cost, since output 0's value is fixed but paid by the attacker's own extra input) and win the mempool/fee race against the legitimate operator's broadcast. This is feasible with modest BTC (fee-bumping cost only) and is fully repeatable across withdrawals/operators.

### Recommendation
Bind the operator attribution cryptographically instead of relying on an unauthenticated OP_RETURN chosen by whoever wins the spend race:
- Require the operator's own signature (or a MuSig2/commitment scheme already tied to the deposit's presigned kickoff/round infrastructure) over the OP_RETURN payload, and verify it in `update_finalized_payouts`/`handle_finalized_payout` before crediting reimbursement.
- Alternatively/additionally, enforce in `add_tx_to_queue`'s `Payout` arm (and independently in `update_finalized_payouts`) that `signed_tx`'s output layout matches exactly what `create_payout_txhandler` produces (output 0 = payout, output 1 = anchor, output 2 = single OP_RETURN, no extras), rejecting any payout tx with additional or reordered outputs.
- Consider requiring the withdrawal signature to cover the OP_RETURN output too (e.g. via `SIGHASH_ALL` with a pre-agreed operator identity embedded in the withdrawal request itself, verified against the operator broadcasting it).

### Proof of Concept
```
cargo test -p clementine-core --test <e2e_test> -- --nocapture
```
Plan:
1. Reuse `generate_withdrawal_transaction_and_signature`/`sign_withdrawal_output` (`core/src/test/common/setup_utils.rs:499-543`) to produce a valid `SinglePlusAnyoneCanPay` signature over `(dust_utxo, output_txout)`.
2. Instead of calling `operator.withdraw(...)`, directly construct two competing transactions both spending `dust_utxo` with output 0 = signed `output_txout`:
   - Tx A ("attacker"): adds an extra funding input + OP_RETURN with an arbitrary/forged xonly pk (e.g. `operator_1_xonly_pk`, a different registered operator that never funded anything).
   - Tx B ("honest operator's real tx"): built via `create_payout_txhandler` with the real fronting operator's own xonly pk.
3. Broadcast Tx A first and mine it (simulating winning the race); do not broadcast Tx B.
4. Run the finalized-block handling path (`update_finalized_payouts`) and assert via `db.get_payout_txs_for_withdrawal_utxos` / `withdrawals.payout_payer_operator_xonly_pk` that the recorded operator xonly pk equals the attacker-forged pk (`operator_1_xonly_pk`), not the actual funder.
5. Assert that `PayoutCheckerTask`/`handle_finalized_payout` for `operator_1` subsequently creates a Kickoff/Reimburse tx for this deposit, proving operator_1 is credited despite never funding the payout — i.e. `LHS (recorded payer pk) == operator_1_xonly_pk` while `RHS (actual funder of output 0) == attacker`, violating the binding.

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

**File:** core/src/verifier.rs (L2312-2328)
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

            if operator_xonly_pk.is_none() {
                tracing::info!(
                    "No valid operator xonly pk found in payout tx {:?} OP_RETURN. Either it is an optimistic payout or the operator constructed the payout tx wrong",
                    payout_txid
                );
            }
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

**File:** core/src/tx_sender_queue.rs (L92-105)
```rust
            TransactionType::Challenge | TransactionType::Payout => {
                self.insert_try_to_send(
                    dbtx,
                    tx_metadata,
                    signed_tx,
                    FeePayingType::RBF,
                    rbf_info,
                    &[],
                    &[],
                    &[],
                    &[],
                )
                .await
            }
```
