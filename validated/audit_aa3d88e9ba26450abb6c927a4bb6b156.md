## Title
Anyone-can-spend P2A anchor allows fee-bump pinning of deadline-bound, pre-signed bridge transactions - (File: `crates/clementine-tx-sender/src/cpfp.rs`, `core/src/tx_sender_ext.rs`)

## Summary
Deadline-bound protocol transactions (`ChallengeTimeout`, `OperatorChallengeNack`, `WatchtowerChallenge`, `LatestBlockhashTimeout`, `Payout`/optimistic payout) are built as BIP-431 v3 ("TRUC") transactions whose only fee-bumping mechanism is a `P2A` anchor output that is explicitly anyone-can-spend [1](#0-0) . Because these transactions carry pre-signed N-of-N/committee signatures, they cannot be resigned or replaced via ordinary RBF, so CPFP through that anchor is the *only* fee-bump path, and `debug_tx` in `core/src/tx_sender_ext.rs` merely reports state from this same pipeline rather than adding any additional bumping mechanism.

## Finding Description
The binding claimed by the invariant is: `for every deadline-bound tx T with anchor output A, spendable_by(A) == { TxSender-only-child }`. In reality `spendable_by(A) == { anyone }`, because `anchor_output`/`is_p2a_anchor` produce the fixed anyone-can-spend script `51024e73` (`OP_1 <0x4e73>`) [1](#0-0) [2](#0-1) .

These bridge transactions are built with `.with_version(NON_STANDARD_V3)` (BIP-431 TRUC) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) . Bitcoin Core's TRUC mempool policy caps a v3 transaction to a single unconfirmed descendant. The tx-sender's CPFP module explicitly documents this constraint: "a third transaction can't be put into the package. So, a so-called 'fee payer' transaction must be sent and confirmed before the CPFP package is sent." [8](#0-7) 

Any unprivileged actor who observes the broadcast parent transaction (once it enters the mempool with its anchor output visible) can craft and broadcast their own low-fee transaction spending that same anchor output, since it is anyone-can-spend. If that attacker transaction is accepted into the mempool first, it occupies the parent's single allowed TRUC descendant slot. When `send_cpfp_tx`/`create_package` later attempts to build and submit the legitimate child transaction spending the same anchor output (`find_p2a_vout` → `create_child_tx` → `build_and_sign_child_tx`) [9](#0-8) [10](#0-9) , the outpoint is already spent by the attacker's transaction (a conflicting/already-spent input), and/or the parent already has its one permitted descendant, so the legitimate CPFP child is rejected by the node. Because these presigned transactions cannot be RBF'd (their signatures are fixed by other parties and would need re-signing to change fee/outputs), the parent transaction's effective fee rate is now pinned at its original value with no possible bump.

`debug_tx` only surfaces database state (`fee_payer_utxos`, `submission_errors`, `current_state`) about this same pipeline [11](#0-10) ; it does not detect or work around anchor-output theft, so an operator/verifier running `debug_tx` would simply observe repeated CPFP failures with no remedy.

The existing "fee payer" confirmation-then-package flow (`create_fee_payer_utxo`, `get_confirmed_fee_payer_utxos`, `create_package`) assumes the anchor output remains available for the honest child at package-submission time, and does not defend against a third party pre-spending it. No guard such as `Verifier::is_kickoff_malicious`, `SPV::verify`, or a database uniqueness constraint intervenes here since this is purely a Bitcoin mempool/relay-layer race, outside those protocol-level checks.

## Impact Explanation
If `ChallengeTimeout`, `OperatorChallengeNack`, `LatestBlockhashTimeout`, `WatchtowerChallenge`, or the `Payout`/optimistic payout transaction cannot be fee-bumped to confirm before its associated timelock/deadline matures, the opposing branch of the presigned transaction graph (e.g., the challenge succeeding instead of `ChallengeTimeout`, or a withdrawal timing out) can win by default. This can result in an honest operator failing to be reimbursed in time, an honest party's collateral being burned via the timeout path, or a user withdrawal never settling — matching the "High: a deadline-bound challenge/disprove/timeout transaction made unconfirmable by attacker-shaped chain data" category. The attack is repeatable per kickoff/deposit/withdrawal instance since every occurrence of these transaction types shares the same anyone-can-spend anchor pattern.

## Likelihood Explanation
The attacker only needs to observe a broadcast parent transaction in the public mempool and race a minimal-fee spend of its anchor output — no special access, keys, or protocol role is required, matching the stated unprivileged attacker capabilities (broadcast transactions, pay fees). Cost is limited to a single low-value transaction fee. Success requires winning a mempool propagation race, which is feasible for a motivated attacker monitoring the mempool for these transaction types, and is not prevented by any code path found in this repo (I was unable to locate any check that rejects/handles an already-spent anchor by re-picking a different fee-bump path within `cpfp.rs`, `tx_sender_ext.rs`, or the transaction sender's confirmation loop).

## Recommendation
Restrict the anchor output's spendability so only the legitimate `TxSender` signer (or a covenant enforcing "must pay at least X sat/vB to this address") can spend it, or use a non-P2A-style anchor combined with an alternate always-available fee-bump path (e.g., allow the parent's own outputs to be RBF-bumped via presigned SIGHASH_ANYONECANPAY-friendly construction, or add a fallback CPFP anchor slot that reissues fresh anchors when the primary one is stolen). At minimum, detect when the anchor outpoint is already spent by a non-tx-sender transaction and immediately create a fresh replacement transaction (if presigned data allows) rather than silently stalling.

## Proof of Concept
```
// cargo test plan (regtest, no mainnet/live Citrea):
// 1. Build a NON_STANDARD_V3 tx `T` with a P2A anchor output (e.g. create_challenge_timeout_txhandler)
//    and broadcast it, seeding it via insert_try_to_send with FeePayingType::CPFP.
// 2. Before the TxSender's own send_cpfp_tx executes, from a second wallet (no privileged role)
//    construct and broadcast tx `S` spending T's anchor outpoint at minimal relay fee.
// 3. Assert `S` is accepted into the mempool (confirms anyone-can-spend script 51024e73).
// 4. Run the TxSender's fee-bump loop / call debug_tx(try_to_send_id) and assert that
//    the returned TxDebugInfo shows a submission_error / current_state indicating the
//    CPFP child could not be created or broadcast (parent's anchor already spent).
// 5. Bind the invariant: assert_eq!(effective_fee_rate_after_bump, effective_fee_rate_before_bump)
//    i.e., the "before" and "after" sides of the fee-bumpability equality no longer match,
//    demonstrating T is pinned at its original fee rate past the fee-bump attempt window.
```

### Citations

**File:** core/src/builder/transaction/mod.rs (L252-262)
```rust
/// Creates a P2A (anchor) output for Child Pays For Parent (CPFP) fee bumping.
///
/// # Returns
///
/// A [`TxOut`] with a statically defined script and value, used as an anchor output in protocol transactions. The TxOut is spendable by anyone.
pub fn anchor_output(amount: Amount) -> TxOut {
    TxOut {
        value: amount,
        script_pubkey: ScriptBuf::from_hex("51024e73").expect("statically valid script"),
    }
}
```

**File:** crates/clementine-utils/src/address.rs (L111-114)
```rust
/// Helper function to check if a TxOut is a P2A anchor.
pub fn is_p2a_anchor(output: &TxOut) -> bool {
    output.script_pubkey == ScriptBuf::from_hex("51024e73").expect("valid anchor script")
}
```

**File:** core/src/builder/transaction/challenge.rs (L54-55)
```rust
    let mut builder = TxHandlerBuilder::new(TransactionType::WatchtowerChallenge(watchtower_idx))
        .with_version(NON_STANDARD_V3)
```

**File:** core/src/builder/transaction/challenge.rs (L171-172)
```rust
        TxHandlerBuilder::new(TransactionType::OperatorChallengeNack(watchtower_idx))
            .with_version(NON_STANDARD_V3)
```

**File:** core/src/builder/transaction/challenge.rs (L371-372)
```rust
    Ok(TxHandlerBuilder::new(TransactionType::ChallengeTimeout)
        .with_version(NON_STANDARD_V3)
```

**File:** core/src/builder/transaction/operator_assert.rs (L80-81)
```rust
        TxHandlerBuilder::new(TransactionType::LatestBlockhashTimeout)
            .with_version(NON_STANDARD_V3)
```

**File:** core/src/builder/transaction/operator_reimburse.rs (L471-472)
```rust
    let mut txhandler = TxHandlerBuilder::new(TransactionType::Payout)
        .with_version(NON_STANDARD_V3)
```

**File:** crates/clementine-tx-sender/src/cpfp.rs (L12-18)
```rust
//! ### Fee Payer Transactions/UTXOs
//!
//! Child transaction needs to spend an UTXO for the fees. But because of the
//! TRUC rules (https://github.com/bitcoin/bips/blob/master/bip-0431.mediawiki#specification),
//! a third transaction can't be put into the package. So, a so called "fee
//! payer" transaction must be send and confirmed before the CPFP package is
//! send.
```

**File:** crates/clementine-tx-sender/src/cpfp.rs (L288-327)
```rust
    async fn create_child_tx(
        &self,
        p2a_anchor: OutPoint,
        anchor_sat: Amount,
        fee_payer_utxos: Vec<crate::SpendableUtxo>,
        parent_tx_size: Weight,
        fee_rate: FeeRateKvb,
    ) -> Result<Transaction> {
        let required_fee = Self::calculate_required_fee(
            parent_tx_size,
            fee_payer_utxos.len(),
            fee_rate,
            FeePayingType::CPFP,
        )?;

        let change_address = self
            .rpc
            .get_new_wallet_address()
            .await
            .wrap_err("Failed to get new wallet address")?;

        let total_fee_payer_amount = fee_payer_utxos
            .iter()
            .map(|utxo| utxo.txout.value)
            .sum::<Amount>()
            + anchor_sat;

        if change_address.script_pubkey().minimal_non_dust() + required_fee > total_fee_payer_amount
        {
            return Err(SendTxError::InsufficientFeePayerAmount);
        }

        self.build_and_sign_child_tx(
            p2a_anchor,
            anchor_sat,
            fee_payer_utxos,
            change_address,
            required_fee,
        )
    }
```

**File:** crates/clementine-tx-sender/src/cpfp.rs (L338-363)
```rust
    async fn create_package(
        &self,
        tx: Transaction,
        fee_rate: FeeRateKvb,
        fee_payer_utxos: Vec<crate::SpendableUtxo>,
    ) -> Result<Vec<Transaction>> {
        let txid = tx.compute_txid();
        let p2a_vout = self
            .find_p2a_vout(&tx)
            .map_err(|e: BridgeError| SendTxError::Other(e.into()))?;
        let anchor_sat = tx.output[p2a_vout].value;

        let child_tx = self
            .create_child_tx(
                OutPoint {
                    txid,
                    vout: p2a_vout as u32,
                },
                anchor_sat,
                fee_payer_utxos,
                tx.weight(),
                fee_rate,
            )
            .await?;

        Ok(vec![tx, child_tx])
```

**File:** core/src/tx_sender_ext.rs (L27-103)
```rust
    async fn debug_tx(&self, id: u32) -> Result<TxDebugInfo, BridgeError> {
        use crate::rpc::clementine::{TxDebugFeePayerUtxo, TxDebugInfo, TxDebugSubmissionError};

        let (tx_metadata, tx, fee_paying_type, seen_at_height, _) =
            self.db.get_try_to_send_tx(None, id).await.map_to_eyre()?;

        let submission_errors = self
            .db
            .get_tx_debug_submission_errors(None, id)
            .await
            .map_to_eyre()?;

        let submission_errors = submission_errors
            .into_iter()
            .map(|(error_message, timestamp)| TxDebugSubmissionError {
                error_message,
                timestamp,
            })
            .collect();

        let current_state = self.db.get_tx_debug_info(None, id).await.map_to_eyre()?;

        let fee_payer_utxos = self
            .db
            .get_tx_debug_fee_payer_utxos(None, id)
            .await
            .map_to_eyre()?;

        let fee_payer_utxos = fee_payer_utxos
            .into_iter()
            .map(|(txid, vout, amount, confirmed)| TxDebugFeePayerUtxo {
                txid: Some(txid.into()),
                vout,
                amount: amount.to_sat(),
                confirmed,
            })
            .collect::<Vec<_>>();

        let txid = match fee_paying_type {
            FeePayingType::CPFP | FeePayingType::NoFunding => tx.compute_txid(),
            FeePayingType::RBF | FeePayingType::RbfWtxidGrind => self
                .db
                .get_last_rbf_txid(None, id)
                .await
                .map_to_eyre()?
                .unwrap_or(bitcoin::Txid::all_zeros()),
        };
        let debug_info = TxDebugInfo {
            id,
            is_active: seen_at_height.is_none(),
            current_state: current_state.unwrap_or_else(|| "unknown".to_string()),
            submission_errors,
            created_at: "".to_string(),
            txid: Some(txid.into()),
            fee_paying_type: format!("{fee_paying_type:?}"),
            fee_payer_utxos_count: fee_payer_utxos.len() as u32,
            fee_payer_utxos_confirmed_count: fee_payer_utxos
                .iter()
                .filter(|utxo| utxo.confirmed)
                .count() as u32,
            fee_payer_utxos,
            raw_tx: bitcoin::consensus::serialize(&tx),
            metadata: tx_metadata.map(|metadata| rpc::clementine::TxMetadata {
                deposit_outpoint: metadata.deposit_outpoint.map(Into::into),
                operator_xonly_pk: metadata.operator_xonly_pk.map(Into::into),

                round_idx: metadata
                    .round_idx
                    .unwrap_or(RoundIndex::Round(0))
                    .to_index() as u32,
                kickoff_idx: metadata.kickoff_idx.unwrap_or(0),
                tx_type: Some(metadata.tx_type.into()),
            }),
        };

        Ok(debug_info)
    }
```
