### Title
Removed Operator Can Indefinitely Block New Deposits by Refusing to Exit Protocol - (`core/src/verifier.rs`)

---

### Summary

When an operator is removed from the aggregator's active set, every verifier still enforces that the operator's collateral must be spent before it will sign any new deposit. Because there is no on-chain or off-chain mechanism to force an operator to spend their collateral (exit the protocol), a single removed-but-uncooperative operator can permanently halt all new BTC deposits into the bridge.

---

### Finding Description

`Verifier::is_deposit_valid` iterates over every operator stored in the verifier's database and calls `collateral_check` on each one. [1](#0-0) 

If an operator is present in the verifier's DB with usable collateral but is **absent** from the deposit data supplied by the aggregator, the verifier immediately rejects the deposit:

```
if !operators_in_deposit_data.contains(xonly_pk) && is_collateral_usable {
    return Err(BridgeError::InvalidDeposit(...));
}
``` [2](#0-1) 

The only way an operator is considered "not usable" is when `collateral_check` returns `false`, which happens only when the operator's collateral UTXO has been spent outside the protocol: [3](#0-2) 

There is no `remove_operator` function in the verifier's database. Operators are only added via `insert_operator_if_not_exists` during `set_operator`. The round state machine's `operator_exit` state is only entered when the collateral is spent on-chain: [4](#0-3) 

The existing E2E test explicitly documents this limitation: [5](#0-4) 

The test then shows the only resolution is for the operator to spend their own collateral: [6](#0-5) 

**Analog to M-1**: In MZero, a disapproved earner continues earning because only they can call `stopEarning()`. In Clementine, a removed operator continues blocking deposits because only they can spend their collateral. In both cases, the "removed" actor retains unilateral control over when the protocol resumes normal operation.

---

### Impact Explanation

A single operator who has been removed from the aggregator's active set but refuses to spend their collateral will cause **every subsequent deposit attempt to fail** at the verifier's `is_deposit_valid` check. No new BTC can be bridged into Citrea until the operator voluntarily exits. This is a permanent bridge liveness failure with material fund impact: users are unable to peg in BTC, and the bridge's core function is halted.

---

### Likelihood Explanation

Any legitimately registered operator can trigger this condition by simply going offline or refusing to cooperate after the aggregator removes them. No special privilege beyond initial registration is required. The operator's collateral remains locked in their own key-spend taproot address, which only they control.

---

### Recommendation

1. **Add a DB-level operator removal path**: Allow verifiers to remove an operator from their local DB when the aggregator signals the operator has been deregistered and their collateral is confirmed spent on-chain.

2. **Decouple deposit validity from DB-resident operators**: Instead of requiring all DB-resident operators with usable collateral to appear in every deposit, allow the aggregator to provide a signed "operator exit" attestation that verifiers accept as proof the operator is no longer active, without requiring the collateral to be spent first.

3. **Alternatively, add a protocol-level force-exit**: Introduce a pre-signed transaction (analogous to `OperatorChallengeNack`) that the N-of-N can broadcast to burn or redirect the collateral of an operator who has been deregistered but refuses to exit, enabling the protocol to proceed without the operator's cooperation.

---

### Proof of Concept

1. Register operator O with verifiers (via `set_operator`). O's collateral is on-chain and usable.
2. Aggregator removes O from its active operator list (e.g., `actors.remove_operator(O_index)`).
3. Attempt a new deposit. The aggregator constructs `DepositData` without O.
4. Each verifier calls `is_deposit_valid` → `collateral_check(O)` returns `true` → deposit rejected with `InvalidDeposit("Operator ... is still in protocol but not in the deposit data from aggregator")`.
5. O refuses to spend their collateral.
6. All subsequent deposit attempts fail indefinitely. The bridge is frozen for new deposits.

This is confirmed by the existing test at `core/src/test/deposit_and_withdraw_e2e.rs` lines 444–507, which explicitly demonstrates that deposits fail until the operator spends their own collateral. [7](#0-6)

### Citations

**File:** core/src/verifier.rs (L603-636)
```rust
        let operators_in_deposit_data = deposit_data.get_operators();
        // check if all operators that still have collateral are in the deposit
        let operators_in_db = self.db.get_operators(None).await?;
        for (xonly_pk, reimburse_addr, collateral_funding_outpoint) in operators_in_db.iter() {
            let operator_data = OperatorData {
                xonly_pk: *xonly_pk,
                collateral_funding_outpoint: *collateral_funding_outpoint,
                reimburse_addr: reimburse_addr.clone(),
            };
            let kickoff_winternitz_pks = self
                .db
                .get_operator_kickoff_winternitz_public_keys(None, *xonly_pk)
                .await?;
            let kickoff_wpks = KickoffWinternitzKeys::new(
                kickoff_winternitz_pks,
                self.config.protocol_paramset().num_kickoffs_per_round,
                self.config.protocol_paramset().num_round_txs,
            )?;
            let is_collateral_usable = self
                .rpc
                .collateral_check(
                    &operator_data,
                    &kickoff_wpks,
                    self.config.protocol_paramset(),
                )
                .await?;
            // if operator is not in deposit but its collateral is still on chain, return false
            if !operators_in_deposit_data.contains(xonly_pk) && is_collateral_usable {
                let reason = format!(
                    "Operator {xonly_pk:?} is is still in protocol but not in the deposit data from aggregator",
                );
                tracing::error!("{reason}");
                return Err(BridgeError::InvalidDeposit(reason));
            }
```

**File:** core/src/extended_bitcoin_rpc.rs (L236-240)
```rust
        // if the collateral utxo we found latest in the round tx chain is spent, operators collateral is spent from Clementine
        // bridge protocol, thus it is unusable and operator cannot fulfill withdrawals anymore
        // if not spent, it should exist in chain, which is checked below
        Ok(!self.is_utxo_spent(&current_collateral_outpoint).await?)
    }
```

**File:** core/src/states/round.rs (L344-354)
```rust
    /// Entry action for the operator exit state.
    /// This method removes all matchers for the round state machine.
    /// We do not care about anything after the operator exits the protocol.
    /// For example, even if operator sends a kickoff after exiting the protocol, that
    /// kickoff is useless as reimburse connector utxo of that kickoff is in the next round,
    /// which cannot be created anymore as the collateral is spent. So we do not want to challenge it, etc.
    #[action]
    pub(crate) async fn on_operator_exit_entry(&mut self) {
        self.matchers = HashMap::new();
        tracing::warn!(?self.operator_data, "Operator exited the protocol.");
    }
```

**File:** core/src/test/deposit_and_withdraw_e2e.rs (L444-507)
```rust
        // remove an operator and try a deposit, it should fail because the  operator is still in verifiers DB.
        // to make it not fail, operator data needs to be removed from verifiers DB.
        // if the behavior is changed in the future, the test should be updated.
        tracing::info!("Removing operator 1");
        let op1_secret_key = actors
            .get_operator_by_index(1)
            .expect("Operator 1 not found")
            .secret_key;
        actors.remove_operator(1).await.unwrap();
        // try to do a deposit, it should fail.
        assert!(
            run_single_deposit::<CitreaClient>(&mut config, rpc.clone(), None, &actors, None)
                .await
                .is_err()
        );

        // spend the operator's collateral then try a deposit, it should work now as operator exited the protocol
        let op1_actor = Actor::new(op1_secret_key, config.protocol_paramset().network);
        let op1_xonly_pk = op1_actor.xonly_public_key;
        let op1_collateral = new_operator_db
            .get_operator(None, op1_xonly_pk)
            .await
            .unwrap()
            .unwrap()
            .collateral_funding_outpoint;
        let collateral_funding_amount = config.protocol_paramset().collateral_funding_amount;
        let (op_address, op_spend) =
            create_taproot_address(&[], Some(op1_xonly_pk), config.protocol_paramset().network);
        let mut txhandler = TxHandlerBuilder::new(TransactionType::Dummy)
            .add_input(
                NormalSignatureKind::OperatorSighashDefault,
                SpendableTxIn::new(
                    op1_collateral,
                    TxOut {
                        value: collateral_funding_amount,
                        script_pubkey: op1_actor.address.script_pubkey(),
                    },
                    vec![],
                    Some(op_spend.clone()),
                ),
                SpendPath::KeySpend,
                DEFAULT_SEQUENCE,
            )
            .add_output(UnspentTxOut::from_partial(TxOut {
                value: collateral_funding_amount - Amount::from_sat(1000),
                script_pubkey: op_address.script_pubkey(),
            }))
            .finalize();
        op1_actor
            .tx_sign_and_fill_sigs(&mut txhandler, &[], None)
            .unwrap();
        let tx = txhandler.get_cached_tx();
        rpc.send_raw_transaction(tx).await.unwrap();
        rpc.mine_blocks_while_synced(1, &actors, Some(&citrea_e2e_data))
            .await
            .unwrap();
        // check if collateral is spent
        assert!(rpc.is_utxo_spent(&op1_collateral).await.unwrap());
        // try a deposit, it should work now
        assert!(
            run_single_deposit::<CitreaClient>(&mut config, rpc.clone(), None, &actors, None)
                .await
                .is_ok()
        );
```
