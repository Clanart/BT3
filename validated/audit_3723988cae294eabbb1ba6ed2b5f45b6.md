### Title
Payout `payout_tx` is fee-bumped by RBF while signed with `SIGHASH_SINGLE|ANYONECANPAY`, letting anyone strip its OP_RETURN and permanently block operator reimbursement - (File: `core/src/operator.rs`, `core/src/builder/transaction/operator_reimburse.rs`, `core/src/verifier.rs`)

### Summary
`create_payout_txhandler` builds a payout transaction whose only signed input (the user's withdrawal UTXO) is authorized with a `SinglePlusAnyoneCanPay` signature that binds solely to input 0 and output 0 [1](#0-0) . The tx is queued as `FeePayingType::RBF` [2](#0-1) , meaning the network treats it as opt-in replaceable and Bitcoin Core's fund/PSBT flow explicitly supports adding new inputs/outputs to bump it [3](#0-2) . Because outputs 1 (anchor) and 2 (OP_RETURN with the operator's xonly pubkey) are outside the signature's commitment, any outside party who observes the broadcast payout in the mempool can replace it with a higher-fee package that reuses input 0 + output 0 verbatim but drops the OP_RETURN entirely, causing the confirmed payout to be permanently unattributable to the operator that actually funded it.

### Finding Description
The binding that must hold: for withdrawal index `i`, `operator_xonly_pk` recorded in `payout_info` for `move_txid` == the operator that actually funded output 0 of the confirmed payout transaction.

Trace:
1. `Operator::withdraw` builds `payout_txhandler` via `create_payout_txhandler`, which adds output 0 (user payout, signed via key-spend), output 1 (anchor), and output 2 (`op_return_txout(operator_xonly_pk)`) [4](#0-3) .
2. The user's signature is verified against a sighash computed with `in_signature.sighash_type`, and the code's own error message states the expected type is `SinglePlusAnyoneCanPay` [5](#0-4) . `SIGHASH_SINGLE` only commits to the output at the same index as the signed input (i.e., output 0); `ANYONECANPAY` means no other input is committed. Outputs 1 and 2 and any inputs besides input 0 are entirely unconstrained by this signature.
3. The tx is registered with `TransactionType::Payout` under `FeePayingType::RBF` [2](#0-1) , and `send_rbf_tx`/PSBT funding logic is designed to add wallet inputs/change and rebroadcast replacements with higher fee [6](#0-5) .
4. An unprivileged attacker monitoring the mempool extracts input 0 (witness + outpoint) and output 0 from the broadcast honest payout, then constructs their own transaction: same input 0/witness, same output 0, but with the attacker's own funding input(s) and no OP_RETURN output (2-output tx), paying a strictly higher fee. This satisfies BIP125 RBF rules and, since Core's wallet-created payout is opt-in replaceable by default, the attacker's version can be relayed and mined instead of the operator's.
5. `Verifier::update_finalized_payouts` finds whichever transaction actually spent the withdrawal outpoint, looks for the first OP_RETURN output via `get_first_op_return_output`, and — finding none — sets `operator_xonly_pk = None`, storing `NULL` in the DB and logging it as an "optimistic payout or wrong construction" case [7](#0-6) .
6. Later, `Verifier::is_kickoff_malicious` looks up `payout_info` by `move_txid`, finds `operator_xonly_pk_opt = None`, and unconditionally returns `Ok(true)` ("assuming malicious") regardless of the actual funding operator [8](#0-7) . `Operator::send_asserts` similarly requires `payout_op_xonly_pk_opt` to be `Some` and errors out otherwise [9](#0-8) .
7. No existing guard prevents this: `is_profitable` only checks amounts [10](#0-9) ; `SECP.verify_schnorr` only validates the user's signature over input0/output0, which remains valid in the attacker's replacement; there is no check anywhere that the *confirmed* payout transaction's other outputs match what the operator originally broadcast.

### Impact Explanation
The honest operator that genuinely funded the withdrawal (paid the user out of pocket) becomes permanently unable to be reimbursed: every subsequent `is_kickoff_malicious` check for its real kickoff returns `true` because `operator_xonly_pk_opt` is `None` in the DB row keyed by `move_txid`, and this record is written once from the confirmed on-chain block data with no correction path. This matches the Critical category "an honest operator permanently unable to be reimbursed" (and, via the resulting challenge, an operator's collateral may subsequently be burned as well). The attack is repeatable against any withdrawal/operator pair as long as RBF is enabled and the honest operator does not use a maximal fee, so the blast radius spans all deposits/withdrawals processed this way.

### Likelihood Explanation
Preconditions: RBF replacement enabled on the Bitcoin network/mempool policy (default in modern Bitcoin Core, and the tx is explicitly funded with wallet defaults that make it opt-in replaceable), and the honest operator's payout fee not already at the top of the fee market. Attacker cost is only the fee premium needed to win BIP125 replacement plus the extra input value they must supply, which for a simple 2-output payout is modest and does not require any bridge deposit, key material, or privileged role — purely public mempool monitoring plus standard wallet/Bitcoin RPC access. This is fully reproducible on regtest, independent of Citrea or mainnet.

### Recommendation
Do not treat `payout_tx` as an ordinary `FeePayingType::RBF` transaction whose non-signed outputs (especially the OP_RETURN with the operator identity) can be freely dropped by third parties. Options: (a) require the user's withdrawal signature to cover the OP_RETURN output as well (e.g., use `SIGHASH_ALL` or a custom scheme that binds all payout outputs, not just output 0, before fee funding/RBF is applied), or (b) commit to the operator identity independently of the mutable payout transaction (e.g., have the operator's own signature/commitment recorded off-chain/on Citrea prior to broadcast, and have `is_kickoff_malicious`/`update_finalized_payouts` fall back to that pre-registered commitment instead of solely trusting the OP_RETURN of whatever transaction happens to confirm), or (c) use CPFP fee bumping (anchor output) instead of RBF for the payout so the original inputs/outputs cannot be replaced by a third party.

### Proof of Concept
```
cargo test --package clementine-core --test regtest_payout_rbf_griefing
```
Plan for the test:
1. Set up a regtest environment with an operator and a valid deposit/withdrawal (as in existing e2e helpers, e.g. `core/src/test/common/clementine_utils.rs`).
2. Call `operator.withdraw(...)` to have the operator sign and broadcast `payout_tx` (3 outputs: user payout, anchor, OP_RETURN with operator xonly pk), with RBF enabled in the wallet.
3. Before it confirms, extract input 0 (outpoint + witness) and output 0 from the mempool tx; build an attacker transaction with: input 0 (unchanged), attacker-funded additional input(s), output 0 unchanged, and no OP_RETURN output, at a higher feerate; broadcast via `send_raw_transaction`.
4. Mine blocks to confirm the attacker's replacement and to finality depth.
5. Assert (equality before/after):
   - Before: expected `operator_xonly_pk_opt == Some(operator.signer.xonly_public_key)` for the withdrawal's `move_txid`.
   - After sync (`update_finalized_payouts`): query `db.get_payout_info_from_move_txid` and assert `operator_xonly_pk_opt == None` (demonstrating the divergence).
6. Trigger the operator's genuine kickoff for this deposit and call `Verifier::is_kickoff_malicious` (or the equivalent `handle_kickoff` duty); assert it returns `true` even though the operator correctly funded the payout, proving permanent inability to be reimbursed.

### Citations

**File:** core/src/operator.rs (L503-537)
```rust
    fn is_profitable(
        input_amount: Amount,
        withdrawal_amount: Amount,
        bridge_amount_sats: Amount,
        operator_withdrawal_fee_sats: Amount,
    ) -> bool {
        // Use checked_sub to safely handle potential underflow
        let withdrawal_diff = match withdrawal_amount
            .to_sat()
            .checked_sub(input_amount.to_sat())
        {
            Some(diff) => Amount::from_sat(diff),
            None => {
                // input amount is greater than withdrawal amount, so it's profitable but doesn't make sense
                tracing::warn!(
                    "Some user gave more amount than the withdrawal amount as input for withdrawal"
                );
                return true;
            }
        };

        if withdrawal_diff > bridge_amount_sats {
            return false;
        }

        // Calculate net profit after the withdrawal using checked_sub to prevent panic
        let net_profit = match bridge_amount_sats.checked_sub(withdrawal_diff) {
            Some(profit) => profit,
            None => return false, // If underflow occurs, it's not profitable
        };

        // Net profit must be bigger than withdrawal fee.
        // net profit doesn't take into account the fees, but operator_withdrawal_fee_sats should
        net_profit >= operator_withdrawal_fee_sats
    }
```

**File:** core/src/operator.rs (L628-637)
```rust
        // tracing::info!("Payout txhandler: {:?}", hex::encode(bitcoin::consensus::serialize(&payout_txhandler.get_cached_tx())));

        let sighash = payout_txhandler.calculate_sighash_txin(0, in_signature.sighash_type)?;

        SECP.verify_schnorr(
            &in_signature.signature,
            &Message::from_digest(*sighash.as_byte_array()),
            user_xonly_pk,
        )
        .wrap_err("Failed to verify signature received from user for payout txin. Ensure the signature uses SinglePlusAnyoneCanPay sighash type.")?;
```

**File:** core/src/operator.rs (L1284-1295)
```rust
        let payout_op_xonly_pk = payout_op_xonly_pk_opt.ok_or_eyre(format!(
            "Payout operator xonly pk not found in payout info DB while sending asserts for deposit move txid: {move_txid}"
        ))?;

        tracing::info!("Sending asserts for deposit_idx: {deposit_idx:?}");

        if payout_op_xonly_pk != kickoff_data.operator_xonly_pk {
            return Err(eyre::eyre!(
                "Payout operator xonly pk does not match kickoff operator xonly pk in send_asserts"
            )
            .into());
        }
```

**File:** core/src/tx_sender_queue.rs (L92-105)
```rust
            TransactionType::Challenge | TransactionType::Payout => {
                self.insert_try_to_send(
                    dbtx,
                    tx_metadata,
                    signed_tx,
                    FeePayingType::RBF,
                    rbf_info,
                    &[],
                    &[],
                    &[],
                    &[],
                )
                .await
            }
```

**File:** crates/clementine-tx-sender/src/rbf.rs (L532-548)
```rust
    /// Sends or bumps a transaction using the Replace-By-Fee (RBF) strategy.
    ///
    /// It interacts with the database to track the latest RBF attempt (`last_rbf_txid`).
    ///
    /// # Logic:
    /// 1.  **Check for Existing RBF Tx:** Retrieves RBF txids for the `try_to_send_id` and
    ///     selects the most recent one still in the mempool.
    /// 2.  **Bump Existing Tx:** If a mempool tx exists, it calls `rpc.psbt_bump_fee`.
    ///     - This internally uses the Bitcoin Core `psbtbumpfee` RPC.
    ///     - We then sign the inputs that we can using our Actor and have the wallet sign the rest.
    ///
    /// 3.  **Send Initial RBF Tx:** If no RBF tx is found in the mempool:
    ///     - It uses `fund_raw_transaction` RPC to let the wallet add (potentially) inputs,
    ///       outputs, set the fee according to `fee_rate`, and mark the transaction as replaceable.
    ///     - Uses `sign_raw_transaction_with_wallet` RPC to sign the funded transaction.
    ///     - Uses `send_raw_transaction` RPC to broadcast the initial RBF transaction.
    ///     - Saves the resulting `txid` to the database as the `last_rbf_txid`.
```

**File:** crates/clementine-tx-sender/src/rbf.rs (L888-906)
```rust
            let mut added_dummy_output = false;
            // if the tx has no outputs, btc core wallet fund transaction will fail, so we add a dummy output
            // which we will remove later to save on fees.
            if tx.output.is_empty() {
                tx.output.push(TxOut {
                    value: NON_EPHEMERAL_ANCHOR_AMOUNT,
                    script_pubkey: ScriptBuf::from_hex("51024e73").expect("valid anchor script"),
                });
                added_dummy_output = true;
            }

            let create_result = self
                .create_funded_psbt(&tx, fee_rate)
                .await
                .map_err(|err| {
                    let err = eyre!(err).wrap_err("Failed to create funded PSBT");
                    self.handle_err(format!("{err:?}"), "rbf_psbt_create_failed", try_to_send_id);

                    err
```

**File:** core/src/builder/transaction/operator_reimburse.rs (L407-436)
```rust
pub fn create_payout_txhandler(
    input_utxo: UTXO,
    output_txout: TxOut,
    operator_xonly_pk: XOnlyPublicKey,
    user_sig: taproot::Signature,
    _network: bitcoin::Network,
) -> Result<TxHandler<Signed>, BridgeError> {
    let txin = SpendableTxIn::new_partial(input_utxo.outpoint, input_utxo.txout);

    let output_txout = UnspentTxOut::from_partial(output_txout.clone());

    let op_return_txout = op_return_txout(PushBytesBuf::from(operator_xonly_pk.serialize()));

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
    txhandler.set_p2tr_key_spend_witness(&user_sig, 0)?;
    txhandler.promote()
}
```

**File:** core/src/verifier.rs (L1875-1885)
```rust
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
```

**File:** core/src/verifier.rs (L2312-2328)
```rust
            // Find the first output that contains OP_RETURN
            let circuit_payout_tx = CircuitTransaction::from(payout_tx.clone());
            let op_return_output = get_first_op_return_output(&circuit_payout_tx);

            // If OP_RETURN doesn't exist in any outputs, or the data in OP_RETURN is not a valid xonly_pubkey,
            // operator_xonly_pk will be set to None, and the corresponding column in DB set to NULL.
            // This can happen if optimistic payout is used, or an operator constructs the payout tx wrong.
            let operator_xonly_pk = op_return_output
                .and_then(|output| parse_op_return_data(&output.script_pubkey))
                .and_then(|bytes| XOnlyPublicKey::from_slice(bytes).ok());

            if operator_xonly_pk.is_none() {
                tracing::info!(
                    "No valid operator xonly pk found in payout tx {:?} OP_RETURN. Either it is an optimistic payout or the operator constructed the payout tx wrong",
                    payout_txid
                );
            }
```
