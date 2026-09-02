### Title
Unauthenticated `SendMoveToVaultTx` broadcasts an attacker-crafted transaction whose input is never checked against a verified deposit — ([File: core/src/rpc/aggregator.rs])

### Summary
`ClementineAggregator::send_move_to_vault_tx` validates only the *output* shape of the submitted `raw_tx` (value and scriptPubkey of the two outputs) but never checks that `movetx.input[0].previous_output` equals the `deposit_outpoint` supplied in the request, never checks the input witness/signature, and never calls `Verifier::is_deposit_valid` or consults the aggregator's own deposit records. Combined with `client_verification=false` disabling all authentication (`Interceptors::Noop`), any unauthenticated caller can submit a self-signed transaction spending their own funds and have the aggregator broadcast it via `tx_sender.insert_try_to_send` as if it were a verified move-to-vault transaction.

### Finding Description
Binding claimed to hold: `movetx.input[0].previous_output == deposit_outpoint` AND `deposit_outpoint` has passed `Verifier::is_deposit_valid` (i.e., its on-chain scriptPubkey matches `BaseDepositScript`/`ReplacementDepositScript` derived from the registered `nofn_xonly_pk`/`evm_address`, per `core/src/verifier.rs:659-705`).

In `core/src/rpc/aggregator.rs:1973-2102`, `send_move_to_vault_tx`:
- Deserializes `movetx` and `deposit_outpoint` independently from the request [1](#0-0) .
- Checks only `movetx.input.len()==1`, `movetx.output.len()==2`, output values `(bridge_amount, 0)`, and output scriptPubkeys against a **generic** taproot address built from the current `nofn_xonly_pk` and `security_council` (identical for every deposit, not deposit-specific) [2](#0-1) .
- Never compares `movetx.input[0].previous_output` to `deposit_outpoint`, never inspects the witness, and never invokes any deposit-validity check (`is_deposit_valid`) or DB lookup tying `deposit_outpoint` to a deposit the verifiers actually signed.
- It then unconditionally enqueues the raw transaction for broadcast with `tx_sender.insert_try_to_send(..., FeePayingType::CPFP, ...)` and returns the computed txid [3](#0-2) .

Because the actual N-of-N signing/validation of a move-to-vault transaction happens earlier, in `new_deposit`/`deposit_finalize` (which does call `Verifier::is_deposit_valid` and requires MuSig2 partial signatures, `core/src/verifier.rs:866-994`), `send_move_to_vault_tx` was designed only to *relay* an already-produced signed transaction. It performs no cross-check that the `raw_tx` it is asked to broadcast is that transaction. An attacker can instead construct their own transaction: fund a decoy P2TR UTXO with their own key, spend it with a valid self-signature, and pay `bridge_amount` to the (publicly known, deposit-independent) vault scriptPubkey plus a 0-sat anchor. This structurally satisfies every check in the function and will be accepted and broadcast, with the attacker-chosen `deposit_outpoint` recorded as `TxMetadata.deposit_outpoint` for a `MoveToVault` entry that was never verified by any verifier.

Existing guards that fail to close this gap: `only_aggregator_and_self`/`client_verification` do not apply because verification is disabled by config (documented aggregator state); `Verifier::is_deposit_valid` is never called in this code path; there is no signature/MuSig2 aggregation check inside `send_move_to_vault_tx` itself.

### Impact Explanation
Within this repository's boundary, the demonstrable effect is that an unauthenticated party can force the aggregator to broadcast an arbitrary, self-signed Bitcoin transaction of the attacker's own construction and cause a spurious `MoveToVault` DB record to be inserted with an attacker-chosen (fictitious) `deposit_outpoint`. This matches the High-severity category "an unauthenticated state-changing or broadcasting call": no certificate, no key, and no relationship to a verifier-approved deposit is required to trigger state-changing behavior (a DB insert plus a Bitcoin broadcast) on the aggregator.

It does **not** by itself achieve the claimed Critical outcomes (BTC leaving the vault without a fronted withdrawal, or N-of-N partial signatures for an unauthorized spend): the transaction the attacker submits must spend a UTXO they can actually sign for (their own funds), so no bridge-held value is spent, and the function never triggers verifier signing at all — the "N-of-N session" impact from the question is not reachable via this call. Whether this could culminate in a false Citrea mint depends entirely on the Citrea Bridge contract's own `sha_script_pubkeys` check (which hashes the *spent-input* prevout scriptPubkeys, tying a deposit to its unique per-user, EVM-address-embedding script), which is Citrea-contract logic out of scope for this repository and cannot be demonstrated or ruled out from this codebase alone.

### Likelihood Explanation
Requires `client_verification=false` (the documented aggregator deployment state) and `automation` feature enabled. The attacker needs only to fund a small decoy UTXO of `bridge_amount` to a P2TR address they control (cost: `bridge_amount` + fees, fully recoverable since they control the spending key) and reach the aggregator's public gRPC port. No verifier/operator privileges, keys, or certificates are needed. Repeatable per attempt/per decoy UTXO.

### Recommendation
In `send_move_to_vault_tx`, before enqueueing broadcast:
1. Require `movetx.input[0].previous_output == deposit_outpoint`.
2. Look up the deposit associated with `deposit_outpoint` in the aggregator's/verifiers' records and confirm it was actually signed (i.e., that the aggregator itself produced this exact `raw_tx` via `create_movetx`/`new_deposit`, or independently verify the N-of-N signature in the witness against the deposit's committed `nofn_xonly_pk`).
3. Do not derive the accepted vault scriptPubkey solely from the current global `nofn`/`security_council` config; tie acceptance to the specific `DepositData` for `deposit_outpoint`.

### Proof of Concept
```rust
// cargo test (regtest, automation feature) demonstrating the unchecked-input gap
#[tokio::test(flavor = "multi_thread")]
async fn unauthenticated_decoy_movetx_is_broadcast() {
    // Setup aggregator with client_verification = false (default insecure config).
    let mut config = create_test_config_with_thread_name().await;
    let regtest = create_regtest_rpc(&mut config).await;
    let rpc = regtest.rpc();
    let actors = create_actors::<MockCitreaClient>(&config).await;
    let mut aggregator = actors.get_aggregator();

    aggregator.setup(Request::new(Empty {})).await.unwrap();

    // Attacker funds their own decoy UTXO (not a verified deposit outpoint).
    let attacker_key = SecretKey::new(&mut rand::thread_rng());
    let attacker_addr = /* P2TR from attacker_key */;
    let decoy_outpoint = rpc.send_to_address(&attacker_addr, config.protocol_paramset().bridge_amount).await.unwrap();
    rpc.mine_blocks(6).await.unwrap();

    // Attacker crafts their own movetx spending the decoy input to the generic vault scriptPubkey.
    let raw_tx = build_attacker_movetx(&config, decoy_outpoint, &attacker_key); // 1 input, 2 outputs matching template

    // Binding under test: aggregator must reject because decoy_outpoint never passed is_deposit_valid.
    let result = aggregator.send_move_to_vault_tx(SendMoveTxRequest {
        deposit_outpoint: Some(decoy_outpoint.into()),
        raw_tx: Some(RawSignedTx { raw_tx: bitcoin::consensus::serialize(&raw_tx) }),
    }).await;

    // EXPECTED (fixed behavior): Err — input not tied to a verified deposit.
    // ACTUAL (current code): Ok(txid) — assert this demonstrates the gap.
    assert!(result.is_ok(), "vulnerability: aggregator accepted an unverified decoy movetx");
}
```

### Citations

**File:** core/src/rpc/aggregator.rs (L1998-2011)
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
```

**File:** core/src/rpc/aggregator.rs (L2020-2073)
```rust
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

**File:** core/src/rpc/aggregator.rs (L2075-2101)
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

            Ok(Response::new(movetx.compute_txid().into()))
        }
```
