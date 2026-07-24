### Title
Verifier Suppresses Automatic Challenge Submission on Mainnet/Testnet4, Allowing Malicious Operator to Drain Bridge Vault — (File: `core/src/verifier.rs`)

---

### Summary

`Verifier::handle_kickoff` detects a fraudulent kickoff and confirms it is malicious via `is_kickoff_malicious`, but then unconditionally skips automatic submission of the `Challenge` transaction on `bitcoin::Network::Bitcoin` (mainnet) and `bitcoin::Network::Testnet4`. If no human operator manually broadcasts the challenge within the `operator_challenge_timeout_timelock` window, the malicious operator can send `ChallengeTimeout`, proceed unchallenged, and claim reimbursement from the bridge vault for a payout they never made — stealing bridged BTC.

---

### Finding Description

In `core/src/verifier.rs`, the `handle_kickoff` function first confirms the kickoff is fraudulent: [1](#0-0) 

Then, despite `is_malicious = true`, a network gate prevents the automatic protective response on the two networks where real funds are at stake: [2](#0-1) 

The only action taken on mainnet/testnet4 is a `tracing::warn!` log that includes the challenge tx hex. No transaction is queued. The comment reads: *"do not automatically send challenge txs on mainnet or testnet4"* — but no fallback enforcement mechanism exists.

The `ChallengeTimeout` transaction is pre-signed and spends the `Challenge` UTXO after `operator_challenge_timeout_timelock` blocks: [3](#0-2) 

If the challenge is not sent before this timelock expires, the operator sends `ChallengeTimeout`, the `KickoffFinalizer` is spent, the `KickoffStateMachine` transitions to `closed`, and the operator proceeds to claim reimbursement from the bridge vault via `ReadyToReimburse` — for a payout they never made.

The `is_kickoff_malicious` check already validates:
- No payout info found in DB for the move txid (operator never paid)
- Operator xonly pk mismatch
- Committed blockhash mismatch with payout blockhash [4](#0-3) 

The gate at lines 1999–2003 is therefore not a false-positive guard — it suppresses a confirmed-malicious response.

The `KickoffStateMachine` only dispatches `Duty::VerifierDisprove` after the kickoff is `challenged` (i.e., after the `Challenge` UTXO is spent by a non-timeout tx). If the challenge is never sent, the state machine never enters the `challenged` state and never attempts to disprove: [5](#0-4) [6](#0-5) 

---

### Impact Explanation

A malicious operator on mainnet or testnet4 can:

1. Send a kickoff transaction claiming reimbursement for a withdrawal they never paid.
2. The verifier detects `is_malicious = true` but does **not** queue the `Challenge` tx.
3. After `operator_challenge_timeout_timelock` blocks with no challenge on-chain, the operator sends `ChallengeTimeout`.
4. The `KickoffFinalizer` is spent, the kickoff is considered finalized, and the operator claims reimbursement from the bridge vault.
5. Bridged BTC (the full `bridge_amount` per deposit) is stolen from the vault.

This is a direct theft of bridge-controlled UTXOs — the highest-severity impact class.

---

### Likelihood Explanation

- Requires a malicious operator (a protocol participant who has already posted collateral and participated in setup).
- Requires no human to monitor verifier logs and manually broadcast the challenge within the timelock window.
- On mainnet, timelocks are measured in days/weeks, but 24/7 human monitoring is not guaranteed.
- The verifier automation is explicitly enabled (`#[cfg(feature = "automation")]`) yet the challenge — the most critical automated response — is suppressed on the networks where it matters most.
- The analog to M-02 is exact: a gate that should not apply to a protective action is applied, creating a window for loss.

---

### Recommendation

Remove the network check that suppresses automatic challenge submission. The `is_kickoff_malicious` check already provides the necessary validation. The challenge must be sent automatically on all networks when a malicious kickoff is confirmed:

```rust
// Before (broken):
if !challenged_before
    && !matches!(
        self.config.protocol_paramset().network,
        bitcoin::Network::Bitcoin | bitcoin::Network::Testnet4
    )
{
    #[cfg(feature = "automation")]
    self.tx_sender.add_tx_to_queue(...).await?;
}

// After (fixed):
if !challenged_before {
    #[cfg(feature = "automation")]
    self.tx_sender.add_tx_to_queue(...).await?;
}
```

If human review is desired before challenging on mainnet, implement an out-of-band alert (e.g., PagerDuty, webhook) and a short-circuit timer that auto-challenges if no human action is taken within a safe sub-window of the timelock — rather than suppressing the automatic response entirely.

---

### Proof of Concept

1. Deploy Clementine on `bitcoin::Network::Bitcoin` (mainnet) with `automation` feature enabled.
2. Malicious operator sends a kickoff transaction with a fraudulent payout claim (e.g., claiming reimbursement for a deposit they never paid out).
3. Verifier's `handle_kickoff` calls `is_kickoff_malicious` → returns `true`.
4. Network check at line 2000 evaluates `matches!(network, Bitcoin)` → `true` → the entire `if` block is skipped.
5. Only a `tracing::warn!` log is emitted with the challenge tx hex.
6. No human monitors the log within `operator_challenge_timeout_timelock` blocks.
7. Operator broadcasts `ChallengeTimeout`, spending the `Challenge` UTXO.
8. `KickoffStateMachine` receives `KickoffFinalizerSpent` → transitions to `closed`.
9. Operator sends `ReadyToReimburse` and claims reimbursement from the bridge vault.
10. Bridged BTC equal to `bridge_amount` is transferred out of the vault to the malicious operator.

### Citations

**File:** core/src/verifier.rs (L1857-1914)
```rust
    /// Checks if the operator who sent the kickoff matches the payout data saved in our db
    /// Payout data in db is updated during citrea sync.
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

**File:** core/src/verifier.rs (L1977-1979)
```rust
        let is_malicious = self
            .is_kickoff_malicious(kickoff_witness, &mut deposit_data, kickoff_data, dbtx)
            .await?;
```

**File:** core/src/verifier.rs (L1998-2016)
```rust
            // do not automatically send challenge txs on mainnet or testnet4
            if !challenged_before
                && !matches!(
                    self.config.protocol_paramset().network,
                    bitcoin::Network::Bitcoin | bitcoin::Network::Testnet4
                )
            {
                #[cfg(feature = "automation")]
                self.tx_sender
                    .add_tx_to_queue(
                        dbtx,
                        TransactionType::Challenge,
                        &challenge_tx,
                        &[],
                        Some(tx_metadata),
                        self.config.protocol_paramset(),
                        None,
                    )
                    .await?;
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

**File:** core/src/states/kickoff.rs (L260-270)
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
```

**File:** core/src/states/kickoff.rs (L594-598)
```rust
        match event {
            KickoffEvent::Challenged => {
                tracing::warn!("Warning: Operator challenged: {}", self.kickoff_data);
                Transition(State::challenged())
            }
```
