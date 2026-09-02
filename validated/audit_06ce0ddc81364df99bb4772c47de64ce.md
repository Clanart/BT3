### Title
Deadline-bound `disprove_tx` uses a non-ephemeral, anyone-can-spend P2A anchor without TRUC (v3) topology protection, letting an attacker pin its CPFP path before `disprove_timeout_timelock` matures - (File: `core/src/builder/transaction/challenge.rs`, `crates/clementine-tx-sender/src/cpfp.rs`)

### Summary
Every deadline-bound protocol transaction in the codebase (`kickoff`, `challenge_timeout`, `disprove_timeout`, `watchtower_challenge_timeout`, `assert_timeout`, `latest_blockhash_timeout`, `operator_challenge_nack`, `unspent_kickoff`, `kickoff_not_finalized`) is built with `NON_STANDARD_V3` (BIP-431/TRUC), which restricts mempool topology to at most one unconfirmed ancestor/descendant and thereby defeats classic pinning attacks against the CPFP anchor. `create_disprove_txhandler` in `core/src/builder/transaction/challenge.rs` is the one deadline-bound transaction explicitly built as `Version::TWO` with a `non_ephemeral_anchor_output()`, with the comment "must be non-ephemeral, because tx is v2" [1](#0-0) . Because it is a v2 transaction, it does not receive the TRUC-mandated topology limits, so its anyone-can-spend P2A output can be pinned in the mempool by an unprivileged party before the tx-sender's own CPFP child is broadcast.

### Finding Description
The broken binding, stated as an equality, is: for every deadline-bound bridge transaction `T` with a timelocked competing timeout path, `is_bumpable_before_deadline(T) == true` must hold. For all protocol transactions except `disprove_tx`, this holds because they are `NON_STANDARD_V3` [2](#0-1) [3](#0-2) [4](#0-3) , whose BIP-431 mempool policy caps unconfirmed ancestors/descendants to 1, preventing an attacker from stacking low-fee filler transactions on the anchor output to exhaust the package/descendant-size policy limits.

`disprove_tx`, however, is intentionally `Version::TWO` with a `non_ephemeral_anchor_output()` (a P2A output with real positive value rather than the forced-zero-fee ephemeral anchor) [1](#0-0) . A v2 transaction's anchor output is not subject to the TRUC 1-ancestor/1-descendant restriction, so once `disprove_tx` is broadcast and visible in the mempool (unconfirmed), any unprivileged party who can broadcast Bitcoin transactions and pay fees can immediately spend that anchor output (it requires no signature, only `OP_1 <0x4e73>`) with their own low-feerate, non-RBF-signaling transaction. Because the anchor output is not ephemeral, this spend is a legitimate standalone low-value/low-fee transaction that can sit in the mempool paying just above the minimum relay fee, occupying the single spend of that UTXO.

The tx-sender's CPFP path (`send_cpfp_tx` / `create_package` in `crates/clementine-tx-sender/src/cpfp.rs`) builds its own child transaction spending the same P2A output identified via `find_p2a_vout` [5](#0-4)  and submits it via `submitpackage` [6](#0-5) . If the attacker's non-replaceable spend of the anchor is already in the mempool, this submission collides on the same outpoint. Nothing in `create_package`, `create_child_tx`, or `send_cpfp_tx` checks whether the anchor output already has a competing, non-signaling spend before constructing/submitting the child, and there is no fallback logic that detects "anchor output already spent by a foreign non-RBF transaction" and attempts to out-bid or evict it. `bump_fees_of_unconfirmed_fee_payer_txs` only manages the tx-sender's own fee-payer UTXOs' RBF bumping, not a foreign transaction occupying the bridge's own anchor output [7](#0-6) . Unless the operator/verifier's Bitcoin node has full-RBF policy enabled (not guaranteed/configured in this repo) and the tx-sender is coded to detect and replace the foreign spend (it is not), `disprove_tx`'s only fee-bumping avenue is blocked for as long as the attacker keeps the pinning transaction in the mempool (re-broadcasting/paying just enough to avoid eviction), which can be sustained past `disprove_timeout_timelock`.

If `disprove_tx` cannot confirm before `disprove_timeout_timelock` matures, the malicious operator's `disprove_timeout_tx` (which spends the same `UtxoVout::Disprove`/`KickoffFinalizer` outputs of the kickoff, per `create_disprove_timeout_txhandler` in `core/src/builder/transaction/operator_assert.rs`) becomes spendable first, letting the operator escape the burn/disprove path entirely.

### Impact Explanation
If exploited, a malicious operator's collateral (burn connector) is never burned despite the N-of-N having a valid, provable disprove — matching the "High" category "a deadline-bound challenge/disprove/timeout transaction made unconfirmable by attacker-shaped chain data." This can be repeated for every disprove attempt against any operator, and the cost to the attacker is only mempool relay fees for a minimal-size low-fee transaction, which is cheap relative to the operator's collateral and the bridged funds at stake. The attacker gains nothing directly monetarily from this specific pin (unless colluding with the malicious operator), but it destroys the bridge's core disincentive mechanism (burning malicious-operator collateral), enabling repeated fraudulent reimbursement claims across deposits.

### Likelihood Explanation
Exploitation requires only that (1) `disprove_tx` be broadcast into a public mempool ahead of `disprove_timeout_timelock` maturing (an inherent step of the challenge/disprove protocol) and (2) the attacker be first to spend the anchor with a non-signaling transaction, which is trivially achievable by any mempool-watching, fee-paying party — no privileged role, key material, or majority hashrate is needed. The main uncertainty is operational: whether the operator/verifier's Bitcoin Core node runs with `mempoolfullrbf=1` (default since Core v28) would let the tx-sender out-bid the pin via full-RBF, but the current tx-sender code in this repo has no logic that specifically detects and replaces a foreign non-signaling spend of the P2A anchor, so the mitigation, if any, depends entirely on node configuration rather than the protocol/code itself.

### Recommendation
Make `disprove_tx` (and any other v2, non-ephemeral-anchor deadline transaction) use `NON_STANDARD_V3` with an ephemeral P2A anchor like all other deadline-bound transactions, so BIP-431 topology limits prevent pinning of its CPFP path. If v2 is unavoidable for `disprove_tx`, add explicit tx-sender logic to detect when the anchor UTXO is already spent by a non-cooperating mempool transaction and actively attempt to replace it (require/verify `mempoolfullrbf=1` on the operator/verifier node, or fund a competing higher-feerate replacement using `bumpfee`/RBF against the foreign spend), and add monitoring/alerting when `disprove_tx` is stuck near the timeout deadline.

### Proof of Concept
```
#[tokio::test]
async fn disprove_tx_anchor_pinning_blocks_cpfp_before_timeout() {
    // 1. Set up regtest tx-sender + bitcoind as in existing cpfp tests (see core/src/test/txsender.rs).
    // 2. Construct and broadcast a disprove_tx equivalent: a Version::TWO transaction with a
    //    non_ephemeral_anchor_output() P2A output, spending a dummy prevout, at a fee rate too low to confirm quickly.
    // 3. As an unprivileged actor, immediately spend the P2A output with a separate,
    //    non-RBF-signaling (nSequence = 0xffffffff) low-feerate transaction.
    // 4. Insert disprove_tx into tx-sender via TxSenderClient::insert_try_to_send with FeePayingType::CPFP.
    // 5. Run TxSenderTaskInternal::run_once() repeatedly (simulating time until disprove_timeout_timelock blocks pass).
    // 6. Assert: disprove_tx's CPFP child submission (send_cpfp_tx) fails/returns an error each iteration
    //    because the anchor output is already spent by the foreign transaction (submitpackage returns a conflict).
    // 7. Assert: disprove_tx remains unconfirmed after mining `disprove_timeout_timelock` blocks,
    //    while a corresponding disprove_timeout_tx (spending the same KickoffFinalizer/Disprove outputs)
    //    becomes spendable, demonstrating the binding "is_bumpable_before_deadline(disprove_tx) == true" is false.
}
```

### Citations

**File:** core/src/builder/transaction/challenge.rs (L272-293)
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

**File:** core/src/builder/transaction/operator_reimburse.rs (L420-433)
```rust
    let mut txhandler = TxHandlerBuilder::new(TransactionType::Payout)
        .with_version(NON_STANDARD_V3)
        .add_input(
            NormalSignatureKind::NotStored,
            txin,
            SpendPath::KeySpend,
            DEFAULT_SEQUENCE,
        )
        .add_output(output_txout)
        .add_output(UnspentTxOut::from_partial(anchor_output(
            NON_EPHEMERAL_ANCHOR_AMOUNT,
        )))
        .add_output(UnspentTxOut::from_partial(op_return_txout))
        .finalize();
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

**File:** crates/clementine-tx-sender/src/cpfp.rs (L338-364)
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
    }
```

**File:** crates/clementine-tx-sender/src/cpfp.rs (L434-549)
```rust
    pub async fn bump_fees_of_unconfirmed_fee_payer_txs(&self, fee_rate: FeeRateKvb) -> Result<()> {
        let bumpable_txs = self
            .db
            .get_all_unconfirmed_fee_payer_txs(None)
            .await
            .map_err(|e: BridgeError| SendTxError::Other(e.into()))?;
        let mut not_evicted_ids = HashSet::new();
        let mut all_parent_ids = HashSet::new();

        for (id, try_to_send_id, fee_payer_txid, vout, amount, replacement_of_id) in bumpable_txs {
            tracing::debug!(
                "Bumping fee for fee payer tx {} for try to send id {} for fee rate {}",
                fee_payer_txid,
                try_to_send_id,
                fee_rate
            );
            let parent_id = replacement_of_id.unwrap_or(id);
            all_parent_ids.insert(parent_id);

            match self.rpc.get_mempool_entry(&fee_payer_txid).await {
                Ok(info) => {
                    not_evicted_ids.insert(parent_id);
                    // if it has descendants, it cannot be bumped, or if it was bumped recently, we should not bump it again
                    if info.descendant_count > 1
                        || std::time::SystemTime::now()
                            .duration_since(std::time::UNIX_EPOCH)
                            .unwrap()
                            .as_secs()
                            .saturating_sub(info.time)
                            < self.tx_sender_limits.cpfp_fee_payer_bump_wait_time_seconds
                    {
                        continue;
                    }
                }
                Err(e) => {
                    // If not in mempool we should ignore, it was either evicted or replaced by a bumped feepayer tx
                    // give an error if the error is not "Transaction not in mempool"
                    if !e.to_string().contains("Transaction not in mempool") {
                        return Err(
                            eyre!("Failed to get mempool entry for {fee_payer_txid}: {e}").into(),
                        );
                    }
                    // get_transaction only returns if tx is wallet owned, it should not be an issue here as if it is not wallet owned,
                    // for example if wallet was changed and txsender restarted, it cannot be bumped anyway
                    if let Ok(tx_info) = self.rpc.get_transaction(&fee_payer_txid, None).await {
                        if tx_info.info.blockhash.is_some() && tx_info.info.confirmations > 0 {
                            not_evicted_ids.insert(parent_id);
                        }
                    }
                    continue;
                }
            }

            match self
                .rpc
                .bump_fee_with_fee_rate(fee_payer_txid, fee_rate)
                .await
            {
                Ok(new_txid) => {
                    if new_txid != fee_payer_txid {
                        self.db
                            .save_fee_payer_tx(
                                None,
                                try_to_send_id,
                                new_txid,
                                vout,
                                amount,
                                Some(parent_id),
                            )
                            .await
                            .map_err(|e: BridgeError| SendTxError::Other(e.into()))?;
                    } else {
                        tracing::trace!(
                            "Fee payer tx {} has enough fee, no need to bump",
                            fee_payer_txid
                        );
                    }
                }
                Err(e) => match e {
                    BitcoinRPCError::TransactionAlreadyInBlock(block_hash) => {
                        tracing::debug!(
                            "Fee payer tx {} is already in block {}, skipping",
                            fee_payer_txid,
                            block_hash
                        );
                        continue;
                    }
                    BitcoinRPCError::BumpFeeUTXOSpent(outpoint) => {
                        tracing::debug!(
                            "Fee payer tx {} is already onchain, skipping: {:?}",
                            fee_payer_txid,
                            outpoint
                        );
                        continue;
                    }
                    _ => {
                        tracing::warn!(
                            "Failed to bump fee the fee payer tx {} with error {e}, skipping",
                            fee_payer_txid
                        );
                        continue;
                    }
                },
            }
        }

        for parent_id in all_parent_ids {
            if !not_evicted_ids.contains(&parent_id) {
                self.db
                    .mark_fee_payer_utxo_as_evicted(None, parent_id)
                    .await
                    .map_err(|e: BridgeError| SendTxError::Other(e.into()))?;
            }
        }
        Ok(())
    }
```

**File:** crates/clementine-tx-sender/src/cpfp.rs (L675-679)
```rust
        let submit_result = self
            .rpc
            .submit_package(&package_refs, Some(Amount::ZERO), None)
            .await
            .wrap_err("Failed to submit package")?;
```
