Looking at the `get_all_collateral_outpoints` function and `transfer_outpoints_to_wallet` in `core/src/operator.rs`, and the round tx structure in `core/src/builder/transaction/operator_collateral.rs`, I need to verify exactly which UTXOs at the operator's plain taproot address are and are not protected.### Title
`get_all_collateral_outpoints` omits `ReimburseInRound` connector UTXOs, allowing `transfer_to_btc_wallet` to permanently destroy operator reimbursement outputs — (`core/src/operator.rs`)

---

### Summary

`get_all_collateral_outpoints` only protects the collateral chain (`collateral_funding_outpoint` → `CollateralInRound` → `CollateralInReadyToReimburse`). It does **not** protect the `ReimburseInRound` connector UTXOs that are emitted by every round tx at the operator's plain taproot address (no scripts). Because `transfer_to_btc_wallet` allows spending any UTXO at that address that is absent from `get_all_collateral_outpoints`, the operator can sweep those connectors to their wallet. Once spent, the `reimburse_tx` cannot be constructed, permanently blocking recovery of the `bridge_amount` (≈1 BTC) the operator fronted for the withdrawal.

---

### Finding Description

**Round tx output layout** (`create_round_txhandler`):

| vout | `UtxoVout` | Script | Address |
|------|-----------|--------|---------|
| 0 | `CollateralInRound` | none | operator plain taproot ← **protected** |
| 1…K | `Kickoff(i)` | Winternitz commit + timelock | *different* taproot address |
| K+1…2K | `ReimburseInRound(i, K)` | none | operator plain taproot ← **NOT protected** |
| last | anchor | P2A | different | [1](#0-0) 

The reimburse connector outputs are created with `from_scripts(default_utxo_amount(), vec![], Some(operator_xonly_pk), network)` — identical `script_pubkey` to `signer.address`. [2](#0-1) 

**`get_all_collateral_outpoints` only inserts `CollateralInRound` and `CollateralInReadyToReimburse`** for each round; `ReimburseInRound` outpoints are never added to the protection map. [3](#0-2) 

**`transfer_to_btc_wallet` RPC** accepts any outpoint whose `script_pubkey` matches `signer.address` and that is absent from `get_all_collateral_outpoints`. Both conditions are satisfied by `ReimburseInRound` UTXOs. [4](#0-3) 

**`create_reimburse_txhandler`** requires `UtxoVout::ReimburseInRound(kickoff_idx, num_kickoffs_per_round)` as its third input (key-spend path, signed by operator). If that UTXO is already spent, the reimburse tx is unbroadcastable. [5](#0-4) 

The reimbursement output value equals the full `bridge_amount` from the `MoveToVaultTx` deposit UTXO. [6](#0-5) 

---

### Impact Explanation

If any `ReimburseInRound(i, K)` UTXO is swept via `transfer_to_btc_wallet`, the corresponding `reimburse_tx` for kickoff index `i` can never be confirmed. The operator permanently loses the `bridge_amount` (≈1 BTC per deposit) they fronted for the withdrawal. This is a direct, irreversible loss of reimbursement outputs — explicitly within the allowed impact scope.

---

### Likelihood Explanation

The trigger is the operator (or any party with valid mTLS credentials to the operator gRPC) calling `TransferToBtcWallet` with a `ReimburseInRound` outpoint. This can happen:

- **Accidentally**: the operator scans their taproot address for "spare" UTXOs and includes a live reimburse connector.
- **Intentionally by a rogue operator**: the operator deliberately sweeps connectors to prevent reimbursement (e.g., to avoid a challenge-period obligation).

The RPC is operator-authenticated via mTLS, so external parties cannot trigger it without the operator's credentials. Likelihood is **medium** — the scenario is realistic during manual UTXO management.

---

### Recommendation

Extend `get_all_collateral_outpoints` to also enumerate all `ReimburseInRound(i, num_kickoffs_per_round)` outpoints for every round tx and every kickoff index `i ∈ [0, num_kickoffs_per_round)`:

```rust
// inside the per-round loop in get_all_collateral_outpoints
for kickoff_idx in 0..self.config.protocol_paramset().num_kickoffs_per_round {
    let reimburse_outpoint = OutPoint {
        txid: *round_tx.get_txid(),
        vout: UtxoVout::ReimburseInRound(
            kickoff_idx,
            self.config.protocol_paramset().num_kickoffs_per_round,
        ).get_vout(),
    };
    outpoints.insert(reimburse_outpoint, (round_idx, TransactionType::Reimburse));
}
```

This mirrors the existing protection pattern for `CollateralInRound`. [7](#0-6) 

---

### Proof of Concept

1. Operator fronts a withdrawal: pays `bridge_amount` to user, payout tx confirmed.
2. Automation sends the round tx for the relevant round. Round tx is confirmed on-chain, creating `ReimburseInRound(i, K)` at the operator's plain taproot address.
3. Operator (or rogue gRPC client with valid cert) calls:
   ```
   TransferToBtcWallet { outpoints: [<round_txid>:<reimburse_vout>] }
   ```
4. `transfer_to_btc_wallet` RPC fetches the txout, confirms `script_pubkey == signer.address.script_pubkey()` ✓, calls `get_all_collateral_outpoints()` — the outpoint is absent ✓, signs and broadcasts the sweep tx.
5. The `ReimburseInRound` UTXO is now spent to the operator's wallet address.
6. When the reimbursement flow reaches `create_reimburse_txhandler`, it tries to spend `UtxoVout::ReimburseInRound(i, K)` — already spent. Bitcoin rejects the tx as double-spend.
7. Operator cannot recover `bridge_amount` (≈1 BTC). Loss is permanent. [8](#0-7) [9](#0-8)

### Citations

**File:** core/src/builder/transaction/operator_collateral.rs (L134-142)
```rust
    // Create reimburse utxos
    for _ in 0..paramset.num_kickoffs_per_round {
        builder = builder.add_output(UnspentTxOut::from_scripts(
            paramset.default_utxo_amount(),
            vec![],
            Some(operator_xonly_pk),
            paramset.network,
        ));
    }
```

**File:** core/src/operator.rs (L1909-1923)
```rust
        // check if any outpoint is a collateral outpoint
        let collateral_outpoints = self
            .get_all_collateral_outpoints()
            .await
            .wrap_err("Failed to get all collateral outpoints")?;
        for (outpoint, _) in inputs.iter() {
            if collateral_outpoints.contains_key(outpoint) {
                let (round_idx, tx_type) = collateral_outpoints
                    .get(outpoint)
                    .expect("Collateral outpoint should be found in the map");
                return Err(
                    eyre!("Cannot transfer collateral outpoint {outpoint} belonging to {round_idx:?} {tx_type:?} to wallet").into(),
                );
            }
        }
```

**File:** core/src/operator.rs (L2022-2085)
```rust
    /// Gets all collateral outpoints for the operator.
    /// Returns a map of outpoint to the round index it belongs to.
    async fn get_all_collateral_outpoints(
        &self,
    ) -> Result<HashMap<OutPoint, (RoundIndex, TransactionType)>, BridgeError> {
        let mut outpoints = HashMap::new();
        outpoints.insert(
            self.collateral_funding_outpoint,
            (RoundIndex::Collateral, TransactionType::Round),
        );

        // Fetch operator kickoff winternitz public keys to build round txs
        let operator_winternitz_public_keys = self
            .db
            .get_operator_kickoff_winternitz_public_keys(None, self.signer.xonly_public_key)
            .await?;
        let kickoff_wpks = KickoffWinternitzKeys::new(
            operator_winternitz_public_keys,
            self.config.protocol_paramset().num_kickoffs_per_round,
            self.config.protocol_paramset().num_round_txs,
        )?;
        let operator_data = self.data();

        let mut prev_ready_to_reimburse: Option<TxHandler> = None;

        // Collect collateral outpoints for each round
        for round_idx in RoundIndex::iter_rounds(self.config.protocol_paramset().num_round_txs) {
            let txhandlers = create_round_txhandlers(
                self.config.protocol_paramset(),
                round_idx,
                &operator_data,
                &kickoff_wpks,
                prev_ready_to_reimburse.as_ref(),
            )?;

            let round_tx = txhandlers
                .iter()
                .find(|txhandler| txhandler.get_transaction_type() == TransactionType::Round)
                .ok_or(eyre::eyre!("Round tx not found in txhandlers"))?;
            let collateral_outpoint = OutPoint {
                txid: *round_tx.get_txid(),
                vout: UtxoVout::CollateralInRound.get_vout(),
            };
            outpoints.insert(collateral_outpoint, (round_idx, TransactionType::Round));

            let ready_to_reimburse_tx = txhandlers
                .iter()
                .find(|txhandler| {
                    txhandler.get_transaction_type() == TransactionType::ReadyToReimburse
                })
                .ok_or(eyre::eyre!("Ready to reimburse tx not found in txhandlers"))?;
            let ready_to_reimburse_collateral_outpoint = OutPoint {
                txid: *ready_to_reimburse_tx.get_txid(),
                vout: UtxoVout::CollateralInReadyToReimburse.get_vout(),
            };
            outpoints.insert(
                ready_to_reimburse_collateral_outpoint,
                (round_idx, TransactionType::ReadyToReimburse),
            );
            prev_ready_to_reimburse = Some(ready_to_reimburse_tx.clone());
        }

        Ok(outpoints)
    }
```

**File:** core/src/rpc/operator.rs (L509-531)
```rust
        for outpoint in outpoints {
            tracing::info!(
                "TransferToBtcWallet rpc called with outpoint: {:?}",
                outpoint
            );

            let txout = self
                .operator
                .rpc
                .get_txout_from_outpoint(&outpoint)
                .await
                .map_err(|e| Status::internal(format!("Failed to get txout: {e}")))?;

            if txout.script_pubkey != self.operator.signer.address.script_pubkey() {
                return Err(Status::invalid_argument(format!(
                    "Outpoint script_pubkey does not match operator's address. Expected: {:?}, Got: {:?}",
                    self.operator.signer.address.script_pubkey(),
                    txout.script_pubkey
                )));
            }

            inputs.push((outpoint, txout));
        }
```

**File:** core/src/builder/transaction/operator_reimburse.rs (L363-371)
```rust
        .add_input(
            NormalSignatureKind::OperatorSighashDefault,
            round_txhandler.get_spendable_output(UtxoVout::ReimburseInRound(
                kickoff_idx,
                paramset.num_kickoffs_per_round,
            ))?,
            builder::script::SpendPath::KeySpend,
            DEFAULT_SEQUENCE,
        );
```

**File:** core/src/builder/transaction/operator_reimburse.rs (L373-380)
```rust
    Ok(builder
        .add_output(UnspentTxOut::from_partial(TxOut {
            value: move_txhandler
                .get_spendable_output(UtxoVout::DepositInMove)?
                .get_prevout()
                .value,
            script_pubkey: operator_reimbursement_address.script_pubkey(),
        }))
```
