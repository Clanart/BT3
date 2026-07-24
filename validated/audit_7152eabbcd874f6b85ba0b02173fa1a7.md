### Title
`transfer_outpoints_to_wallet` fails to protect `ReimburseInRound` UTXOs, enabling permanent lock of bridged BTC — (`core/src/operator.rs`)

### Summary

The `TransferToBtcWallet` gRPC endpoint and its backing `transfer_outpoints_to_wallet` function protect only collateral UTXOs from being swept by the operator. The round transaction also creates `ReimburseInRound` UTXOs that pay to the operator's identical plain taproot address and are required inputs to the reimburse transaction. Because they are absent from the blocklist, the operator can sweep them, making the reimburse transaction unbroadcastable and permanently locking the `DepositInMove` UTXO that holds the bridged BTC.

### Finding Description

`transfer_outpoints_to_wallet` builds its blocklist by calling `get_all_collateral_outpoints()`, which enumerates three categories of protected outpoints:

1. The initial `collateral_funding_outpoint`
2. `CollateralInRound` (vout 0) for every round tx
3. `CollateralInReadyToReimburse` (vout 0) for every ready-to-reimburse tx [1](#0-0) 

The round transaction also produces `ReimburseInRound` UTXOs at vout `num_kickoffs_per_round + idx + 1` for each kickoff index. These are constructed with no scripts and the operator's xonly key as the sole internal key — identical to the operator's plain taproot address: [2](#0-1) 

Because their `script_pubkey` matches `self.operator.signer.address.script_pubkey()`, they pass the address check in the RPC handler: [3](#0-2) 

They are not in the collateral blocklist, so `transfer_outpoints_to_wallet` will sign and broadcast a transaction spending them without error.

The reimburse transaction requires `ReimburseInRound` as its third input via a key-path spend: [4](#0-3) 

If that UTXO is already spent, `create_reimburse_txhandler` will fail to construct the transaction, and the `DepositInMove` UTXO — the only pre-signed spending path for the bridged BTC locked in the move-to-vault transaction — becomes permanently unspendable. [5](#0-4) 

The `UtxoVout` enum confirms the exact vout positions involved: [6](#0-5) 

### Impact Explanation

Sweeping any `ReimburseInRound` UTXO for a given kickoff index:

1. Prevents the reimburse transaction for that kickoff from being constructed or broadcast.
2. Permanently locks the `DepositInMove` UTXO (the bridged BTC amount) because the reimburse tx is the sole pre-signed spending path for it.
3. Causes the operator to forfeit their reimbursement for a payout they already made.

The result is permanent loss/lock of bridge-controlled BTC — a material bridge safety impact.

### Likelihood Explanation

`TransferToBtcWallet` is an operator-facing management RPC exposed via the CLI (`TransferToBtcWallet` subcommand). An operator consolidating UTXOs from their taproot address could accidentally include a `ReimburseInRound` outpoint. The function's documentation says it "checks if any outpoint is the collateral of the operator" but makes no mention of reimburse connector UTXOs, creating a false sense of completeness. The gap is invisible to an operator who does not know the internal vout layout of the round transaction. [7](#0-6) 

### Recommendation

Extend `get_all_collateral_outpoints()` to also enumerate all `ReimburseInRound` outpoints for every round transaction. For each round, iterate over `0..num_kickoffs_per_round` and insert `OutPoint { txid: *round_tx.get_txid(), vout: UtxoVout::ReimburseInRound(idx, num_kickoffs_per_round).get_vout() }` into the protected map. [8](#0-7) 

### Proof of Concept

1. A round transaction is confirmed on-chain. Its outputs include `CollateralInRound` (vout 0, protected) and `ReimburseInRound(0)` (vout `num_kickoffs_per_round + 1`, unprotected), both paying to the operator's plain taproot address.
2. The operator calls `TransferToBtcWallet` with the `ReimburseInRound(0)` outpoint.
3. The RPC handler fetches the txout, confirms `script_pubkey == operator.signer.address.script_pubkey()` — check passes.
4. `transfer_outpoints_to_wallet` calls `get_all_collateral_outpoints()`, which does not contain this outpoint — check passes.
5. The function signs and broadcasts a key-path spend of `ReimburseInRound(0)` to the operator's BTC wallet.
6. Later, when the operator attempts to construct the reimburse transaction for kickoff index 0, `create_reimburse_txhandler` calls `round_txhandler.get_spendable_output(UtxoVout::ReimburseInRound(0, ...))` — the UTXO is already spent, so the transaction cannot be broadcast.
7. The `DepositInMove` UTXO (bridged BTC) is permanently locked with no remaining spending path.

### Citations

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

**File:** core/src/rpc/operator.rs (L522-528)
```rust
            if txout.script_pubkey != self.operator.signer.address.script_pubkey() {
                return Err(Status::invalid_argument(format!(
                    "Outpoint script_pubkey does not match operator's address. Expected: {:?}, Got: {:?}",
                    self.operator.signer.address.script_pubkey(),
                    txout.script_pubkey
                )));
            }
```

**File:** core/src/builder/transaction/operator_reimburse.rs (L349-356)
```rust
    let builder = TxHandlerBuilder::new(TransactionType::Reimburse)
        .with_version(NON_STANDARD_V3)
        .add_input(
            NormalSignatureKind::Reimburse1,
            move_txhandler.get_spendable_output(UtxoVout::DepositInMove)?,
            builder::script::SpendPath::ScriptSpend(0),
            DEFAULT_SEQUENCE,
        )
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

**File:** crates/clementine-primitives/src/lib.rs (L208-216)
```rust
    /// The vout of the reimburse connector utxo in RoundTx
    ReimburseInRound(usize, usize),
    /// The vout of the kickoff utxo in RoundTx
    Kickoff(usize),
    /// The vout of the collateral utxo in RoundTx
    CollateralInRound,
    /// The vout of the collateral utxo in ReadyToReimburseTx
    CollateralInReadyToReimburse,
}
```

**File:** core/src/rpc/clementine.proto (L364-373)
```text
  // Sends the given outpoints to the operator's btc wallet.
  // The transaction will also be broadcasted to the network.
  // Each outpoint must pay to the operator's taproot address (xonly key, no
  // merkle root). The rpc also checks if any outpoint is the collateral of the
  // operator, and rejects the request if so. # Parameters
  // - outpoints: The outpoints to send to the operator's btc wallet
  // # Returns
  // - Raw signed tx that transfers the given outpoints to the operator's btc
  // wallet address
  rpc TransferToBtcWallet(Outpoints) returns (RawSignedTx) {}
```
