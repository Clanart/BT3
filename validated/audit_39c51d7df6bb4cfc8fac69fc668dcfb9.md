### Title
`TransferToBtcWallet` Allows Sweeping `ReimburseInRound` Connector UTXOs, Permanently Locking Bridged BTC - (File: `core/src/operator.rs`)

---

### Summary

`transfer_outpoints_to_wallet` guards only against collateral outpoints (`CollateralInRound`, `CollateralInReadyToReimburse`). It does not guard against `ReimburseInRound` UTXOs, which also pay to the operator's plain taproot address and are required inputs to the `reimburse_tx`. An operator who sweeps a `ReimburseInRound` outpoint via `TransferToBtcWallet` permanently destroys the only spending path for the corresponding `DepositInMove` UTXO, locking the bridged BTC forever.

---

### Finding Description

`create_round_txhandler` creates `num_kickoffs_per_round` reimburse connector outputs (`ReimburseInRound`) with empty scripts and `Some(operator_xonly_pk)` as the internal key:

```rust
// Create reimburse utxos
for _ in 0..paramset.num_kickoffs_per_round {
    builder = builder.add_output(UnspentTxOut::from_scripts(
        paramset.default_utxo_amount(),
        vec![],                    // no scripts
        Some(operator_xonly_pk),   // operator's plain taproot key
        paramset.network,
    ));
}
``` [1](#0-0) 

This produces a `script_pubkey` identical to `self.operator.signer.address.script_pubkey()`.

The `transfer_to_btc_wallet` RPC handler accepts any outpoint whose `script_pubkey` matches the operator's address:

```rust
if txout.script_pubkey != self.operator.signer.address.script_pubkey() {
    return Err(...)
}
inputs.push((outpoint, txout));
``` [2](#0-1) 

Then `transfer_outpoints_to_wallet` only rejects outpoints present in `get_all_collateral_outpoints()`: [3](#0-2) 

`get_all_collateral_outpoints` populates the map with `CollateralInRound` (vout 0) and `CollateralInReadyToReimburse` (vout 0) outpoints only — it never adds `ReimburseInRound` outpoints (vout `num_kickoffs_per_round + idx + 1`): [4](#0-3) 

`ReimburseInRound` is a mandatory input to `create_reimburse_txhandler`:

```rust
.add_input(
    NormalSignatureKind::OperatorSighashDefault,
    round_txhandler.get_spendable_output(UtxoVout::ReimburseInRound(
        kickoff_idx,
        paramset.num_kickoffs_per_round,
    ))?,
    builder::script::SpendPath::KeySpend,
    DEFAULT_SEQUENCE,
)
``` [5](#0-4) 

The `reimburse_tx` also spends `DepositInMove` — the UTXO holding the bridged BTC from the move-to-vault transaction: [6](#0-5) 

If `ReimburseInRound` is spent by `TransferToBtcWallet`, the `reimburse_tx` can never be broadcast. The `DepositInMove` UTXO has no other defined spending path in the protocol, so the bridged BTC is permanently locked.

---

### Impact Explanation

- **Permanent lock of bridged BTC**: The `DepositInMove` UTXO (containing the full bridge amount) has no spending path other than `reimburse_tx`. Destroying the `ReimburseInRound` connector makes `reimburse_tx` unbroadcastable, permanently locking the user's deposited BTC.
- **Loss of operator reimbursement**: The operator who fronted the withdrawal also loses their reimbursement.
- **No recovery path**: The N-of-N multisig controls `DepositInMove` but has no pre-signed alternative spending path defined in the protocol.

---

### Likelihood Explanation

Medium. The `TransferToBtcWallet` RPC is an operator utility to sweep UTXOs from their taproot address. An operator scanning their own address for unspent UTXOs (e.g., using `scantxoutset`) would see `ReimburseInRound` outputs alongside legitimate sweepable UTXOs, since both share the same `script_pubkey`. A mistaken or automated sweep of all UTXOs at the operator's address would include these connectors. The trigger requires no external attacker — only the operator calling their own RPC.

---

### Recommendation

Extend `get_all_collateral_outpoints` (or add a parallel `get_all_protected_outpoints` function) to also enumerate all `ReimburseInRound` outpoints for every round transaction, and reject them in `transfer_outpoints_to_wallet` with the same guard used for collateral outpoints.

Concretely, inside the loop in `get_all_collateral_outpoints`, after inserting the `CollateralInRound` outpoint, also insert all `ReimburseInRound` outpoints:

```rust
for kickoff_idx in 0..paramset.num_kickoffs_per_round {
    let reimburse_outpoint = OutPoint {
        txid: *round_tx.get_txid(),
        vout: UtxoVout::ReimburseInRound(kickoff_idx, paramset.num_kickoffs_per_round).get_vout(),
    };
    outpoints.insert(reimburse_outpoint, (round_idx, TransactionType::Round));
}
```

---

### Proof of Concept

1. Operator completes a deposit flow and sends a `round_tx` on-chain. The round tx produces `num_kickoffs_per_round` `ReimburseInRound` outputs at vout `num_kickoffs_per_round + idx + 1`, each paying to the operator's plain taproot address.
2. Operator (or an automated script) scans their taproot address for UTXOs and finds a `ReimburseInRound` outpoint.
3. Operator calls `TransferToBtcWallet` with that outpoint.
4. `transfer_to_btc_wallet` fetches the txout, confirms `script_pubkey == operator.signer.address.script_pubkey()` — passes.
5. `transfer_outpoints_to_wallet` checks `get_all_collateral_outpoints()` — `ReimburseInRound` is absent — passes.
6. The UTXO is swept to the operator's wallet and confirmed on-chain.
7. When the operator later attempts to send `reimburse_tx` (to recover the bridged BTC), it fails because `ReimburseInRound` is already spent.
8. `DepositInMove` (the bridged BTC UTXO) is permanently unspendable.

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

**File:** core/src/rpc/operator.rs (L522-530)
```rust
            if txout.script_pubkey != self.operator.signer.address.script_pubkey() {
                return Err(Status::invalid_argument(format!(
                    "Outpoint script_pubkey does not match operator's address. Expected: {:?}, Got: {:?}",
                    self.operator.signer.address.script_pubkey(),
                    txout.script_pubkey
                )));
            }

            inputs.push((outpoint, txout));
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
