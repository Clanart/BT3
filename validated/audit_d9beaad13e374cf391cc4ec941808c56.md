### Title
Unauthenticated `SIGHASH_SINGLE|ANYONECANPAY` payout construction allows permanent freezing of the vault UTXO by front-running with an invalid/foreign operator OP_RETURN - (File: `core/src/builder/transaction/operator_reimburse.rs`)

### Summary
The payout transaction that fronts a Citrea withdrawal is authorized by the withdrawing user with a `SIGHASH_SINGLE|ANYONECANPAY` signature that only commits to input 0 and output 0 (the withdrawal payout itself). The two remaining outputs — the anchor and, critically, the OP_RETURN carrying the fronting operator's x-only pubkey — are left uncommitted and can be freely chosen by whoever assembles and funds the final transaction. Any party in possession of that signature (starting with the withdrawing user themselves) can self-fund the transaction and set the OP_RETURN to garbage or to an operator that never paid. Downstream, the protocol's payout attribution (`update_finalized_payouts`) and reimbursement gating (`validate_payer_is_operator`, `is_kickoff_malicious`) hard-require the OP_RETURN pubkey to match exactly the operator sending the kickoff. If the OP_RETURN is invalid/unmatched, no operator can ever safely claim reimbursement for that deposit, and the withdrawal UTXO (needed for the sole remaining recovery path, the optimistic payout) is already spent — permanently freezing the move-to-vault deposit.

### Finding Description
The payout tx is built in `create_payout_txhandler`, which sets the OP_RETURN to an `operator_xonly_pk` argument and signs input 0 with the caller-supplied `user_sig` over the `KeySpend` path: [1](#0-0) 

The comment on `sign_withdrawal_output`/`generate_withdrawal_transaction_and_signatures` explicitly documents that this signature uses `SinglePlusAnyoneCanPay`: [2](#0-1) 

`SIGHASH_SINGLE|ANYONECANPAY` only commits to the input being signed (index 0) and the output at the *same index* (index 0, the user's payout output). It does not commit to output 1 (anchor) or output 2 (the OP_RETURN naming the fronting operator), nor to any additional inputs. The tx-sender code itself is built around this fact — it explicitly places the wallet's change/funding output at the *last* index specifically "so that SinglePlusAnyoneCanPay signatures stay valid," confirming that arbitrary additional inputs/outputs can be appended without invalidating the user's signature: [3](#0-2) 

Because the operator's identity commitment (OP_RETURN) is not covered by the user's signature, anyone who has the raw `(in_signature, in_outpoint, out_script_pubkey, out_amount)` tuple — which, per the design, is the user's own withdrawal request data, "committed in Citrea side, with the signature given to operators off-chain" — can independently build and broadcast a valid payout transaction directly on Bitcoin, entirely bypassing Clementine's operator RPC. They can add their own funding input(s) (fully self-signed, standard) to cover the withdrawal amount, and set the OP_RETURN to any 32 bytes they like: garbage that fails to parse as an x-only pubkey, or a real operator's pubkey without that operator's participation.

On the read side, `update_finalized_payouts` scans the confirmed payout tx, extracts the OP_RETURN and tries to parse it as an x-only pubkey; if it fails to parse, `operator_xonly_pk` is stored as `NULL`: [4](#0-3) 

Reimbursement can only proceed for an operator whose kickoff matches the *stored* payer pubkey exactly. `validate_payer_is_operator` errors out entirely if the DB's payer info is missing/`None`, or does not equal the querying operator's own key: [5](#0-4) 

Even if some operator tried anyway, `is_kickoff_malicious` treats a missing/mismatched OP_RETURN operator pubkey as malicious, meaning any operator that dares to kick off for this deposit gets challenged/slashed: [6](#0-5) 

Meanwhile the only alternative recovery path, the optimistic multisig payout, requires the withdrawal UTXO to be *unspent*: [7](#0-6) 

Since the attacker's rogue payout transaction already spends that exact withdrawal UTXO (it must, to satisfy the user's own signed commitment), the optimistic payout path is foreclosed as well. The move-to-vault deposit output — an N-of-N (+security-council) multisig UTXO — thus becomes permanently unclaimable by any code path other than a security-council intervention, which is out of scope as a privileged remedy.

### Impact Explanation
This breaks the binding "the operator credited versus the party that paid" and results in "a vault UTXO permanently frozen," both explicitly listed Critical impacts. The withdrawing user (or anyone who obtains their signed withdrawal tuple) can, entirely unprivileged and without any operator, verifier, or security-council role, cause the underlying deposited BTC held in the N-of-N vault output to become permanently stuck, since neither the kickoff/reimburse flow (blocked by strict operator-pubkey matching) nor the optimistic-payout flow (blocked because the withdrawal UTXO is already spent) can subsequently release it.

### Likelihood Explanation
The action requires no privileged role, no key compromise, and no collusion — only knowledge of a legitimately user-signed withdrawal (which by design the user themselves already possesses, since they create it to authorize a fronting operator). The `SIGHASH_SINGLE|ANYONECANPAY` scheme and its narrow output commitment is a deliberate design choice (confirmed by the tx-sender's explicit comment) rather than an implementation slip elsewhere, making this reachable through the normal, documented withdrawal flow.

### Recommendation
Bind the fronting operator's identity to the user's signature so it cannot be substituted or nulled by a third party — e.g., have the user sign with `SIGHASH_ALL` (or a `SIGHASH_SINGLE` variant that also covers the OP_RETURN output), or otherwise commit the intended operator's x-only pubkey (or a placeholder that verifiers can validate deterministically) inside the signed message/commitment before publication, so a rogue payout with an invalid/foreign OP_RETURN cannot be constructed with a validly-signed input. Additionally, consider not requiring the optimistic-payout path to be foreclosed purely by input-outpoint-spent status; allow recovery via verifier N-of-N signature even when the payout OP_RETURN doesn't resolve to a registered operator, so a malformed/rogue payout does not permanently strand the vault funds.

### Proof of Concept
Conceptual PoC (bitcoin-only, no Clementine RPC calls needed):
1. User initiates a withdrawal on Citrea and locally builds+signs the payout skeleton exactly as `generate_withdrawal_transaction_and_signature` does: input = their own dust UTXO, output0 = their own withdrawal payout script/amount, signed with `SIGHASH_SINGLE|ANYONECANPAY` (`core/src/test/common/setup_utils.rs:430-449`).
2. Instead of relaying this data to an operator, the user (or any third party holding it) assembles their own transaction: input0 = the same signed dust UTXO + signature, input1 = an additional self-owned funding UTXO (normal signature) sized to cover `out_amount` + fees, output0 = identical to the signed payout output, output1 = anchor, output2 = OP_RETURN with 32 bytes that do not decode to a valid x-only pubkey (or an arbitrary operator's pubkey chosen without consent).
3. Broadcast directly to the Bitcoin network. The transaction is fully valid per consensus rules since `SIGHASH_SINGLE|ANYONECANPAY` only requires output0 and input0 to match; nothing prevents the extra input/outputs.
4. `update_finalized_payouts` will store `payout_payer_operator_xonly_pk = NULL` (or a pubkey belonging to an operator who never sent this payout) (`core/src/verifier.rs:2311-2328`).
5. No operator can produce reimbursement txs for this deposit (`validate_payer_is_operator` errors, `is_kickoff_malicious` flags any attempt) (`core/src/operator.rs:1703-1729`, `core/src/verifier.rs:1857-1890`), and optimistic payout is blocked because the withdrawal UTXO is already spent (`core/src/verifier.rs:1580-1586`). The move-to-vault deposit UTXO is now permanently unrecoverable through any unprivileged or operator-level path.

### Citations

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

**File:** core/src/test/common/setup_utils.rs (L430-449)
```rust
/// Generates withdrawal transaction and signs it with `SinglePlusAnyoneCanPay`.
///
/// # Returns
///
/// A tuple of:
///
/// - [`UTXO`]: Dust UTXO used as the input of the withdrawal transaction
/// - [`TxOut`]: Txout of the withdrawal transaction
/// - [`Signature`]: Signature of the withdrawal transaction
pub async fn generate_withdrawal_transaction_and_signature(
    config: &BridgeConfig,
    rpc: &ExtendedBitcoinRpc,
    withdrawal_address: &bitcoin::Address,
    withdrawal_amount: bitcoin::Amount,
) -> (UTXO, bitcoin::TxOut, taproot::Signature) {
    let dust_utxo = generate_withdrawal_utxo(config, rpc).await;
    let (txout, sig) =
        sign_withdrawal_output(config, &dust_utxo, withdrawal_address, withdrawal_amount);
    (dust_utxo, txout, sig)
}
```

**File:** crates/clementine-tx-sender/src/rbf.rs (L152-177)
```rust
    pub async fn create_funded_psbt(
        &self,
        tx: &Transaction,
        fee_rate: FeeRateKvb,
    ) -> Result<WalletCreateFundedPsbtResult> {
        // 1. Create a funded PSBT using the wallet
        let create_psbt_opts = bitcoincore_rpc::json::WalletCreateFundedPsbtOptions {
            add_inputs: Some(true), // Let the wallet add its inputs
            include_unsafe: Some(self.include_unsafe),
            change_address: None,
            change_position: Some(tx.output.len() as u16), // Add change output at last index (so that SinglePlusAnyoneCanPay signatures stay valid)
            change_type: None,
            include_watching: None,
            lock_unspent: None,
            // Bitcoincore expects BTC/kvbyte for fee_rate
            fee_rate: Some(
                fee_rate
                    .fee_vb(1000)
                    .ok_or_eyre("Failed to convert fee rate to BTC/kvbyte")?,
            ),
            subtract_fee_from_outputs: vec![],
            replaceable: Some(true), // Mark as RBF enabled
            conf_target: None,
            estimate_mode: None,
        };

```

**File:** core/src/verifier.rs (L1580-1586)
```rust
    ) -> Result<PartialSignature, BridgeError> {
        // if the withdrawal utxo is spent, no reason to sign optimistic payout
        if self.rpc.is_utxo_spent(&input_outpoint).await? {
            return Err(
                eyre::eyre!("Withdrawal utxo {:?} is already spent", input_outpoint).into(),
            );
        }
```

**File:** core/src/verifier.rs (L1857-1890)
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
```

**File:** core/src/verifier.rs (L2311-2328)
```rust
            let payout_tx = &block.txdata[*payout_tx_idx];
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

**File:** core/src/operator.rs (L1703-1729)
```rust
        // first check if the payer is the operator, and the kickoff is handled
        // by the PayoutCheckerTask, meaning kickoff_txid is set
        let (payout_blockhash, kickoff_txid) = match (
            payer_xonly_pk,
            payout_blockhash,
            kickoff_txid,
        ) {
            (Some(payer_xonly_pk), Some(payout_blockhash), Some(kickoff_txid)) => {
                if payer_xonly_pk != self.signer.xonly_public_key {
                    return Err(eyre::eyre!(
                        "Payer is not own operator for deposit, payer xonly pk: {:?}, operator xonly pk: {:?}",
                        payer_xonly_pk,
                        self.signer.xonly_public_key
                    )
                    .into());
                }
                (payout_blockhash, kickoff_txid)
            }
            _ => {
                return Err(eyre::eyre!(
                    "Payer info not found for deposit, payout blockhash: {:?}, kickoff txid: {:?}",
                    payout_blockhash,
                    kickoff_txid
                )
                .into());
            }
        };
```
