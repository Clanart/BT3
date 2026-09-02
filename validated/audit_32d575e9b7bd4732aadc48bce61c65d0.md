No vulnerability found for this question.

**Reasoning:**

The claimed equality is: `is_utxo_spent(input_outpoint)` at the start of `sign_optimistic_payout`/`optimistic_payout` == `input_outpoint` still unspent at the time the final aggregated signature's transaction is broadcast. This check is indeed not atomic with the final broadcast — `optimistic_payout` checks spend status once [1](#0-0) , then does multiple network round trips to collect nonces and partial signatures from verifiers before assembling and broadcasting the final tx [2](#0-1) , and `Verifier::sign_optimistic_payout` performs the same non-atomic check [3](#0-2) .

However, this gap does not translate into the claimed impact (two independently-valid spends of the same MoveToVault funds). Both the optimistic payout transaction and a competing operator-fronted payout transaction spend the *same* Bitcoin outpoint (`input_outpoint`/`withdrawal_utxo`). Bitcoin's UTXO consensus model — not application logic — guarantees that only one transaction spending a given outpoint can ever be confirmed on-chain; the loser of the race is rejected as a conflicting/double-spend transaction by the network when its broadcast is attempted (via `tx_sender.add_tx_to_queue`, [4](#0-3) ). Even though the aggregator may finish computing a valid MuSig2 aggregate signature for the optimistic payout, that signature only produces a *candidate* transaction; it cannot result in an actual confirmed second spend of already-spent bridge funds, since the UTXO no longer exists once the competing transaction confirms.

So while the check-then-act gap is real, the exploit description does not demonstrate bridge value actually leaving a MoveToVault UTXO twice, an operator being reimbursed for a payout it never funded, or any of the other Critical/High impacts listed — it demonstrates, at most, a wasted signing ceremony whose resulting transaction fails to confirm, which is expected UTXO-race behavior and not a bridge-security defect reachable by an unprivileged attacker. No code path in this repo allows the optimistic payout tx to double-spend the withdrawal UTXO after it has already been consumed by another confirmed transaction.

### Citations

**File:** core/src/rpc/aggregator.rs (L1032-1042)
```rust
        // if the withdrawal utxo is spent, no reason to sign optimistic payout
        if self
            .rpc
            .is_utxo_spent(&input_outpoint)
            .await
            .map_to_status()?
        {
            return Err(Status::invalid_argument(format!(
                "Withdrawal utxo is already spent: {input_outpoint:?}",
            )));
        }
```

**File:** core/src/rpc/aggregator.rs (L1128-1231)
```rust
            // get which verifiers participated in the deposit to collect the optimistic payout tx signature
            let participating_verifiers = self.get_participating_verifiers(&deposit_data).await?;
            let verifiers_ids = participating_verifiers.ids();
            let (first_responses, mut nonce_streams) = {
                create_nonce_streams(
                    participating_verifiers.clone(),
                    1,
                    #[cfg(test)]
                    &self.config,
                )
                .await?
            };
            // collect nonces
            let pub_nonces = get_next_pub_nonces(&mut nonce_streams, &verifiers_ids)
                .await
                .wrap_err("Failed to aggregate nonces for optimistic payout")
                .map_to_status()?;
            let agg_nonce = aggregate_nonces(pub_nonces.iter().collect::<Vec<_>>().as_slice())?;

            let agg_nonce_bytes = agg_nonce.serialize().to_vec();
            // send the agg nonce to the verifiers to sign the optimistic payout tx
            let opt_payout_sign_futures = participating_verifiers
                .clients()
                .iter()
                .zip(first_responses)
                .map(|(client, first_response)| {
                    let mut client = client.clone();
                    let opt_withdraw_params = opt_withdraw_params.clone();
                    {
                        let agg_nonce_serialized = agg_nonce_bytes.clone();
                        async move {
                            let mut request = Request::new(OptimisticPayoutParams {
                                opt_withdrawal: Some(opt_withdraw_params),
                                agg_nonce: agg_nonce_serialized,
                                nonce_gen: Some(first_response),
                            });
                            request.set_timeout(OPTIMISTIC_PAYOUT_TIMEOUT);
                            client.optimistic_payout_sign(request).await
                        }
                    }
                })
                .collect::<Vec<_>>();

            // get signatures and check for any errors
            let opt_payout_resps = join_all(opt_payout_sign_futures).await;
            let mut payout_sigs = Vec::new();
            let mut errors = Vec::new();
            for (resp, verifier_id) in opt_payout_resps
                .into_iter()
                .zip(participating_verifiers.ids())
            {
                match resp {
                    Ok(res) => {
                        payout_sigs.push(res.into_inner());
                    }
                    Err(e) => {
                        errors.push(format!("{verifier_id} optimistic payout sign failed: {e}"));
                    }
                }
            }
            if !errors.is_empty() {
                return Err(eyre::eyre!("{errors:?}").into_status());
            }

            // calculate final sig
            // txin at index 1 is deposited utxo in movetx
            let sighash = opt_payout_txhandler.calculate_script_spend_sighash_indexed(
                1,
                0,
                bitcoin::TapSighashType::Default,
            )?;

            let musig_partial_sigs = payout_sigs
                .into_iter()
                .map(|sig| {
                    PartialSignature::from_byte_array(
                        &sig.partial_sig
                            .try_into()
                            .map_err(|_| secp256k1::musig::ParseError::MalformedArg)?,
                    )
                })
                .collect::<Result<Vec<_>, _>>()
                .map_err(|e| Status::internal(format!("Failed to parse partial sig: {e:?}")))?;

            let musig_sigs_and_nonces = musig_partial_sigs
                .into_iter()
                .zip(pub_nonces)
                .collect::<Vec<_>>();

            let final_sig = bitcoin::taproot::Signature {
                signature: crate::musig2::aggregate_partial_signatures(
                    deposit_data.get_verifiers(),
                    None,
                    agg_nonce,
                    &musig_sigs_and_nonces,
                    Message::from_digest(sighash.to_byte_array()),
                )?,
                sighash_type: bitcoin::TapSighashType::Default,
            };

            // set witness and send tx
            opt_payout_txhandler.set_p2tr_script_spend_witness(&[final_sig.serialize()], 1, 0)?;
            let opt_payout_txhandler = opt_payout_txhandler.promote()?;
            let opt_payout_tx = opt_payout_txhandler.get_cached_tx();
```

**File:** core/src/rpc/aggregator.rs (L1237-1253)
```rust
            #[cfg(feature = "automation")]
            {
                tracing::info!("Sending optimistic payout tx via tx_sender");

                let mut dbtx = self.db.begin_transaction().await?;
                self.tx_sender
                    .add_tx_to_queue(
                        &mut dbtx,
                        TransactionType::OptimisticPayout,
                        opt_payout_tx,
                        &[],
                        None,
                        self.config.protocol_paramset(),
                        None,
                    )
                    .await
                    .map_to_status()?;
```

**File:** core/src/verifier.rs (L1580-1586)
```rust
    ) -> Result<PartialSignature, BridgeError> {
        // if the withdrawal utxo is spent, no reason to sign optimistic payout
        if self.rpc.is_utxo_spent(&input_outpoint).await? {
            return Err(
                eyre::eyre!("Withdrawal utxo {:?} is already spent", input_outpoint).into(),
            );
        }
```
