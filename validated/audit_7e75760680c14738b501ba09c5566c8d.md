After thorough investigation of the Clementine codebase, I have found a valid analog.

---

### Title
Citrea Sequencer Downtime Allows Malicious Operator to Bypass the Challenge Window and Steal Bridged BTC — (`core/src/states/event.rs`)

### Summary
The verifier's only path to challenge a malicious kickoff is gated on a `SystemEvent::LCPProcessed` event, which is emitted only when the Citrea light-client prover successfully returns an LCP for a given Bitcoin block height. If the Citrea sequencer is offline for ≥ `operator_challenge_timeout_timelock` Bitcoin blocks (144 blocks, ~1 day), no LCP is ever generated for the kickoff height, `check_if_kickoff_malicious` is never called, the Bitcoin-enforced challenge window expires, and the operator can send `ChallengeTimeout` and proceed to reimbursement unchallenged.

### Finding Description

**Step 1 — The only challenge trigger is `LCPProcessed`.**

In `core/src/states/event.rs`, `check_if_kickoff_malicious` is called in exactly two places:

1. When a new kickoff is detected and `last_processed_lcp >= kickoff_height` (i.e., the LCP was already available).
2. When `SystemEvent::LCPProcessed { height }` fires and there are kickoff machines at that height. [1](#0-0) 

If `LCPProcessed` never fires for the kickoff height, `check_if_kickoff_malicious` is never invoked, and no challenge transaction is ever queued.

**Step 2 — `LCPProcessed` depends entirely on the Citrea sequencer being live.**

The `LcpSyncerTask` drives `Verifier::handle_finalized_block` for each finalized Bitcoin block. Internally, `get_light_client_proof` calls `get_light_client_proof_by_l1_height` on the Citrea light-client prover RPC. When the Citrea sequencer is down, this RPC returns `None`, and no `LCPProcessed` event is emitted for that height. [2](#0-1) 

**Step 3 — The Bitcoin-enforced challenge window is fixed and does not pause.**

The `ChallengeTimeout` transaction uses a Bitcoin CSV timelock of `operator_challenge_timeout_timelock` (144 blocks, ~1 day). This timelock counts Bitcoin blocks regardless of Citrea sequencer status. [3](#0-2) [4](#0-3) 

**Step 4 — After the window expires, the operator proceeds to reimbursement.**

Once the `ChallengeTimeout` tx is mined, the `KickoffFinalizer` UTXO is spent, the `KickoffStateMachine` transitions to `closed`, and the operator proceeds through the reimbursement flow without ever having been challenged. [5](#0-4) 

**Step 5 — The maliciousness check itself confirms the impact.**

`is_kickoff_malicious` verifies that the payout blockhash committed in the kickoff WOTS matches the actual payout data from Citrea. A malicious operator who committed a wrong blockhash (i.e., did not legitimately front the withdrawal) would be caught here — but only if this function is ever called. [6](#0-5) 

### Impact Explanation
A malicious operator can commit a fraudulent payout blockhash in the kickoff WOTS (claiming to have fronted a withdrawal they did not), time the kickoff during a Citrea sequencer outage of ≥ 144 Bitcoin blocks, and receive reimbursement from the bridge deposit (bridged BTC) without having legitimately fronted the withdrawal. This constitutes direct theft of bridged BTC from the bridge deposit, up to `bridge_amount` (1 BTC in production config) per exploited kickoff slot.

### Likelihood Explanation
Citrea is an L2 with a centralized sequencer. A 144-block (~24-hour) outage is plausible due to planned maintenance, software bugs, or a targeted DoS attack. The operator does not need to control the sequencer — they only need to observe an ongoing outage and submit their kickoff transaction during it. The attack is permissionless for any registered operator.

### Recommendation
1. **Track LCP availability against the challenge deadline.** Before the `operator_challenge_timeout_timelock` expires for any active kickoff, verify that an LCP has been processed for the kickoff height. If not, block or delay the `ChallengeTimeout` transaction at the off-chain automation layer.
2. **Extend the challenge window on sequencer downtime.** Introduce a protocol-level mechanism (e.g., a Citrea-side sequencer uptime oracle or a Bitcoin-anchored liveness proof) that pauses or extends the challenge window when the sequencer is demonstrably offline.
3. **Fail-safe challenge.** If `LCPProcessed` has not fired for a kickoff height within a configurable safety margin before the challenge deadline, treat the kickoff as malicious by default and queue the challenge transaction.

### Proof of Concept

1. Operator registers and sends a kickoff tx at Bitcoin height H, committing a fraudulent payout blockhash (no legitimate withdrawal was fronted).
2. Citrea sequencer goes offline (or is DoS'd) before the LCP for height H is generated.
3. `LcpSyncerTask` calls `get_light_client_proof(H)` → returns `None` → no `LCPProcessed { height: H }` event is emitted.
4. `check_if_kickoff_malicious` is never called for this kickoff; no challenge tx is queued.
5. 144 Bitcoin blocks elapse. The `ChallengeTimeout` CSV timelock matures.
6. Operator broadcasts `ChallengeTimeout` tx, spending the `KickoffFinalizer` UTXO.
7. `KickoffStateMachine` transitions to `closed` (line 525 of `kickoff.rs`).
8. Operator proceeds through the reimbursement flow and receives `bridge_amount` BTC from the bridge deposit, having never legitimately fronted the withdrawal. [7](#0-6) [8](#0-7) [9](#0-8)

### Citations

**File:** core/src/states/event.rs (L262-313)
```rust
                // check if malicious if lcp is already processed for the kickoff height
                if let Some(last_lcp_height) = self.last_processed_lcp {
                    if last_lcp_height >= kickoff_height {
                        self.check_if_kickoff_malicious(
                            &payout_blockhash,
                            &kickoff_data,
                            &deposit_data,
                            &mut context,
                        )
                        .await?;
                    }
                }
            }
            // Received when a the LCP for an L1 block height is processed
            SystemEvent::LCPProcessed { height } => {
                let kickoffs_to_check: Vec<_> = self
                    .kickoff_machines
                    .iter()
                    .filter(|machine| machine.kickoff_height == height)
                    .map(|machine| {
                        (
                            machine.payout_blockhash.clone(),
                            machine.kickoff_data,
                            machine.deposit_data.clone(),
                        )
                    })
                    .collect();

                if !kickoffs_to_check.is_empty() {
                    // create a dummy context for duty processing, a block is not needed for LCPProcessed
                    let mut dummy_context = self.new_context_with_block_cache(
                        dbtx.clone(),
                        self.last_finalized_block.clone().ok_or_eyre(
                            "Last finalized block not found, should always be Some after initialization",
                        )?,
                    )?;

                    for (payout_blockhash, kickoff_data, deposit_data) in kickoffs_to_check {
                        self.check_if_kickoff_malicious(
                            &payout_blockhash,
                            &kickoff_data,
                            &deposit_data,
                            &mut dummy_context,
                        )
                        .await?;
                    }
                }

                tracing::info!("LCP processed for height: {}", height);

                self.last_processed_lcp = Some(height);
            }
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

**File:** core/src/builder/transaction/challenge.rs (L367-389)
```rust
pub fn create_challenge_timeout_txhandler(
    kickoff_txhandler: &TxHandler,
    paramset: &'static ProtocolParamset,
) -> Result<TxHandler, BridgeError> {
    Ok(TxHandlerBuilder::new(TransactionType::ChallengeTimeout)
        .with_version(NON_STANDARD_V3)
        .add_input(
            NormalSignatureKind::OperatorSighashDefault,
            kickoff_txhandler.get_spendable_output(UtxoVout::Challenge)?,
            SpendPath::ScriptSpend(1),
            Sequence::from_height(paramset.operator_challenge_timeout_timelock),
        )
        .add_input(
            NormalSignatureKind::ChallengeTimeout2,
            kickoff_txhandler.get_spendable_output(UtxoVout::KickoffFinalizer)?,
            SpendPath::ScriptSpend(0),
            DEFAULT_SEQUENCE,
        )
        .add_output(UnspentTxOut::from_partial(
            builder::transaction::anchor_output(paramset.anchor_amount()),
        ))
        .finalize())
}
```

**File:** crates/clementine-config/src/protocol.rs (L103-105)
```rust
    /// Number of blocks for operator challenge timeout timelock (currently BLOCKS_PER_WEEK)
    pub operator_challenge_timeout_timelock: u16,
    /// Number of blocks for operator challenge NACK timelock (currently BLOCKS_PER_WEEK * 3)
```

**File:** core/src/states/kickoff.rs (L382-388)
```rust
                    self.matchers.insert(
                        Matcher::BlockHeight(
                            self.kickoff_height
                                + context.config.time_to_send_watchtower_challenge as u32,
                        ),
                        KickoffEvent::TimeToSendWatchtowerChallenge,
                    );
```

**File:** core/src/states/kickoff.rs (L521-526)
```rust
            // When the kickoff finalizer is spent in Bitcoin,
            // the kickoff process is finished and the state machine will transition to the "Closed" state
            KickoffEvent::KickoffFinalizerSpent => {
                tracing::info!("Detected kickoff finalizer spent for {}", self.kickoff_data,);
                Transition(State::closed())
            }
```

**File:** core/src/verifier.rs (L1859-1914)
```rust
    async fn is_kickoff_malicious(
        &self,
        kickoff_witness: Witness,
        deposit_data: &mut DepositData,
        kickoff_data: KickoffData,
        dbtx: DatabaseTransaction<'_>,
    ) -> Result<bool, BridgeError> {
        let move_txid =
            create_move_to_vault_txhandler(deposit_data, self.config.protocol_paramset())?
                .get_cached_tx()
                .compute_txid();

        let payout_info = self
            .db
            .get_payout_info_from_move_txid(Some(dbtx), move_txid)
            .await?;
        let Some((operator_xonly_pk_opt, payout_blockhash, _, _)) = payout_info else {
            tracing::warn!(
                "No payout info found in db for move txid {move_txid}, assuming malicious"
            );
            return Ok(true);
        };

        let Some(operator_xonly_pk) = operator_xonly_pk_opt else {
            tracing::warn!("No operator xonly pk found in payout tx OP_RETURN, assuming malicious");
            return Ok(true);
        };

        if operator_xonly_pk != kickoff_data.operator_xonly_pk {
            tracing::warn!("Operator xonly pk for the payout does not match with the kickoff_data");
            return Ok(true);
        }

        let wt_derive_path = WinternitzDerivationPath::Kickoff(
            kickoff_data.round_idx,
            kickoff_data.kickoff_idx,
            self.config.protocol_paramset(),
        );
        let commits = extract_winternitz_commits(
            kickoff_witness,
            &[wt_derive_path],
            self.config.protocol_paramset(),
        )?;
        let blockhash_data = commits.first();
        // only last 20 bytes of the blockhash is committed
        let truncated_blockhash = &payout_blockhash[12..];
        if let Some(committed_blockhash) = blockhash_data {
            if committed_blockhash != truncated_blockhash {
                tracing::warn!("Payout blockhash does not match committed hash: committed: {:?}, truncated payout blockhash: {:?}",
                        blockhash_data, truncated_blockhash);
                return Ok(true);
            }
        } else {
            return Err(eyre::eyre!("Couldn't retrieve committed data from witness").into());
        }
        Ok(false)
```

**File:** core/src/task/lcp_syncer.rs (L54-75)
```rust
#[async_trait::async_trait]
impl<C> crate::bitcoin_syncer::BlockHandler for Verifier<C>
where
    C: CitreaClientT,
{
    async fn handle_new_block(
        &mut self,
        dbtx: DatabaseTransaction<'_>,
        block_id: u32,
        block: bitcoin::Block,
        height: u32,
    ) -> Result<(), BridgeError> {
        self.handle_finalized_block(
            dbtx,
            block_id,
            height,
            Arc::new(BlockCache::from_block(block, height)),
            None,
        )
        .await
    }
}
```
