### Title
Verifier's `sign_optimistic_payout` skips withdrawal signature verification, letting any caller obtain N-of-N partial signatures for an unauthorized optimistic payout - (File: `core/src/verifier.rs`)

### Summary
`Verifier::sign_optimistic_payout` in `core/src/verifier.rs` builds the optimistic-payout transaction and produces a MuSig2 partial signature over the move-to-vault script-spend input without ever verifying that `input_signature` is a valid Schnorr signature from the actual withdrawal UTXO owner (`user_xonly_pk`). Every other code path that consumes the same `input_signature` (`Operator::withdraw` and `AggregatorServer::optimistic_payout`) explicitly performs this check before proceeding, but the verifier's own signing entrypoint does not.

### Finding Description
The withdrawal-authorization binding in Clementine is: the withdrawal UTXO's owner signs `input_signature` (a Taproot key-spend Schnorr signature, `SinglePlusAnyoneCanPay`) authorizing a specific `output_script_pubkey`/`output_amount` pair. Every consumer of this signature is expected to verify:
```
sighash = sighash_of(payout_tx_input_0)
verify_schnorr(input_signature, sighash, user_xonly_pk)
```
This is exactly what happens in:
- `Operator::withdraw`, `core/src/operator.rs:630-637` [1](#0-0) 
- `AggregatorServer::optimistic_payout`, `core/src/rpc/aggregator.rs:1120-1126` [2](#0-1) 

However, `Verifier::sign_optimistic_payout` — the function invoked by the verifier's gRPC `optimistic_payout_sign` handler — builds the same `create_optimistic_payout_txhandler` with the caller-supplied `input_signature`, `output_script_pubkey`, and `output_amount`, but never calls `verify_schnorr` (or any other check) to confirm `input_signature` matches the withdrawal UTXO's `user_xonly_pk`: [3](#0-2) 

The only checks performed are: the withdrawal UTXO is not yet spent, the output script type is standard, an optional ECDSA "verification signature" from the aggregator (which is itself derived only from the caller-supplied fields, not from any check that `input_signature` is valid), the output amount does not exceed the bridge amount, and that `input_outpoint` matches the UTXO Citrea has on record for that `deposit_id`: [4](#0-3) 

Because `input_signature` is never checked against `user_xonly_pk`, any caller reaching this RPC directly (bypassing the aggregator's own schnorr check) can supply garbage bytes as `input_signature` together with an attacker-chosen `output_script_pubkey`/`output_amount` (up to the bridge amount) for any deposit whose withdrawal has been registered on Citrea, and the verifier will happily compute and return a real MuSig2 partial signature toward the N-of-N signature that authorizes moving the deposited funds (script-path input of the move-to-vault UTXO) to that attacker-chosen output — an "unauthorised N-of-N partial signature," binding-equality violated:
```
signer_of(input_signature) == owner_of(withdrawal_utxo)   [expected]
vs.
signer_of(input_signature) == anyone                       [actual, in verifier path]
```
This mirrors the report's bug class: a signing/state-mutating hash/verification path omits a binding element (there, a missing parameter in the recomputed hash; here, a missing signature-verification step), letting an unintended party trigger a privileged operation.

### Impact Explanation
This produces "unauthorised N-of-N partial signatures" against the deposited/move-to-vault funds, which the rules classify as Critical impact. Although completing the resulting two-input transaction on-chain would still additionally require a valid key-spend witness for input 0 (the withdrawal UTXO itself, owned by the real Citrea withdrawer) — meaning full BTC exfiltration is not demonstrated here — verifiers can be induced to produce genuine cryptographic co-signatures for payouts that were never authorized by the withdrawal owner. This breaks the intended custody/authorization boundary (verifier signing must be gated on user authorization) and is exactly the missing-verification class of bug the reference report describes, independent of whether every downstream constraint happens to also block final broadcast.

### Likelihood Explanation
The verifier's `optimistic_payout_sign` gRPC method is reachable by any party able to connect to a verifier node (no verifier/operator/aggregator role, key, or certificate is required to call it — it is the RPC surface itself that is unauthenticated for this check). Any attacker who knows a registered Citrea `deposit_id`/withdrawal outpoint (public information once a withdrawal is registered) can invoke this call directly, supplying a garbage `input_signature` and an arbitrary standard output. No privileged access is needed to trigger the flawed code path; only reaching all participating verifiers with consistent nonces is required to obtain the aggregated N-of-N signature for input 1.

### Recommendation
Add the same signature verification present in `Operator::withdraw` and `AggregatorServer::optimistic_payout` to `Verifier::sign_optimistic_payout` before using `input_signature`/`output_script_pubkey`/`output_amount` to build a partial signature: recover `user_xonly_pk` from the withdrawal UTXO's script pubkey and call `SECP.verify_schnorr(&input_signature.signature, &Message::from_digest(sighash), user_xonly_pk)`, rejecting the request on failure, mirroring `core/src/rpc/aggregator.rs:1120-1126`.

### Proof of Concept
1. Wait for (or observe) a legitimate Citrea withdrawal to be registered for `deposit_id = X`, giving a known `withdrawal_utxo` (outpoint owned by the real withdrawer) still unspent.
2. Directly call the verifier's `optimistic_payout_sign` gRPC method (bypassing the aggregator) with:
   - `withdrawal_id = X`
   - `input_outpoint = withdrawal_utxo` (matches DB record, passes the check at `core/src/verifier.rs:1646-1659`)
   - `input_signature` = arbitrary 64-byte garbage with `SinglePlusAnyoneCanPay` flag
   - `output_script_pubkey` / `output_amount` = attacker-controlled standard output, amount ≤ `bridge_amount`
3. Observe that `Verifier::sign_optimistic_payout` (`core/src/verifier.rs:1570-1713`) never rejects the request for an invalid `input_signature`, and returns a valid MuSig2 `PartialSignature` over the move-to-vault script-spend input for the attacker-chosen output.
4. Repeat against a sufficient number of verifiers with matching nonces to assemble the aggregated N-of-N signature — demonstrating that verifiers co-sign an optimistic payout that the withdrawal owner never authorized.

### Citations

**File:** core/src/operator.rs (L630-637)
```rust
        let sighash = payout_txhandler.calculate_sighash_txin(0, in_signature.sighash_type)?;

        SECP.verify_schnorr(
            &in_signature.signature,
            &Message::from_digest(*sighash.as_byte_array()),
            user_xonly_pk,
        )
        .wrap_err("Failed to verify signature received from user for payout txin. Ensure the signature uses SinglePlusAnyoneCanPay sighash type.")?;
```

**File:** core/src/rpc/aggregator.rs (L1120-1126)
```rust
            let sighash = opt_payout_txhandler
                .calculate_pubkey_spend_sighash(0, input_signature.sighash_type)?;

            let message = Message::from_digest(sighash.to_byte_array());

            SECP.verify_schnorr(&input_signature.signature, &message, &user_xonly_pk)
                .map_err(|_| Status::internal("Invalid signature for optimistic payout tx. Ensure the signature uses SinglePlusAnyoneCanPay sighash type."))?;
```

**File:** core/src/verifier.rs (L1581-1690)
```rust
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

        let mut deposit_data = self
            .db
            .get_deposit_data_with_move_tx(None, move_txid)
            .await?
            .ok_or_eyre("Deposit data corresponding to move txid not found")?;

        let withdrawal_prevout = self.rpc.get_txout_from_outpoint(&input_outpoint).await?;
        let withdrawal_utxo = UTXO {
            outpoint: input_outpoint,
            txout: withdrawal_prevout,
        };
        let output_txout = TxOut {
            value: output_amount,
            script_pubkey: output_script_pubkey,
        };

        let opt_payout_txhandler = create_optimistic_payout_txhandler(
            &mut deposit_data,
            withdrawal_utxo,
            output_txout,
            input_signature,
            self.config.protocol_paramset(),
        )?;
        // txin at index 1 is deposited utxo in movetx
        let sighash = opt_payout_txhandler.calculate_script_spend_sighash_indexed(
            1,
            0,
            bitcoin::TapSighashType::Default,
        )?;

```
