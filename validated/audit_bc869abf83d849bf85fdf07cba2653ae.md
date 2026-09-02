### Title
Unauthenticated Aggregator RPC Broadcasts a Move-to-Vault Transaction Without Verifying It Spends the Claimed Deposit Outpoint - (File: core/src/rpc/aggregator.rs)

### Summary
The aggregator's `send_move_to_vault_tx` RPC is reachable without any authentication (the aggregator does not enforce mTLS client-certificate checks, unlike the operator/verifier servers) and validates only the *output* amounts/script-pubkeys of a supplied `movetx`, never confirming that the transaction actually spends the `deposit_outpoint` the caller claims it does. This breaks the binding `deposit_outpoint (claimed) == outpoint actually consumed by movetx (real)` before the pairing is persisted and the transaction is queued for fee-bumped broadcast.

### Finding Description
`send_move_to_vault_tx` in [1](#0-0)  deserializes an attacker-supplied raw transaction and a caller-supplied `deposit_outpoint`, but the only checks performed are on the transaction shape and output fields:

- input/output counts [2](#0-1) 
- output[0] value equals `bridge_amount` and output[1] value is 0 [3](#0-2) 
- output script-pubkeys match the N-of-N/security-council vault address and the anchor script [4](#0-3) 

Nowhere is `movetx.input[0].previous_output` compared against the caller-supplied `deposit_outpoint`. The function then persists `TxMetadata { deposit_outpoint: Some(deposit_outpoint), tx_type: TransactionType::MoveToVault, .. }` and queues the transaction for CPFP fee-bumped broadcast via `self.tx_sender.insert_try_to_send(...)` [5](#0-4) .

This RPC is reachable by an unprivileged network caller: per the project's own documentation, "The aggregator does not enforce client certificates but does use TLS for encryption" [6](#0-5) , in contrast to operator/verifier servers, which reject any caller whose leaf certificate is not the aggregator's or their own via the `only_aggregator_and_self` interceptor [7](#0-6) .

This is structurally the same class of defect as the reported `createDistribution()` issue: an input parameter that is supposed to be cryptographically/structurally tied to a specific committed value (`_ethAmount`/`_reachAmount` vs. the Merkle-committed totals in the report; here, `deposit_outpoint` vs. the outpoint the transaction's witness actually authorizes spending) is accepted without validating that binding, and the mismatch is written into durable state (TxMetadata / DB bookkeeping) that downstream flows (deposit → move-to-vault → withdrawal/reimbursement lookups such as `get_move_to_vault_txid_from_citrea_deposit`, referenced in `core/src/database/verifier.rs` and consumed by `sign_optimistic_payout`/`optimistic_payout`) rely on to associate a specific deposit with its vault transaction.

Note: I was not able to fully trace, within the available tool budget, the exact code path that later reads back this `TxMetadata.deposit_outpoint` association into the `get_move_to_vault_txid_from_citrea_deposit`/`get_deposit_data_with_move_tx` lookup tables used elsewhere (e.g. in `core/src/verifier.rs` and `core/src/rpc/aggregator.rs::optimistic_payout`). This is a genuine gap in my verification — the severity of downstream misattribution (e.g. whether it can actually corrupt a legitimate deposit's move-txid record, freeze its progression, or cause double bookkeeping) depends on that unverified linkage. What is proven with exact file/function support is: (1) the endpoint is unauthenticated, (2) it is state-changing (it inserts a DB row and queues a broadcast), and (3) it accepts an unvalidated attacker-controlled `deposit_outpoint`/`movetx` pairing.

### Impact Explanation
If the unverified linkage does feed deposit-outpoint → move-txid records that later drive reimbursement/withdrawal logic, an attacker could cause a legitimate deposit's bookkeeping to point at the wrong (or a replayed) move-to-vault transaction, potentially freezing that deposit's future withdrawal/reimbursement flow or creating a misattributed record. Independent of that unresolved question, the endpoint unambiguously allows an unauthenticated caller to trigger a state-changing/broadcasting operation on the aggregator (inserting `TxMetadata` and queuing fee-bumped rebroadcast of a Bitcoin transaction), which matches the "High — an unauthenticated state-changing or broadcasting call" impact category on its own, regardless of the deeper misattribution question.

### Likelihood Explanation
Reaching this endpoint requires no credentials — the aggregator's gRPC endpoint does not authenticate callers by design [6](#0-5) . Constructing a syntactically valid `movetx` that passes the amount/script checks requires either observing a genuinely N-of-N-signed move-to-vault transaction (e.g., from the mempool or a broadcast attempt for a different, unrelated deposit) or already possessing one from a legitimate deposit flow; the attacker does not need to forge a new signature, only resubmit an existing valid transaction bytes with a mismatched `deposit_outpoint` claim.

### Recommendation
In `send_move_to_vault_tx`, before persisting `TxMetadata` or queuing the transaction for broadcast, verify that `movetx.input[0].previous_output` equals the caller-supplied `deposit_outpoint`, and additionally verify that `deposit_outpoint` corresponds to a deposit actually known/registered in the aggregator's/verifiers' database (e.g., via `get_deposit_data` or equivalent) so that an attacker cannot associate an arbitrary or unrelated `deposit_outpoint` with a transaction. More broadly, consider whether aggregator RPCs that mutate durable state or trigger broadcasts should require at minimum a lightweight authentication/rate-limiting layer, consistent with the stricter model already used for operator/verifier servers.

### Proof of Concept
1. Observe (or possess) a validly N-of-N-signed `movetx` for deposit `A` with outpoint `outpoint_A` (its output amounts/script-pubkeys will pass validation since they are fixed by `bridge_amount` and the N-of-N/security-council address, independent of which deposit it came from).
2. As an unauthenticated client, call the aggregator's `send_move_to_vault_tx` RPC with `raw_tx = movetx_bytes` and `deposit_outpoint = outpoint_B` (a different, unrelated deposit's outpoint) — see request handling at [8](#0-7) .
3. All checks pass because they only inspect `movetx.output[0]`/`output[1]` [9](#0-8)  — `movetx.input[0].previous_output` is never compared to `deposit_outpoint`.
4. The aggregator records `TxMetadata { deposit_outpoint: Some(outpoint_B), tx_type: MoveToVault }` and queues the transaction for CPFP-fee-bumped broadcast [5](#0-4) , creating a persisted association between `outpoint_B` and a transaction that in reality spends `outpoint_A`.

### Citations

**File:** core/src/rpc/aggregator.rs (L1973-2017)
```rust
    #[tracing::instrument(skip(self), err(level = tracing::Level::ERROR))]
    async fn send_move_to_vault_tx(
        &self,
        request: Request<clementine::SendMoveTxRequest>,
    ) -> Result<Response<clementine::Txid>, Status> {
        tracing::info!("Send move to vault tx rpc called");
        #[cfg(not(feature = "automation"))]
        {
            let _ = request;
            return Err(Status::unimplemented(
                "Automation is disabled, cannot automatically send move to vault tx.",
            ));
        }

        #[cfg(feature = "automation")]
        {
            use bitcoin::Amount;
            use std::sync::Arc;

            use crate::builder::{
                address::create_taproot_address,
                script::{CheckSig, Multisig, SpendableScript},
                transaction::anchor_output,
            };

            let request = request.into_inner();
            let movetx: bitcoin::Transaction = bitcoin::consensus::deserialize(
                &request
                    .raw_tx
                    .ok_or_eyre("raw_tx is required")
                    .map_to_status()?
                    .raw_tx,
            )
            .wrap_err("Failed to deserialize movetx")
            .map_to_status()?;
            let deposit_outpoint: bitcoin::OutPoint = request
                .deposit_outpoint
                .ok_or(Status::invalid_argument("deposit_outpoint is required"))?
                .try_into()?;

            tracing::info!(
                "Parsed send move to vault tx rpc params, deposit outpoint: {:?}, movetx hex: {}",
                deposit_outpoint,
                bitcoin::consensus::encode::serialize_hex(&movetx)
            );
```

**File:** core/src/rpc/aggregator.rs (L2019-2073)
```rust
            // check if transaction is a movetx
            if movetx.input.len() != 1 || movetx.output.len() != 2 {
                return Err(Status::invalid_argument(
                    "Transaction is not a movetx, input or output lengths are not correct",
                ));
            }
            // check output values
            // movetx always has 0 sat anchor output
            if !(movetx.output[0].value == self.config.protocol_paramset().bridge_amount
                && movetx.output[1].value == Amount::from_sat(0))
            {
                return Err(Status::invalid_argument(format!(
                    "Transaction is not a movetx, output sat values are not correct, should be ({}, 0), got ({}, {})",
                    self.config.protocol_paramset().bridge_amount,
                    movetx.output[0].value,
                    movetx.output[1].value,
                )));
            }
            // check output scriptpubkeys
            let verifier_keys = self.fetch_verifier_keys().await?;
            let nofn_xonly_pk =
                bitcoin::XOnlyPublicKey::from_musig2_pks(verifier_keys.clone(), None).map_err(
                    |e| {
                        Status::internal(format!(
                            "Failed to aggregate verifier public keys, err: {e}, pubkeys: {verifier_keys:?}"
                        ))
                    },
                )?;
            let nofn_script = Arc::new(CheckSig::new(nofn_xonly_pk));
            let security_council_script = Arc::new(Multisig::from_security_council(
                self.config.security_council.clone(),
            ));

            let (addr, _) = create_taproot_address(
                &[
                    nofn_script.to_script_buf(),
                    security_council_script.to_script_buf(),
                ],
                None,
                self.config.protocol_paramset().network,
            );
            let bridge_script_pubkey = addr.script_pubkey();

            if !(movetx.output[1].script_pubkey
                == anchor_output(self.config.protocol_paramset().anchor_amount()).script_pubkey
                && movetx.output[0].script_pubkey == bridge_script_pubkey)
            {
                return Err(Status::invalid_argument(
                    format!("Transaction is not a movetx, output scriptpubkeys are not correct, expected: (vault: {:?}, anchor: {:?}), got: (vault: {:?}, anchor: {:?})",
                    bridge_script_pubkey,
                    anchor_output(self.config.protocol_paramset().anchor_amount()).script_pubkey,
                    movetx.output[0].script_pubkey,
                    movetx.output[1].script_pubkey,
                )));
            }
```

**File:** core/src/rpc/aggregator.rs (L2075-2098)
```rust
            let mut dbtx = self.db.begin_transaction().await?;
            self.tx_sender
                .insert_try_to_send(
                    &mut dbtx,
                    Some(TxMetadata {
                        deposit_outpoint: Some(deposit_outpoint),
                        operator_xonly_pk: None,
                        round_idx: None,
                        kickoff_idx: None,
                        tx_type: TransactionType::MoveToVault,
                    }),
                    &movetx,
                    FeePayingType::CPFP,
                    None,
                    &[],
                    &[],
                    &[],
                    &[],
                )
                .await
                .map_to_status()?;
            dbtx.commit()
                .await
                .map_err(|e| Status::internal(format!("Failed to commit db transaction: {e}")))?;
```

**File:** docs/usage.md (L192-204)
```markdown
## RPC Authentication

Clementine uses mutual TLS (mTLS) to secure gRPC communications between entities
and to authenticate clients. Client certificates are verified and filtered by
the verifier/operator to ensure that:

1. Verifier/Operator methods can only be called by the aggregator (using
   aggregator's client certificate `aggregator_cert_path`)
2. Internal methods can only be called by the entity's own client certificate
   (using the entity's client certificate `client_cert_path`)

The aggregator does not enforce client certificates but does use TLS for encryption.

```

**File:** core/src/rpc/interceptors.rs (L35-77)
```rust
#[allow(clippy::result_large_err)]
fn only_aggregator_and_self(
    req: Request<()>,
    our_cert: &CertificateDer<'static>,
    aggregator_cert: &CertificateDer<'static>,
) -> Result<Request<()>, Status> {
    let Some(peer_certs) = req.peer_certs() else {
        if cfg!(test) {
            // Test mode, we don't need to verify peer certificates
            return Ok(req);
        } else {
            // If we're not in test mode, we need to check peer certificates
            return Err(Status::unauthenticated(
                "Failed to verify peer certificate, is TLS enabled?",
            ));
        }
    };

    // IMPORTANT: Only check the leaf (end-entity) certificate, which is always the first
    // certificate in the chain. The leaf is the only certificate whose private key the peer
    // proved possession of during the TLS handshake. Checking anywhere else in the chain
    // would allow identity spoofing: an attacker could include a pinned cert as an
    // intermediate in their chain without possessing its private key.
    let Some(leaf_cert) = peer_certs.first() else {
        return Err(Status::unauthenticated("Peer certificate chain is empty"));
    };

    if is_internal(&req) {
        if leaf_cert == our_cert {
            Ok(req)
        } else {
            Err(Status::unauthenticated(
                "Unauthorized call to internal method (not self)",
            ))
        }
    } else if leaf_cert == aggregator_cert || leaf_cert == our_cert {
        Ok(req)
    } else {
        Err(Status::unauthenticated(
            "Unauthorized call to method (not aggregator or self)",
        ))
    }
}
```
