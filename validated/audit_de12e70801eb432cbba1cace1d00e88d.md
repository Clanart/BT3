### Title
`get_all_collateral_outpoints` Omits `ReimburseInRound` UTXOs, Allowing Operator to Permanently Lock Bridged BTC via `transfer_outpoints_to_wallet` — (`File: core/src/operator.rs`)

---

### Summary

`get_all_collateral_outpoints` only enumerates collateral-chain UTXOs (`CollateralInRound`, `CollateralInReadyToReimburse`) as "protected" outpoints. It silently omits the `ReimburseInRound` UTXOs that are also created in every `round_tx` at the operator's plain taproot address. Because `transfer_outpoints_to_wallet` only blocks outpoints found in that map, an operator (or anyone who can reach the gRPC endpoint) can spend a `ReimburseInRound` UTXO via `TransferToBtcWallet`. Once spent, the `reimburse_tx` is permanently invalid, and the bridged BTC locked in the `MoveToVaultTx` output (`DepositInMove`) has no remaining spending path, causing a permanent loss of deposited funds.

---

### Finding Description

**Round-tx output layout** (`create_round_txhandler`, `operator_collateral.rs`):

| vout | `UtxoVout` | Script | Address |
|---|---|---|---|
| 0 | `CollateralInRound` | key-path, operator pk | operator taproot |
| 1…K | `Kickoff(i)` | `WinternitzCommit` script | **not** operator plain address |
| K+1…2K | `ReimburseInRound(i,K)` | **no scripts**, operator pk | **operator plain taproot** |
| last | Anchor | — | — | [1](#0-0) 

The `ReimburseInRound` outputs are created with `vec![]` scripts and `Some(operator_xonly_pk)`, making them indistinguishable from any other UTXO at the operator's plain taproot address.

**`get_all_collateral_outpoints` only inserts three categories:**

1. `collateral_funding_outpoint`
2. `CollateralInRound` for every round
3. `CollateralInReadyToReimburse` for every round

`ReimburseInRound` UTXOs are never inserted. [2](#0-1) 

**`transfer_outpoints_to_wallet` guards only against the above map:**

```rust
if collateral_outpoints.contains_key(outpoint) { return Err(...); }
``` [3](#0-2) 

**The `TransferToBtcWallet` RPC** only validates that the outpoint's `script_pubkey` matches the operator's address — which `ReimburseInRound` UTXOs satisfy — and then calls `transfer_outpoints_to_wallet`. [4](#0-3) 

**`reimburse_tx` requires `ReimburseInRound` as its third input:**

```rust
round_txhandler.get_spendable_output(UtxoVout::ReimburseInRound(kickoff_idx, ...))
``` [5](#0-4) 

Once that UTXO is spent by `transfer_outpoints_to_wallet`, the pre-signed `reimburse_tx` references a spent input and is permanently invalid on-chain.

---

### Impact Explanation

The `reimburse_tx` is the only normal-path transaction that spends `DepositInMove` (the N-of-N vault holding the bridged BTC). If `ReimburseInRound` is already spent, `reimburse_tx` cannot be broadcast. The `DepositInMove` UTXO has no independent timelock recovery path visible in the codebase; the only alternative is an N-of-N optimistic payout, which requires all verifiers to cooperate out-of-band. In practice this means the bridged BTC is permanently locked — a direct loss of user funds. [6](#0-5) 

---

### Likelihood Explanation

The `TransferToBtcWallet` gRPC endpoint is an operator-facing management RPC. An operator could accidentally pass a `ReimburseInRound` outpoint (e.g., when sweeping dust UTXOs from their address), or a UI/tooling bug could enumerate all UTXOs at the operator's address and include protocol-critical ones. If the gRPC port is reachable without mTLS enforcement, any party that can connect could trigger the transfer. The missing protection is a code-level omission with no compensating guard. [7](#0-6) 

---

### Recommendation

Add `ReimburseInRound(i, num_kickoffs_per_round)` outpoints for every round and every kickoff index to the protected set inside `get_all_collateral_outpoints`:

```rust
for kickoff_idx in 0..paramset.num_kickoffs_per_round {
    let reimburse_outpoint = OutPoint {
        txid: *round_tx.get_txid(),
        vout: UtxoVout::ReimburseInRound(kickoff_idx, paramset.num_kickoffs_per_round).get_vout(),
    };
    outpoints.insert(reimburse_outpoint, (round_idx, TransactionType::Round));
}
```

Alternatively, add a blanket check: reject any outpoint whose `vout` matches a known protocol-critical slot for any round tx on-chain, rather than maintaining a manually curated allowlist. [8](#0-7) 

---

### Proof of Concept

1. Operator completes a deposit; a `round_tx` is confirmed on-chain with txid `R`.
2. Operator (or attacker reaching the gRPC port) calls:
   ```
   TransferToBtcWallet { outpoints: [R:vout=(num_kickoffs+kickoff_idx+1)] }
   ```
   where `vout` is `UtxoVout::ReimburseInRound(kickoff_idx, K).get_vout()`.
3. The RPC confirms `script_pubkey` matches the operator's address ✓, `get_all_collateral_outpoints` does not contain this outpoint ✓, transfer proceeds.
4. `ReimburseInRound` UTXO is now spent.
5. Operator sends kickoff, waits out challenge timeout, attempts `reimburse_tx` — Bitcoin rejects it: input `R:vout` is already spent.
6. `DepositInMove` (bridged BTC) is permanently unspendable via the normal protocol path. [9](#0-8) [10](#0-9)

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

**File:** core/src/operator.rs (L1901-1923)
```rust
    pub async fn transfer_outpoints_to_wallet(
        &self,
        inputs: Vec<(OutPoint, TxOut)>,
    ) -> Result<Transaction, BridgeError> {
        if inputs.is_empty() {
            return Err(eyre!("No outpoints provided for transfer").into());
        }

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

**File:** core/src/operator.rs (L2024-2085)
```rust
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

**File:** core/src/rpc/operator.rs (L522-531)
```rust
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

**File:** core/src/builder/transaction/operator_reimburse.rs (L349-385)
```rust
    let builder = TxHandlerBuilder::new(TransactionType::Reimburse)
        .with_version(NON_STANDARD_V3)
        .add_input(
            NormalSignatureKind::Reimburse1,
            move_txhandler.get_spendable_output(UtxoVout::DepositInMove)?,
            builder::script::SpendPath::ScriptSpend(0),
            DEFAULT_SEQUENCE,
        )
        .add_input(
            NormalSignatureKind::Reimburse2,
            kickoff_txhandler.get_spendable_output(UtxoVout::ReimburseInKickoff)?,
            builder::script::SpendPath::ScriptSpend(0),
            DEFAULT_SEQUENCE,
        )
        .add_input(
            NormalSignatureKind::OperatorSighashDefault,
            round_txhandler.get_spendable_output(UtxoVout::ReimburseInRound(
                kickoff_idx,
                paramset.num_kickoffs_per_round,
            ))?,
            builder::script::SpendPath::KeySpend,
            DEFAULT_SEQUENCE,
        );

    Ok(builder
        .add_output(UnspentTxOut::from_partial(TxOut {
            value: move_txhandler
                .get_spendable_output(UtxoVout::DepositInMove)?
                .get_prevout()
                .value,
            script_pubkey: operator_reimbursement_address.script_pubkey(),
        }))
        .add_output(UnspentTxOut::from_partial(
            builder::transaction::anchor_output(paramset.anchor_amount()),
        ))
        .finalize())
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

**File:** crates/clementine-primitives/src/lib.rs (L209-215)
```rust
    ReimburseInRound(usize, usize),
    /// The vout of the kickoff utxo in RoundTx
    Kickoff(usize),
    /// The vout of the collateral utxo in RoundTx
    CollateralInRound,
    /// The vout of the collateral utxo in ReadyToReimburseTx
    CollateralInReadyToReimburse,
```
