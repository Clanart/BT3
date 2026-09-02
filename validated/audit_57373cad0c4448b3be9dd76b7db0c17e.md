### Title
Unauthenticated `SendMoveToVaultTx` accepts a caller-supplied `deposit_outpoint` that is never checked against the transaction's actual input, allowing misattribution of a move-to-vault broadcast - ([File: core/src/rpc/aggregator.rs])

### Summary
The aggregator's `send_move_to_vault_tx` gRPC endpoint validates the structure and script/amount of the supplied `raw_tx` (must be 1 input / 2 outputs, correct vault script pubkey and anchor amount) but never checks that `movetx.input[0].previous_output` actually equals the `deposit_outpoint` field supplied in the same request. Both values are attacker-controlled request fields, and the aggregator server explicitly does not enforce client certificates on this RPC, so any network caller can invoke it.

### Finding Description
`send_move_to_vault_tx` deserializes an attacker-supplied `raw_tx` and a separately supplied `deposit_outpoint`, checks that the transaction has exactly one input and two outputs, and that the two outputs match the expected vault script/amount and zero-value anchor: [1](#0-0) 

It then derives the expected N-of-N + security-council vault script and compares it to `movetx.output[0].script_pubkey`: [2](#0-1) 

Crucially, `movetx.input[0].previous_output` — the actual UTXO the transaction spends — is never compared against the caller-supplied `deposit_outpoint`. The unchecked `deposit_outpoint` is stored directly as `TxMetadata.deposit_outpoint` in the DB record used for tracking/CPFP-bumping the broadcast transaction: [3](#0-2) 

This is structurally the same class of bug as the audited "deposit" issue: a value that should be bound to (derived from/verified against) the accompanying data is instead trusted as an independent, unchecked input. Here it is `deposit_outpoint` accompanying `raw_tx` without a check that they are the same deposit.

Compounding this, the aggregator's own docs and server-setup code confirm this RPC has no client-certificate authentication — the aggregator server only enforces mTLS on Verifier/Operator services, not on itself: `docs/usage.md` states "The aggregator does not enforce client certificates but does use TLS for encryption," and `core/src/servers.rs` builds the aggregator server without the `OnlyAggregatorAndSelf` interceptor gating used for verifier/operator servers. [4](#0-3) 

### Impact Explanation
Because the movetx script must still carry a valid N-of-N + security-council signature to pass the output-script check, an attacker cannot forge an arbitrary move-to-vault transaction from scratch. However, since the RPC is unauthenticated and `deposit_outpoint` is not bound to the transaction's real input, any party who obtains a validly-signed move tx (e.g., from a legitimate `NewDeposit` flow) can resubmit it to `SendMoveToVaultTx` tagged with an incorrect/unrelated `deposit_outpoint`. This corrupts the `TxMetadata.deposit_outpoint` association persisted for that broadcast, which downstream logic (deposit tracking/reimbursement bookkeeping) relies on to attribute the movetx to the correct deposit. This risks a misattributed record between the on-chain deposit and the DB-tracked outpoint used for reimbursement/crediting bookkeeping — matching the "operator credited versus the party that paid" / misattributed reimbursement binding class called out in scope.

### Likelihood Explanation
Reaching this code path requires no privileged role or key: the aggregator service does not gate `SendMoveToVaultTx` behind mTLS client-certificate checks, so any network-reachable caller can invoke it directly with attacker-chosen `deposit_outpoint` and any transaction bytes that happen to satisfy the output-script/amount checks. The check that is missing (`input[0].previous_output == deposit_outpoint`) is a single, obvious comparison that is absent from the function body I reviewed.

### Recommendation
Add an explicit check in `send_move_to_vault_tx` that `movetx.input[0].previous_output` equals the parsed `deposit_outpoint`, before inserting the `TxMetadata` record, mirroring the remediation pattern from the referenced report (validate that the supplied auxiliary account/parameter actually corresponds to the data being acted upon). Additionally, consider whether this aggregator RPC should require authentication given it accepts arbitrary attacker-chosen metadata alongside a transaction payload.

### Proof of Concept
1. Obtain (via normal protocol operation, e.g. observing a `NewDeposit` response, or on-chain if already broadcast) a valid, correctly-signed move-to-vault transaction `tx_A` for deposit outpoint `D_A`.
2. As an unauthenticated network client (no certificate required against the aggregator per `docs/usage.md`), call `SendMoveToVaultTx` with `raw_tx = tx_A` but `deposit_outpoint = D_B` (an unrelated/attacker-chosen outpoint).
3. Because `send_move_to_vault_tx` only checks output structure/scripts and never verifies `tx_A.input[0].previous_output == D_B`, the call succeeds and a `TxMetadata` record with `deposit_outpoint = D_B` and `tx_type = MoveToVault` is inserted for `tx_A`'s txid. [3](#0-2) 

Note: I was unable to fully trace how `TxMetadata.deposit_outpoint` is subsequently consumed downstream (e.g., in Citrea deposit-crediting/reimbursement logic) within the indexed portion of the codebase, so the exact downstream consequence (double-crediting vs. bookkeeping corruption only) could not be conclusively confirmed from the available index; a full-repository review (e.g., via a Devin session) would be needed to trace all consumers of this field.

### Citations

**File:** core/src/rpc/aggregator.rs (L1998-2036)
```rust
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
```

**File:** core/src/rpc/aggregator.rs (L2037-2073)
```rust
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

**File:** core/src/rpc/interceptors.rs (L22-33)
```rust
impl Interceptor for Interceptors {
    #[allow(clippy::result_large_err)]
    fn call(&mut self, req: Request<()>) -> Result<Request<()>, Status> {
        match self {
            Interceptors::OnlyAggregatorAndSelf {
                our_cert,
                aggregator_cert,
            } => only_aggregator_and_self(req, our_cert, aggregator_cert),
            Interceptors::Noop => Ok(req),
        }
    }
}
```
