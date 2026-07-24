### Title
Wrong Index Used in Deposit Lookup Inside `sign_optimistic_payout` Breaks Optimistic Payout Path — (`File: core/src/verifier.rs`)

### Summary

`Verifier::sign_optimistic_payout` receives a `withdrawal_id` from the RPC layer but names the parameter `deposit_id` and passes it to `get_move_to_vault_txid_from_citrea_deposit`, which expects a **deposit** index. Because Citrea maintains independent counters for deposits and withdrawals, the lookup silently queries the wrong row, causing the optimistic payout to fail for every withdrawal whose Citrea index does not coincidentally equal the corresponding deposit index.

### Finding Description

The RPC handler `optimistic_payout_sign` in `core/src/rpc/verifier.rs` parses the incoming `WithdrawParams` and extracts `withdrawal_id` via `parse_withdrawal_sig_params`:

```rust
// core/src/rpc/verifier.rs:115-131
let (withdrawal_id, input_signature, input_outpoint, output_script_pubkey, output_amount) =
    parser::operator::parse_withdrawal_sig_params(withdrawal_params)?;
...
self.verifier.sign_optimistic_payout(
    nonce_session_id,
    agg_nonce,
    withdrawal_id,   // ← Citrea withdrawal index
    ...
)
``` [1](#0-0) 

`parse_withdrawal_sig_params` returns `params.withdrawal_id` as the first element:

```rust
// core/src/rpc/parser/operator.rs:196-197
Ok((
    params.withdrawal_id,
    ...
))
``` [2](#0-1) 

Inside `sign_optimistic_payout`, the parameter is named `deposit_id` and is then used to query the **deposits** table:

```rust
// core/src/verifier.rs:1626-1632
let move_txid = self
    .db
    .get_move_to_vault_txid_from_citrea_deposit(None, deposit_id)  // ← wrong index
    .await?
    .ok_or_else(|| {
        BridgeError::from(eyre::eyre!("Deposit not found for id: {}", deposit_id))
    })?;
``` [3](#0-2) 

The same `deposit_id` value is also used correctly one step later to query the **withdrawals** table:

```rust
// core/src/verifier.rs:1647-1650
let withdrawal_utxo = self
    .db
    .get_withdrawal_utxo_from_citrea_withdrawal(None, deposit_id)  // ← correct here
    .await?;
``` [4](#0-3) 

The asymmetry is the bug: `get_withdrawal_utxo_from_citrea_withdrawal` correctly uses the withdrawal index, but `get_move_to_vault_txid_from_citrea_deposit` incorrectly uses the same withdrawal index as if it were a deposit index.

The database function confirms the two tables are indexed independently:

```rust
// core/src/database/verifier.rs:139-166
pub async fn get_withdrawal_utxo_from_citrea_withdrawal(
    &self, tx: Option<DatabaseTransaction<'_>>, citrea_idx: u32,
) -> Result<OutPoint, BridgeError> {
    // queries withdrawals WHERE idx = $1
``` [5](#0-4) 

### Impact Explanation

Citrea assigns deposit indices and withdrawal indices from separate monotonic counters. After the first deposit-only or withdrawal-only event, the two counters diverge. From that point on, `get_move_to_vault_txid_from_citrea_deposit(None, withdrawal_id)` either:

1. **Returns `None`** (no deposit row at that index) → the verifier returns an error and refuses to sign → the aggregator cannot collect enough partial signatures → the optimistic payout transaction is never broadcast.
2. **Returns the wrong deposit's `move_txid`** (a deposit at index `withdrawal_id` exists but belongs to a different user) → `deposit_data` is fetched for the wrong deposit → the verifier signs with the wrong MuSig2 key set → the aggregated signature is invalid for the correct payout transaction → the optimistic payout transaction is still never broadcast.

In both cases the optimistic payout path is permanently broken for any withdrawal whose Citrea index does not coincidentally equal the corresponding deposit index. Operators are forced onto the full BitVM challenge-response cycle, which imposes a multi-week on-chain delay before reimbursement, constituting a material liveness impact on bridge-controlled BTC.

### Likelihood Explanation

The divergence between deposit and withdrawal counters is the normal operating state of the bridge after any asymmetric activity (e.g., a deposit with no matching withdrawal, or vice versa). No special attacker capability is required; any user submitting an optimistic payout for withdrawal index ≥ 1 when the deposit counter is at a different value will trigger the failure. The condition is unprivileged and reachable through the public `OptimisticPayout` gRPC endpoint.

### Recommendation

Replace `deposit_id` with the correct deposit index derived from the withdrawal. The aggregator already resolves the deposit from the withdrawal's `move_txid`; the verifier should receive the resolved deposit index (or the `move_txid` itself) rather than the raw `withdrawal_id`. Concretely, either:

- Pass the actual deposit index (looked up by the aggregator from the withdrawal's `move_txid`) as a separate field in `OptimisticPayoutParams`, or
- Rename the parameter to `withdrawal_id` and add a separate lookup to resolve the deposit index before calling `get_move_to_vault_txid_from_citrea_deposit`.

### Proof of Concept

1. Deposit #0 is created; Citrea assigns deposit index 0.
2. Withdrawal #0 is created for deposit #0; Citrea assigns withdrawal index 0. Optimistic payout works (indices coincide).
3. A second deposit #1 is created; Citrea assigns deposit index 1. No withdrawal yet.
4. Withdrawal #1 is created for deposit #0 (a second withdrawal against the same deposit); Citrea assigns withdrawal index 1.
5. User calls `aggregator.optimistic_payout` with `withdrawal_id = 1`.
6. The aggregator sends `withdrawal_id = 1` to each verifier's `optimistic_payout_sign`.
7. Each verifier calls `get_move_to_vault_txid_from_citrea_deposit(None, 1)` — this returns deposit #1's `move_txid`, not deposit #0's.
8. `deposit_data` is fetched for deposit #1 (wrong deposit). The verifier signs with deposit #1's verifier set.
9. The aggregator uses deposit #0's verifier set to compute the sighash. The partial signatures are incompatible; the aggregated signature is invalid.
10. The optimistic payout transaction is never broadcast. The operator must fall back to the full BitVM cycle. [6](#0-5) [7](#0-6)

### Citations

**File:** core/src/rpc/verifier.rs (L115-138)
```rust
        let (withdrawal_id, input_signature, input_outpoint, output_script_pubkey, output_amount) =
            parser::operator::parse_withdrawal_sig_params(withdrawal_params)?;

        let verification_signature = verification_signature_str
            .map(|sig| {
                PrimitiveSignature::from_str(&sig).map_err(|e| {
                    Status::invalid_argument(format!("Invalid verification signature: {e}"))
                })
            })
            .transpose()?;

        let partial_sig = self
            .verifier
            .sign_optimistic_payout(
                nonce_session_id,
                agg_nonce,
                withdrawal_id,
                input_signature,
                input_outpoint,
                output_script_pubkey,
                output_amount,
                verification_signature,
            )
            .await?;
```

**File:** core/src/rpc/parser/operator.rs (L196-202)
```rust
    Ok((
        params.withdrawal_id,
        input_signature,
        input_outpoint,
        users_intent_script_pubkey,
        Amount::from_sat(params.output_amount),
    ))
```

**File:** core/src/verifier.rs (L1570-1659)
```rust
    pub async fn sign_optimistic_payout(
        &self,
        nonce_session_id: u128,
        agg_nonce: AggregatedNonce,
        deposit_id: u32,
        input_signature: taproot::Signature,
        input_outpoint: OutPoint,
        output_script_pubkey: ScriptBuf,
        output_amount: Amount,
        verification_signature: Option<PrimitiveSignature>,
    ) -> Result<PartialSignature, BridgeError> {
        // if the withdrawal utxo is spent, no reason to sign optimistic payout
        if self.rpc.is_utxo_spent(&input_outpoint).await? {
            return Err(
                eyre::eyre!("Withdrawal utxo {:?} is already spent", input_outpoint).into(),
            );
        }

        // check for some standard script pubkeys
        if !(output_script_pubkey.is_p2tr()
            || output_script_pubkey.is_p2pkh()
            || output_script_pubkey.is_p2sh()
            || output_script_pubkey.is_p2wpkh()
            || output_script_pubkey.is_p2wsh())
        {
            return Err(eyre::eyre!(format!(
                "Output script pubkey is not a valid script pubkey: {}, must be p2tr, p2pkh, p2sh, p2wpkh, or p2wsh",
                output_script_pubkey
            )).into());
        }

        // if verification address is set in config, check if verification signature is valid
        if let Some(address_in_config) = self.config.aggregator_verification_address {
            // check if verification signature is provided by aggregator
            if let Some(verification_signature) = verification_signature {
                let address_from_sig =
                    recover_address_from_ecdsa_signature::<OptimisticPayoutMessage>(
                        deposit_id,
                        input_signature,
                        input_outpoint,
                        output_script_pubkey.clone(),
                        output_amount,
                        verification_signature,
                    )?;

                // check if verification signature is signed by the address in config
                if address_from_sig != address_in_config {
                    return Err(BridgeError::InvalidECDSAVerificationSignature);
                }
            } else {
                // if verification signature is not provided, but verification address is set in config, return error
                return Err(BridgeError::ECDSAVerificationSignatureMissing);
            }
        }

        // check if withdrawal is valid first
        let move_txid = self
            .db
            .get_move_to_vault_txid_from_citrea_deposit(None, deposit_id)
            .await?
            .ok_or_else(|| {
                BridgeError::from(eyre::eyre!("Deposit not found for id: {}", deposit_id))
            })?;

        // amount in move_tx is exactly the bridge amount
        if output_amount
            > self.config.protocol_paramset().bridge_amount - NON_EPHEMERAL_ANCHOR_AMOUNT
        {
            return Err(eyre::eyre!(
                "Output amount is greater than the bridge amount: {} > {}",
                output_amount,
                self.config.protocol_paramset().bridge_amount - NON_EPHEMERAL_ANCHOR_AMOUNT
            )
            .into());
        }

        // check if withdrawal utxo is correct
        let withdrawal_utxo = self
            .db
            .get_withdrawal_utxo_from_citrea_withdrawal(None, deposit_id)
            .await?;

        if withdrawal_utxo != input_outpoint {
            return Err(eyre::eyre!(
                "Withdrawal utxo is not correct: {:?} != {:?}",
                withdrawal_utxo,
                input_outpoint
            )
            .into());
        }
```

**File:** core/src/database/verifier.rs (L139-166)
```rust
    pub async fn get_withdrawal_utxo_from_citrea_withdrawal(
        &self,
        tx: Option<DatabaseTransaction<'_>>,
        citrea_idx: u32,
    ) -> Result<OutPoint, BridgeError> {
        let query = sqlx::query_as::<_, (Option<TxidDB>, Option<i32>)>(
            "SELECT w.withdrawal_utxo_txid, w.withdrawal_utxo_vout
             FROM withdrawals w
             WHERE w.idx = $1",
        )
        .bind(i32::try_from(citrea_idx).wrap_err("Failed to convert citrea index to i32")?);

        let results = execute_query_with_tx!(self.connection, tx, query, fetch_optional)?;

        match results {
            None => Err(eyre::eyre!("Deposit with id {} is not set", citrea_idx).into()),
            Some((txid, vout)) => match (txid, vout) {
                (Some(txid), Some(vout)) => Ok(OutPoint {
                    txid: txid.0,
                    vout: u32::try_from(vout)
                        .wrap_err("Failed to convert withdrawal utxo vout to u32")?,
                }),
                _ => {
                    Err(eyre::eyre!("Withdrawal utxo is not set for deposit {}", citrea_idx).into())
                }
            },
        }
    }
```
