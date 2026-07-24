### Title
Hard-Capped Fee Rate Prevents Verifier Safety Transactions from Confirming During Bitcoin Fee Spikes, Allowing Malicious Operator to Escape Disprove and Steal Bridged BTC — (`crates/clementine-config/src/tx_sender.rs`, `crates/clementine-tx-sender/src/lib.rs`, `core/src/verifier.rs`, `core/src/tx_sender_queue.rs`)

---

### Summary

The Clementine TxSender enforces a hard cap on fee rates (`fee_rate_hard_cap`, default 100 sat/vB) for all CPFP and RBF transactions. Safety-critical verifier transactions — specifically `WatchtowerChallengeTimeout`, `AssertTimeout`, and `LatestBlockhashTimeout` — are queued as CPFP and are subject to this cap. A malicious operator who has submitted invalid asserts can exploit a Bitcoin fee spike (>100 sat/vB) by simply not sending their own `WatchtowerChallengeTimeout` transactions. Because verifiers cannot bump their CPFP transactions above the hard cap, the disprove flow stalls. After `disprove_timeout_timelock` (720 blocks ≈ 5 days), the operator manually broadcasts `DisproveTimeout` (which has an anchor output and can be CPFP'd at any fee rate outside the automated system), then collects the pre-signed `Reimburse` transaction, stealing the bridged BTC.

---

### Finding Description

**Root cause — the hard cap:**

`TxSenderLimits::fee_rate_hard_cap` defaults to 100 sat/vB and is enforced unconditionally in `calculate_target_fee_rate`:

```rust
let hard_cap = FeeRateKvb::from_sat_per_vb(self.tx_sender_limits.fee_rate_hard_cap)
    .expect("fee_rate_hard_cap should be valid");
// ...
return Ok(std::cmp::min(result, hard_cap));
``` [1](#0-0) 

The same cap is applied when fetching the mempool fee rate:

```rust
if fee_sat_kvb > fee_rate_hard_cap * 1000 {
    fee_sat_kvb = fee_rate_hard_cap * 1000;
}
``` [2](#0-1) 

**Safety-critical transactions use CPFP and are capped:**

When a kickoff is challenged, `queue_txs_for_challenged_kickoff` queues `WatchtowerChallengeTimeout` with `FeePayingType::CPFP`:

```rust
TransactionType::WatchtowerChallengeTimeout(idx) => {
    self.tx_sender
        .insert_try_to_send(
            dbtx,
            ...
            FeePayingType::CPFP,
            ...
        )
        .await?;
}
``` [3](#0-2) 

The comment in the same function explicitly acknowledges that verifiers must send these timeouts to prevent operator abuse:

```rust
// Technically verifiers do not need to send watchtower challenge timeout tx,
// but in state manager we attempt to disprove only if all watchtower challenges utxos are spent
// so if verifiers do not send timeouts, operators can abuse this (by not sending watchtower challenge timeouts)
// to not get disproven
``` [4](#0-3) 

**The disprove gate requires all watchtower UTXOs to be spent:**

```rust
async fn disprove_if_ready(&mut self, context: &mut StateContext<T>) {
    if self.challenged
        && self.operator_asserts.len() == ClementineBitVMPublicKeys::number_of_assert_txs()
        && self.latest_blockhash != Witness::default()
        && self.spent_watchtower_utxos.len() == self.deposit_data.get_num_watchtowers()
        && self.watchtower_challenges.keys().all(|idx| self.operator_challenge_acks.contains_key(idx))
    {
        self.send_disprove(context).await;
    }
}
``` [5](#0-4) 

If even one watchtower UTXO is unspent (because neither the operator nor the verifier sent `WatchtowerChallengeTimeout`), `disprove_if_ready` never fires.

**The `DisproveTimeout` has an anchor output and can be manually CPFP'd at any fee rate:**

```rust
pub fn create_disprove_timeout_txhandler(...) -> Result<TxHandler<Unsigned>, BridgeError> {
    Ok(TxHandlerBuilder::new(TransactionType::DisproveTimeout)
        ...
        .add_output(UnspentTxOut::from_partial(anchor_output(paramset.anchor_amount())))
        .finalize())
}
``` [6](#0-5) 

A malicious operator can manually CPFP the `DisproveTimeout` at any fee rate, bypassing the automated hard cap entirely.

**The `Disprove` transaction uses `NoFunding` and cannot be fee-bumped:**

```rust
TransactionType::Disprove => {
    self.insert_try_to_send(
        dbtx,
        tx_metadata,
        signed_tx,
        FeePayingType::NoFunding,
        ...
    )
    .await
}
``` [7](#0-6) 

The `send_no_funding_tx` path simply calls `send_raw_transaction` with no fee bumping: [8](#0-7) 

**Protocol timelocks (from `ProtocolParamset`):**

| Timelock | Blocks | ~Days |
|---|---|---|
| `watchtower_challenge_timeout_timelock` | 288 | 2 |
| `disprove_timeout_timelock` | 720 | 5 | [9](#0-8) 

The window for verifiers to send `WatchtowerChallengeTimeout` is blocks 288–720 (432 blocks ≈ 3 days). If fees exceed 100 sat/vB for this entire window, the disprove flow is permanently blocked for that kickoff.

---

### Impact Explanation

A malicious operator who has submitted invalid asserts can steal the full bridged BTC amount (`bridge_amount`, e.g., 1 BTC = 100,000,000 sats per kickoff) by:

1. Not sending `WatchtowerChallengeTimeout` for any watchtower that did not challenge.
2. Waiting for a Bitcoin fee spike to prevent verifiers from sending CPFP transactions above 100 sat/vB.
3. After `disprove_timeout_timelock` (720 blocks), manually broadcasting `DisproveTimeout` with a high-fee CPFP child (no hard cap applies to manual broadcasts).
4. Collecting the pre-signed `Reimburse` transaction, which transfers the deposit from the bridge vault to the operator's address.

The operator's collateral is not burned (the disprove tx never confirms), and the bridge vault loses the full deposit. This is a direct theft of bridged BTC from the bridge-controlled UTXO.

---

### Likelihood Explanation

Bitcoin fee spikes exceeding 100 sat/vB have occurred multiple times historically (2021 bull run, 2023 Ordinals/BRC-20 craze, 2024 Runes launch). Sustained spikes lasting 3+ days are less common but have occurred. The attack requires:

1. A malicious operator (already assumed to be the threat model).
2. A Bitcoin fee spike above 100 sat/vB sustained for ~432 blocks (~3 days).
3. The operator to simply not send their own `WatchtowerChallengeTimeout` transactions (trivial for a malicious actor).

The default hard cap of 100 sat/vB is set in the default config and is the value used in all documented deployments: [10](#0-9) [11](#0-10) 

---

### Recommendation

1. **Remove the hard cap for safety-critical transactions.** Introduce a `FeePayingType::CpfpUncapped` variant (or a priority flag) for `WatchtowerChallengeTimeout`, `AssertTimeout`, `LatestBlockhashTimeout`, and `OperatorChallengeNack`. These transactions must confirm within protocol timelocks regardless of fee conditions.

2. **Self-fund safety-critical timeout transactions from operator collateral.** Analogous to how the `Disprove` tx uses `NoFunding` (funded by the collateral UTXO), timeout transactions could be structured to spend a small portion of the operator's collateral as fee input, making them self-funding and immune to the hard cap.

3. **Alert and escalate.** When the mempool fee rate exceeds the hard cap and safety-critical transactions are pending, the system should emit a high-severity alert and allow operators/verifiers to manually intervene with uncapped fee rates.

---

### Proof of Concept

**Setup:** Mainnet or testnet4 with `fee_rate_hard_cap = 100` (default). Operator has submitted a fraudulent kickoff with invalid asserts. At least one watchtower did not send a `WatchtowerChallenge` transaction.

**Attack steps:**

1. Operator submits fraudulent kickoff; verifiers detect it and send `Challenge`.
2. Some watchtowers send `WatchtowerChallenge`; at least one does not.
3. Operator sends invalid `MiniAssert` transactions.
4. Bitcoin mempool fee rate rises above 100 sat/vB (e.g., due to market event).
5. Verifier TxSender attempts to send `WatchtowerChallengeTimeout` for the unchallenged watchtower via CPFP. `calculate_target_fee_rate` caps the fee at 100 sat/vB. The transaction is not accepted by miners and is eventually evicted from the mempool.
6. Operator does not send `WatchtowerChallengeTimeout` (they are malicious).
7. `disprove_if_ready` never fires because `spent_watchtower_utxos.len() < get_num_watchtowers()`.
8. After block 720 (relative to kickoff), operator manually constructs a CPFP child for `DisproveTimeout` at 500 sat/vB (no hard cap on manual broadcast). `DisproveTimeout` confirms.
9. Operator sends pre-signed `ReadyToReimburse` and then `Reimburse`, collecting the full `bridge_amount` from the vault.
10. The bridge vault loses the deposit; operator's collateral is never burned.

**Corrupted value:** The `Reimburse` transaction transfers `bridge_amount` (e.g., 1 BTC) to the operator's address despite the operator having submitted invalid asserts. The operator's collateral UTXO (`CollateralInRound`) is never spent by the `Disprove` transaction.

### Citations

**File:** crates/clementine-tx-sender/src/lib.rs (L487-492)
```rust
        let hard_cap = FeeRateKvb::from_sat_per_vb(self.tx_sender_limits.fee_rate_hard_cap)
            .expect("fee_rate_hard_cap should be valid");

        let Some(previous_rate) = previous_effective_fee_rate else {
            // No previous effective fee rate, use the new fee rate (capped)
            return Ok(std::cmp::min(new_fee_rate, hard_cap));
```

**File:** crates/clementine-tx-sender/src/lib.rs (L572-617)
```rust
    pub async fn send_no_funding_tx(
        &self,
        try_to_send_id: u32,
        tx: Transaction,
        tx_metadata: Option<TxMetadata>,
    ) -> Result<()> {
        match self.rpc.send_raw_transaction(&tx).await {
            Ok(sent_txid) => {
                tracing::debug!(
                    try_to_send_id,
                    "Successfully sent no funding tx with txid {}",
                    sent_txid
                );
                let _ = self
                    .db
                    .update_tx_debug_sending_state(try_to_send_id, "no_funding_send_success", true)
                    .await;
            }
            Err(e) => {
                let err_str = e.to_string();
                if rpc_errors::is_rejecting_replacement_error(&err_str) {
                    tracing::debug!(
                        try_to_send_id,
                        "No funding tx rejected (tx already in mempool): {err_str}"
                    );
                    return Ok(());
                } else {
                    tracing::error!(
                        "Failed to send no funding tx with try_to_send_id: {try_to_send_id:?} and metadata: {tx_metadata:?}"
                    );
                    log_error_for_tx!(
                        self.db,
                        try_to_send_id,
                        format!("send_raw_transaction error for no funding tx: {err_str}")
                    );
                }
                let _ = self
                    .db
                    .update_tx_debug_sending_state(try_to_send_id, "no_funding_send_failed", true)
                    .await;
                return Err(SendTxError::Other(eyre::eyre!(e)));
            }
        };

        Ok(())
    }
```

**File:** crates/clementine-extended-rpc/src/client.rs (L829-836)
```rust
                if fee_sat_kvb > fee_rate_hard_cap * 1000 {
                    tracing::warn!(
                        "Fee rate {} sat/kvB exceeds hard cap {} sat/kvB, using hard cap",
                        fee_sat_kvb,
                        fee_rate_hard_cap * 1000
                    );
                    fee_sat_kvb = fee_rate_hard_cap * 1000;
                }
```

**File:** core/src/verifier.rs (L2062-2065)
```rust
                // Technically verifiers do not need to send watchtower challenge timeout tx,
                // but in state manager we attempt to disprove only if all watchtower challenges utxos are spent
                // so if verifiers do not send timeouts, operators can abuse this (by not sending watchtower challenge timeouts)
                // to not get disproven
```

**File:** core/src/verifier.rs (L2066-2085)
```rust
                TransactionType::WatchtowerChallengeTimeout(idx) => {
                    self.tx_sender
                        .insert_try_to_send(
                            dbtx,
                            Some(TxMetadata {
                                tx_type: TransactionType::WatchtowerChallengeTimeout(idx),
                                ..tx_metadata
                            }),
                            signed_tx,
                            FeePayingType::CPFP,
                            None,
                            &[OutPoint {
                                txid: kickoff_txid,
                                vout: UtxoVout::KickoffFinalizer.get_vout(),
                            }],
                            &[],
                            &[],
                            &[],
                        )
                        .await?;
```

**File:** core/src/states/kickoff.rs (L260-271)
```rust
    async fn disprove_if_ready(&mut self, context: &mut StateContext<T>) {
        if self.challenged && self.operator_asserts.len() == ClementineBitVMPublicKeys::number_of_assert_txs()
            && self.latest_blockhash != Witness::default()
            && self.spent_watchtower_utxos.len() == self.deposit_data.get_num_watchtowers()
            // check if all operator acks are received, one ack for each watchtower challenge
            // to make sure we have all preimages required to disprove if operator didn't include 
            // the watchtower challenge in the BitVM proof
            && self.watchtower_challenges.keys().all(|idx| self.operator_challenge_acks.contains_key(idx))
        {
            self.send_disprove(context).await;
        }
    }
```

**File:** core/src/builder/transaction/operator_assert.rs (L35-53)
```rust
    Ok(TxHandlerBuilder::new(TransactionType::DisproveTimeout)
        .with_version(NON_STANDARD_V3)
        .add_input(
            NormalSignatureKind::OperatorSighashDefault,
            kickoff_txhandler.get_spendable_output(UtxoVout::Disprove)?,
            SpendPath::ScriptSpend(0),
            Sequence::from_height(paramset.disprove_timeout_timelock),
        )
        .add_input(
            NormalSignatureKind::DisproveTimeout2,
            kickoff_txhandler.get_spendable_output(UtxoVout::KickoffFinalizer)?,
            SpendPath::ScriptSpend(0),
            DEFAULT_SEQUENCE,
        )
        .add_output(UnspentTxOut::from_partial(anchor_output(
            paramset.anchor_amount(),
        )))
        .finalize())
}
```

**File:** core/src/tx_sender_queue.rs (L165-177)
```rust
            TransactionType::Disprove => {
                self.insert_try_to_send(
                    dbtx,
                    tx_metadata,
                    signed_tx,
                    FeePayingType::NoFunding,
                    rbf_info,
                    &[],
                    &[],
                    &[],
                    &[],
                )
                .await
```

**File:** core/src/test/data/protocol_paramset.toml (L15-18)
```text
disprove_timeout_timelock = 720 # BLOCKS_PER_DAY * 5
assert_timeout_timelock = 576 # BLOCKS_PER_DAY * 4
operator_reimburse_timelock = 12 # BLOCKS_PER_HOUR * 2
watchtower_challenge_timeout_timelock = 288 # BLOCKS_PER_DAY * 2
```

**File:** crates/clementine-config/src/tx_sender.rs (L26-26)
```rust
            fee_rate_hard_cap: 100,
```

**File:** scripts/docker/configs/regtest/.env.regtest (L80-80)
```text
TX_SENDER_FEE_RATE_HARD_CAP=100
```
