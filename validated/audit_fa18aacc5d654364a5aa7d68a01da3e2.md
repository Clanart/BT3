### Title
`Disprove` Transaction Uses `FeePayingType::NoFunding`, Preventing Fee Bumping and Allowing Malicious Operator to Escape Collateral Slashing via `DisproveTimeout` - (`core/src/tx_sender_queue.rs`)

---

### Summary

The `Disprove` transaction — the critical BitVM transaction that burns a malicious operator's collateral — is registered in TxSender with `FeePayingType::NoFunding`. This means its fee is fixed at pre-signing time and can never be bumped (no RBF, no CPFP). The competing `DisproveTimeout` transaction, which the operator sends to escape slashing after `disprove_timeout_timelock` blocks, uses `FeePayingType::CPFP` and can be fee-bumped at will. During a period of elevated Bitcoin fees, the Disprove transaction stalls in the mempool while the operator's DisproveTimeout confirms, allowing the operator to escape collateral slashing.

---

### Finding Description

In `core/src/tx_sender_queue.rs`, the `add_tx_to_queue` function maps every `TransactionType` to a `FeePayingType`. The `Disprove` branch is:

```rust
TransactionType::Disprove => {
    self.insert_try_to_send(
        dbtx,
        tx_metadata,
        signed_tx,
        FeePayingType::NoFunding,   // ← fixed fee, no bumping
        rbf_info,
        &[], &[], &[], &[],
    )
    .await
}
``` [1](#0-0) 

`FeePayingType::NoFunding` routes to `send_no_funding_tx`, which calls `send_raw_transaction` once and never retries with a higher fee: [2](#0-1) 

The Disprove transaction itself is constructed as a **version-2** transaction with `DEFAULT_SEQUENCE` on both inputs (no RBF signal) and a single `non_ephemeral_anchor_output`: [3](#0-2) 

The fee is therefore permanently fixed as `(Disprove UTXO value + CollateralInRound value) − non_ephemeral_anchor_value`, determined at deposit pre-signing time.

The competing `DisproveTimeout` transaction — which the operator sends after `disprove_timeout_timelock` blocks to escape slashing — is a **v3** transaction registered with `FeePayingType::CPFP`: [4](#0-3) [5](#0-4) 

The `disprove_timeout_timelock` is **720 blocks (~5 days)** on testnet4/mainnet: [6](#0-5) 

The asymmetry is complete: verifiers' Disprove tx has a fixed fee and no bumping path; the operator's DisproveTimeout tx has CPFP and can always outbid the mempool.

---

### Impact Explanation

If Bitcoin fees spike above the pre-signed Disprove fee rate during the 720-block window, the Disprove transaction stalls in the mempool. After the timelock expires, the operator broadcasts DisproveTimeout with a CPFP child at the current market rate, which confirms. The operator's collateral (`collateral_funding_amount` = 99,000,000 sats in the reference paramset, likely larger in production) is **not burned** despite proven fraud. The bridge's fraud-deterrence mechanism — the entire economic security of the BitVM challenge game — is defeated. [7](#0-6) 

---

### Likelihood Explanation

A malicious operator who has already committed fraud has a strong financial incentive to prevent the Disprove transaction from confirming. They can:

1. **Time the fraud** during naturally high-fee periods (e.g., inscription waves, market volatility).
2. **Actively inflate fees** by broadcasting many high-fee transactions from their own wallet to push the mempool minimum above the pre-signed Disprove fee rate for 720 blocks.

The 5-day window is long enough that sustained fee pressure is feasible. The operator's DisproveTimeout is CPFP-enabled, so they can always outbid the market regardless of fee levels.

---

### Recommendation

Change `FeePayingType::NoFunding` to `FeePayingType::CPFP` for `TransactionType::Disprove` in `core/src/tx_sender_queue.rs`. The Disprove transaction already includes a `non_ephemeral_anchor_output` precisely to enable CPFP spending; TxSender simply needs to be told to use it. The `find_p2a_vout` logic in `crates/clementine-tx-sender/src/cpfp.rs` will locate the anchor and create a fee-paying child transaction at the current market rate, matching the behavior of all other time-sensitive bridge transactions. [8](#0-7) 

---

### Proof of Concept

1. Operator commits fraud: sends a kickoff with an invalid BitVM proof.
2. Verifiers detect the fraud, construct the Disprove transaction, and call `send_disprove_tx` → `add_tx_to_queue(TransactionType::Disprove, …)` → `FeePayingType::NoFunding`.
3. The Disprove transaction enters the mempool with a fee rate fixed at deposit-signing time (e.g., 2 sat/vB).
4. Operator (or a colluding miner) floods the mempool, pushing the minimum relay fee above 2 sat/vB.
5. TxSender calls `send_no_funding_tx` on every new block but only re-broadcasts the same transaction — no fee increase occurs.
6. After 720 blocks, the operator broadcasts DisproveTimeout. TxSender registers it as `FeePayingType::CPFP` and creates a child transaction at the current market rate (e.g., 50 sat/vB). DisproveTimeout confirms.
7. The Disprove UTXO is now spent by DisproveTimeout; the Disprove transaction is permanently invalid. Operator collateral is **not** burned. [9](#0-8) [10](#0-9)

### Citations

**File:** core/src/tx_sender_queue.rs (L57-91)
```rust
        match tx_type {
            TransactionType::Kickoff
            | TransactionType::Dummy
            | TransactionType::ChallengeTimeout
            | TransactionType::DisproveTimeout
            | TransactionType::Reimburse
            | TransactionType::Round
            | TransactionType::OperatorChallengeNack(_)
            | TransactionType::UnspentKickoff(_)
            | TransactionType::MoveToVault
            | TransactionType::BurnUnusedKickoffConnectors
            | TransactionType::KickoffNotFinalized
            | TransactionType::MiniAssert(_)
            | TransactionType::LatestBlockhashTimeout
            | TransactionType::LatestBlockhash
            | TransactionType::EmergencyStop
            | TransactionType::OptimisticPayout
            | TransactionType::ReadyToReimburse
            | TransactionType::ReplacementDeposit
            | TransactionType::WatchtowerChallenge(_)
            | TransactionType::AssertTimeout(_) => {
                // no_dependency and cpfp
                self.insert_try_to_send(
                    dbtx,
                    tx_metadata,
                    signed_tx,
                    FeePayingType::CPFP,
                    rbf_info,
                    &[],
                    &[],
                    &[],
                    &[],
                )
                .await
            }
```

**File:** core/src/tx_sender_queue.rs (L165-178)
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
            }
```

**File:** crates/clementine-tx-sender/src/lib.rs (L448-448)
```rust
                FeePayingType::NoFunding => self.send_no_funding_tx(id, tx, tx_metadata).await,
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

**File:** core/src/builder/transaction/challenge.rs (L272-294)
```rust
pub fn create_disprove_txhandler(
    kickoff_txhandler: &TxHandler,
    round_txhandler: &TxHandler,
) -> Result<TxHandler, BridgeError> {
    Ok(TxHandlerBuilder::new(TransactionType::Disprove)
        .with_version(Version::TWO)
        .add_input(
            NormalSignatureKind::NoSignature,
            kickoff_txhandler.get_spendable_output(UtxoVout::Disprove)?,
            SpendPath::Unknown,
            DEFAULT_SEQUENCE,
        )
        .add_input(
            NormalSignatureKind::Disprove2,
            round_txhandler.get_spendable_output(UtxoVout::CollateralInRound)?,
            SpendPath::KeySpend,
            DEFAULT_SEQUENCE,
        )
        .add_output(UnspentTxOut::from_partial(
            builder::transaction::non_ephemeral_anchor_output(), // must be non-ephemeral, because tx is v2
        ))
        .finalize())
}
```

**File:** core/src/builder/transaction/operator_assert.rs (L31-53)
```rust
pub fn create_disprove_timeout_txhandler(
    kickoff_txhandler: &TxHandler,
    paramset: &'static ProtocolParamset,
) -> Result<TxHandler<Unsigned>, BridgeError> {
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

**File:** scripts/docker/configs/testnet4/protocol_paramset.toml (L15-15)
```text
disprove_timeout_timelock = 720 # BLOCKS_PER_DAY * 5
```

**File:** crates/clementine-config/src/protocol.rs (L194-221)
```rust
pub const REGTEST_PARAMSET: ProtocolParamset = ProtocolParamset {
    network: Network::Regtest,
    num_round_txs: 2,
    num_kickoffs_per_round: 10,
    num_signed_kickoffs: 2,
    bridge_amount: Amount::from_sat(1_000_000_000),
    kickoff_amount: Amount::from_sat(0),
    operator_challenge_amount: Amount::from_sat(200_000_000),
    collateral_funding_amount: Amount::from_sat(99_000_000),
    watchtower_challenge_bytes: 144,
    kickoff_blockhash_commit_length: 40,
    winternitz_log_d: WINTERNITZ_LOG_D,
    user_takes_after: 200,
    operator_challenge_timeout_timelock: 4 * BLOCKS_PER_HOUR,
    operator_challenge_nack_timelock: 4 * BLOCKS_PER_HOUR * 3,
    disprove_timeout_timelock: 4 * BLOCKS_PER_HOUR * 5,
    assert_timeout_timelock: 4 * BLOCKS_PER_HOUR * 4,
    operator_reimburse_timelock: 2,
    watchtower_challenge_timeout_timelock: 4 * BLOCKS_PER_HOUR * 2,
    latest_blockhash_timeout_timelock: 4 * BLOCKS_PER_HOUR * 5 / 2,
    finality_depth: 5, // citrea e2e finality depth
    start_height: 190,
    genesis_height: 0,
    genesis_chain_state_hash: [
        95, 115, 2, 173, 22, 200, 189, 158, 242, 243, 190, 0, 200, 25, 154, 134, 249, 224, 186,
        134, 20, 132, 171, 180, 175, 95, 126, 69, 127, 140, 34, 22,
    ],
    bridge_nonstandard: true,
```

**File:** crates/clementine-tx-sender/src/cpfp.rs (L338-363)
```rust
    async fn create_package(
        &self,
        tx: Transaction,
        fee_rate: FeeRateKvb,
        fee_payer_utxos: Vec<crate::SpendableUtxo>,
    ) -> Result<Vec<Transaction>> {
        let txid = tx.compute_txid();
        let p2a_vout = self
            .find_p2a_vout(&tx)
            .map_err(|e: BridgeError| SendTxError::Other(e.into()))?;
        let anchor_sat = tx.output[p2a_vout].value;

        let child_tx = self
            .create_child_tx(
                OutPoint {
                    txid,
                    vout: p2a_vout as u32,
                },
                anchor_sat,
                fee_payer_utxos,
                tx.weight(),
                fee_rate,
            )
            .await?;

        Ok(vec![tx, child_tx])
```

**File:** core/src/verifier.rs (L3033-3050)
```rust
        self.tx_sender
            .add_tx_to_queue(
                dbtx,
                TransactionType::Disprove,
                &disprove_tx,
                &[],
                Some(TxMetadata {
                    tx_type: TransactionType::Disprove,
                    deposit_outpoint: Some(deposit_data.get_deposit_outpoint()),
                    operator_xonly_pk: Some(kickoff_data.operator_xonly_pk),
                    round_idx: Some(kickoff_data.round_idx),
                    kickoff_idx: Some(kickoff_data.kickoff_idx),
                }),
                self.config.protocol_paramset(),
                None,
            )
            .await?;
        Ok(())
```
