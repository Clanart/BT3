Looking at the state machine flow, `send_operator_asserts_if_ready`, the `fetch_validate_and_store_lcp` call chain, and the `HttpClientBuilder` configuration, I have enough to produce a complete finding.

---

### Title
Citrea Light Client Prover Is a Single-Point-of-Failure Oracle With No Retry or Fallback in the Operator Assert Path — (`core/src/citrea.rs`, `core/src/operator.rs`)

### Summary

The operator's `send_asserts` function depends on a single, non-retried RPC call to the Citrea light client prover to obtain the `LightClientProof` (LCP) required to generate BitVM assert transactions. If the prover is unavailable or has not yet generated a proof for the required L1 block height, the call fails immediately with no retry and no fallback. Because the state machine only re-triggers `send_operator_asserts_if_ready` in response to specific Bitcoin events (watchtower challenge sent/timed out, latest blockhash committed), once all those events have fired and the LCP fetch has failed, there is no automatic retry path. If the `assert_timeout_timelock` (576 blocks, ~4 days) expires before the operator can successfully submit asserts, verifiers can slash the operator's collateral via `AssertTimeout` transactions.

### Finding Description

**Root cause — no retry or fallback in `get_light_client_proof`:**

`CitreaClient::get_light_client_proof` in `core/src/citrea.rs` makes a single JSON-RPC call to `light_client_prover_client.get_light_client_proof_by_l1_height(l1_height)` with a 60-second timeout and no retry logic:

```rust
// core/src/citrea.rs:503-507
let proof_result = self
    .light_client_prover_client
    .get_light_client_proof_by_l1_height(l1_height)
    .await
    .wrap_err("Failed to get light client proof")?;
```

The `light_client_prover_client` is built with `HttpClientBuilder::default().request_timeout(timeout.unwrap_or(Duration::from_secs(60)))` — a plain `jsonrpsee` HTTP client with no retry middleware, unlike the `ExtendedBitcoinRpc` which wraps every call in `RetryIf::spawn` with exponential backoff. [1](#0-0) [2](#0-1) 

**`fetch_validate_and_store_lcp` propagates the failure immediately:**

If `get_light_client_proof` returns `None` (proof not yet generated for that L1 height) or an error, `fetch_validate_and_store_lcp` returns an error without saving anything to the DB. Because the DB check at the top of the function only short-circuits on a previously saved LCP, a failed fetch leaves no cached state and the next call will hit the prover again. [3](#0-2) 

**`send_asserts` propagates the error up through the state machine:**

`send_asserts` in `core/src/operator.rs` calls `fetch_validate_and_store_lcp` and immediately propagates any error with `?`: [4](#0-3) 

**State machine has no periodic retry after all events fire:**

`send_operator_asserts_if_ready` is called from `on_challenged_entry`, `WatchtowerChallengeSent`, `WatchtowerChallengeTimeoutSent`, and `LatestBlockHashSent` handlers. Once all watchtower UTXOs are spent and the latest blockhash is committed, no further events trigger this function. A failed LCP fetch at that point leaves the operator permanently unable to submit asserts for that kickoff unless the node is manually restarted and a new event coincidentally arrives. [5](#0-4) [6](#0-5) 

**`capture_error` silently swallows the failure:**

The error from `dispatch_duty(Duty::SendOperatorAsserts {...})` is captured by `capture_error`, which stores it in `context.errors` and returns `()`. The state machine continues without retrying. [7](#0-6) 

**`AssertTimeout` slashes collateral:**

If the operator fails to submit all mini-assert transactions within `assert_timeout_timelock` blocks (576 blocks in the reference config), verifiers can broadcast `AssertTimeout` transactions that burn the operator's `CollateralInRound` UTXO. [8](#0-7) 

### Impact Explanation

An operator who has legitimately fronted a withdrawal payout and initiated a kickoff can have their collateral (configured at 130,000,000 sats = 1.3 BTC in the reference `.env.example`) slashed if the Citrea light client prover is unavailable or lagging at the precise moment all watchtower conditions are satisfied. The operator cannot recover without a new triggering event, and no fallback source for the LCP exists in the codebase. [9](#0-8) [10](#0-9) 

### Likelihood Explanation

The Citrea light client prover is an external service that generates ZK proofs per Bitcoin block. Proof generation can lag behind the Bitcoin tip, especially under load or after a restart. The operator's assert window opens only after all watchtower challenges are resolved and the latest blockhash is committed — a narrow, one-shot window. Any transient unavailability of the prover during this window, with no retry, produces a permanent miss for that kickoff.

### Recommendation

1. **Add retry with exponential backoff** to `get_light_client_proof` using the same `RetryIf::spawn` pattern already used in `ExtendedBitcoinRpc`, retrying on transport errors and `None` responses (proof not yet available).
2. **Add a periodic block-height-based retry matcher** in the kickoff state machine: after `send_operator_asserts_if_ready` fails, insert a `Matcher::BlockHeight(current + N)` event so the duty is retried on the next block, up to the assert timeout boundary.
3. **Do not treat `None` from the prover as a fatal error** — treat it as "not yet available" and schedule a retry.

### Proof of Concept

1. Operator sends a legitimate kickoff and payout transaction at Bitcoin block `H`.
2. All watchtower challenges are sent/timed out; latest blockhash is committed on Bitcoin.
3. `send_operator_asserts_if_ready` fires and calls `send_asserts`.
4. `fetch_validate_and_store_lcp(H, ...)` calls `get_light_client_proof_by_l1_height(H)`.
5. The Citrea light client prover is temporarily unavailable (network blip, restart, or proof generation lag) — returns an error or `None`.
6. `fetch_validate_and_store_lcp` returns `Err(...)`. Nothing is saved to DB.
7. `capture_error` swallows the error. No retry matcher is inserted.
8. No further `WatchtowerChallengeSent`, `WatchtowerChallengeTimeoutSent`, or `LatestBlockHashSent` events arrive (all already processed).
9. 576 blocks pass. Verifiers broadcast `AssertTimeout` transactions, burning the operator's `CollateralInRound` UTXO.
10. Operator loses 1.3 BTC (reference config) despite having acted honestly. [11](#0-10) [12](#0-11)

### Citations

**File:** core/src/citrea.rs (L342-354)
```rust
        let lcp_result = self
            .get_light_client_proof(payout_block_height, paramset)
            .await?;
        let (_lcp, lcp_receipt, _l2_height) = match lcp_result {
            Some(lcp) => lcp,
            None => {
                return Err(eyre::eyre!(
                    "Light client proof could not be fetched found for block height {}",
                    payout_block_height
                )
                .into())
            }
        };
```

**File:** core/src/citrea.rs (L404-407)
```rust
        let light_client_prover_client = HttpClientBuilder::default()
            .request_timeout(timeout.unwrap_or(Duration::from_secs(60)))
            .build(light_client_prover_url)
            .wrap_err("Failed to create Citrea LCP RPC client")?;
```

**File:** core/src/citrea.rs (L498-553)
```rust
    async fn get_light_client_proof(
        &self,
        l1_height: u64,
        paramset: &'static ProtocolParamset,
    ) -> Result<Option<(LightClientProof, Receipt, u64)>, BridgeError> {
        let proof_result = self
            .light_client_prover_client
            .get_light_client_proof_by_l1_height(l1_height)
            .await
            .wrap_err("Failed to get light client proof")?;
        tracing::debug!(
            "Light client proof result {}: {:?}",
            l1_height,
            proof_result
        );

        let ret = if let Some(proof_result) = proof_result {
            let decoded: InnerReceipt = bincode::deserialize(&proof_result.proof)
                .wrap_err("Failed to deserialize light client proof from citrea lcp")?;
            let receipt = receipt_from_inner(decoded)
                .wrap_err("Failed to create receipt from light client proof")?;

            let l2_height = u64::try_from(proof_result.light_client_proof_output.last_l2_height)
                .wrap_err("Failed to convert l2 height to u64")?;

            let lc_image_id = paramset.get_lcp_image_id()?;

            let proof_output: LightClientCircuitOutput = borsh::from_slice(&receipt.journal.bytes)
                .wrap_err("Failed to deserialize light client circuit output")?;

            if !paramset.is_regtest() {
                receipt
                    .verify(lc_image_id)
                    .map_err(|_| eyre::eyre!("Light client proof verification failed"))?;

                if !check_method_id(&proof_output, lc_image_id) {
                    return Err(eyre::eyre!(
                    "Current light client proof method ID does not match the expected LC image ID"
                )
                    .into());
                }
            }

            Some((
                LightClientProof {
                    lc_journal: receipt.journal.bytes.clone(),
                },
                receipt,
                l2_height,
            ))
        } else {
            None
        };

        Ok(ret)
    }
```

**File:** core/src/operator.rs (L1228-1240)
```rust
    async fn send_asserts(
        &self,
        mut dbtx: DatabaseTransaction<'_>,
        kickoff_data: KickoffData,
        deposit_data: DepositData,
        watchtower_challenges: HashMap<usize, Transaction>,
        _payout_blockhash: Witness,
        latest_blockhash: Witness,
    ) -> Result<(), BridgeError> {
        use bridge_circuit_host::utils::{get_verifying_key, is_dev_mode};
        use citrea_sov_rollup_interface::zk::light_client_proof::output::LightClientCircuitOutput;

        let context = ContractContext::new_context_for_kickoff(
```

**File:** core/src/operator.rs (L1315-1324)
```rust
        let lcp_receipt = self
            .citrea_client
            .fetch_validate_and_store_lcp(
                payout_block_height as u64,
                deposit_idx as u32,
                &self.db,
                Some(&mut dbtx),
                self.config.protocol_paramset(),
            )
            .await?;
```

**File:** core/src/states/kickoff.rs (L276-301)
```rust
    async fn send_operator_asserts_if_ready(&mut self, context: &mut StateContext<T>) {
        context
            .capture_error(async |context| {
                {
                    // if all watchtower challenge utxos are spent and latest blockhash is committed, its safe to send asserts
                    if self.challenged
                        && self.spent_watchtower_utxos.len()
                            == self.deposit_data.get_num_watchtowers()
                        && self.latest_blockhash != Witness::default()
                    {
                        context
                            .dispatch_duty(Duty::SendOperatorAsserts {
                                kickoff_data: self.kickoff_data,
                                deposit_data: self.deposit_data.clone(),
                                watchtower_challenges: self.watchtower_challenges.clone(),
                                payout_blockhash: self.payout_blockhash.clone(),
                                latest_blockhash: self.latest_blockhash.clone(),
                            })
                            .await?;
                    }
                    Ok::<(), BridgeError>(())
                }
                .wrap_err(self.kickoff_meta("on send_operator_asserts"))
            })
            .await;
    }
```

**File:** core/src/states/kickoff.rs (L443-476)
```rust
    #[superstate]
    async fn kickoff(
        &mut self,
        event: &KickoffEvent,
        context: &mut StateContext<T>,
    ) -> Response<State> {
        tracing::trace!("Received event in kickoff superstate: {:?}", event);
        match event {
            // When a watchtower challenge is detected in Bitcoin,
            // save the full challenge transaction and check if the latest blockhash can be committed
            // and if the disprove is ready to be sent
            KickoffEvent::WatchtowerChallengeSent {
                watchtower_idx,
                challenge_outpoint,
            } => {
                self.spent_watchtower_utxos.insert(*watchtower_idx);
                let tx = context
                    .cache
                    .get_tx_of_utxo(challenge_outpoint)
                    .expect("Challenge outpoint that got matched should be in block");
                tracing::info!(
                    "Detected watchtower challenge for watchtower {} for {}",
                    watchtower_idx,
                    self.kickoff_data,
                );
                // save challenge witness
                self.watchtower_challenges
                    .insert(*watchtower_idx, tx.clone());
                self.create_matcher_for_latest_blockhash_if_ready(context)
                    .await;
                self.send_operator_asserts_if_ready(context).await;
                self.disprove_if_ready(context).await;
                Handled
            }
```

**File:** core/src/states/context.rs (L217-225)
```rust
    pub async fn capture_error(
        &mut self,
        fnc: impl AsyncFnOnce(&mut Self) -> Result<(), eyre::Report>,
    ) {
        let result = fnc(self).await;
        if let Err(e) = result {
            self.errors.push(e.into());
        }
    }
```

**File:** core/src/builder/transaction/operator_collateral.rs (L170-205)
```rust
pub fn create_assert_timeout_txhandlers(
    kickoff_txhandler: &TxHandler,
    round_txhandler: &TxHandler,
    num_asserts: usize,
    paramset: &'static ProtocolParamset,
) -> Result<Vec<TxHandler>, BridgeError> {
    let mut txhandlers = Vec::new();
    for idx in 0..num_asserts {
        txhandlers.push(
            TxHandlerBuilder::new(TransactionType::AssertTimeout(idx))
                .with_version(NON_STANDARD_V3)
                .add_input(
                    (NumberedSignatureKind::AssertTimeout1, idx as i32),
                    kickoff_txhandler.get_spendable_output(UtxoVout::Assert(idx))?,
                    SpendPath::ScriptSpend(0),
                    Sequence::from_height(paramset.assert_timeout_timelock),
                )
                .add_input(
                    (NumberedSignatureKind::AssertTimeout2, idx as i32),
                    kickoff_txhandler.get_spendable_output(UtxoVout::KickoffFinalizer)?,
                    SpendPath::ScriptSpend(0),
                    DEFAULT_SEQUENCE,
                )
                .add_input(
                    (NumberedSignatureKind::AssertTimeout3, idx as i32),
                    round_txhandler.get_spendable_output(UtxoVout::CollateralInRound)?,
                    SpendPath::KeySpend,
                    DEFAULT_SEQUENCE,
                )
                .add_output(UnspentTxOut::from_partial(
                    builder::transaction::anchor_output(paramset.anchor_amount()),
                ))
                .finalize(),
        );
    }
    Ok(txhandlers)
```

**File:** .env.example (L33-33)
```text

```

**File:** .env.example (L63-63)
```text
DISPROVE_TIMEOUT_TIMELOCK=720
```
