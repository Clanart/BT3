### Title
`SendMoveToVaultTx` accepts a caller-supplied `deposit_outpoint` without binding it to the actual input of the submitted `movetx` - ([File: core/src/rpc/aggregator.rs])

### Summary
`ClementineAggregator::send_move_to_vault_tx` validates that a submitted raw transaction *looks like* a move-to-vault transaction (correct input/output counts, correct output amounts, and correct output script pubkeys derived from the N-of-N + security-council address), but it never checks that `movetx.input[0].previous_output` actually equals the `deposit_outpoint` field supplied in the same request. The mismatched pair is then persisted together via `insert_try_to_send` with `TxMetadata { deposit_outpoint: Some(deposit_outpoint), ... , tx_type: TransactionType::MoveToVault }`.

### Finding Description
The function reads two independent, attacker-controlled fields from `SendMoveTxRequest`: `raw_tx` (a fully signed Bitcoin transaction) and `deposit_outpoint`. [1](#0-0) 

It structurally validates the transaction shape and script pubkeys of the transaction's outputs: [2](#0-1) 

The bridge vault address checked here (`bridge_script_pubkey`) is derived only from the aggregated N-of-N verifier key and the security council multisig - it is **not** deposit-specific, so this check cannot prove the transaction corresponds to the claimed `deposit_outpoint`. There is no assertion anywhere in the function that `movetx.input[0].previous_output == deposit_outpoint`. The (unverified) pairing is then written to the DB: [3](#0-2) 

This breaks the intended binding: `deposit_outpoint == movetx.input[0].previous_output`. Downstream logic throughout the codebase (e.g. `get_move_to_vault_txid_from_citrea_deposit`, `get_deposit_data_with_move_tx`, watchtower/operator reimbursement flows) assumes this equality holds, since the `deposit_outpoint` recorded in `TxMetadata` is used as the authoritative pointer from a Citrea deposit ID to its actual on-chain move transaction/UTXO.

### Impact Explanation
Because a legitimately signed move-tx for deposit A can be submitted alongside an unrelated `deposit_outpoint` B, the bridge's off-chain bookkeeping (`tx_sender`/`TxMetadata`, and any code that later queries "which move-tx belongs to deposit B") can become desynchronized from on-chain reality. Any component that relies on this metadata to determine which vault UTXO corresponds to which deposit (used later for `withdraw`, `optimistic_payout`, or reimbursement pathways) could be misled into treating deposit B as backed by a move-tx it does not own, or into never correctly tracking deposit A's real move-tx. This falls under the "operator credited versus the party that paid" / custody-binding class of impact described in scope, since it can misattribute which deposit a vault UTXO is tied to.

### Likelihood Explanation
This RPC (`SendMoveToVaultTx`) is only compiled under the `automation` feature and is part of the `ClementineAggregator` service. It requires TLS with a certificate accepted by the aggregator's interceptor (`OnlyAggregatorAndSelf` pattern used elsewhere), so exploitation requires the ability to reach the aggregator gRPC endpoint with an accepted certificate — I was not able to fully confirm within the available context whether the Aggregator's own gRPC listener enforces the same "aggregator-or-self" restriction as verifier/operator servers, or is reachable more broadly (e.g., from the `clementine-backend` service with a broader-trust certificate). This uncertainty affects whether this is reachable by a fully unprivileged network attacker or only by an already-trusted caller of the aggregator (e.g., the backend), which would put it outside the "unprivileged attacker" scope required by the rules.

### Recommendation
Add an explicit check in `send_move_to_vault_tx` that `movetx.input[0].previous_output == deposit_outpoint` before calling `insert_try_to_send`, so the recorded metadata cannot be desynchronized from the actual spent outpoint.

### Proof of Concept
Given a validly-signed move-to-vault transaction `movetx_A` for deposit outpoint `A` (satisfying the input/output count, amount, and script-pubkey checks), a caller submits:
```
SendMoveTxRequest {
  raw_tx: <movetx_A signed bytes>,
  deposit_outpoint: B   // unrelated deposit outpoint, != movetx_A.input[0].previous_output
}
```
The handler passes all checks (none of them compare `deposit_outpoint` to `movetx.input[0].previous_output`) and persists `TxMetadata { deposit_outpoint: Some(B), tx_type: MoveToVault }` bound to `movetx_A`, as shown at [4](#0-3) . This creates a bookkeeping record incorrectly asserting that deposit `B`'s move transaction is `movetx_A`.

### Citations

**File:** core/src/rpc/aggregator.rs (L1998-2017)
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

**File:** core/src/rpc/aggregator.rs (L2075-2100)
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
```
