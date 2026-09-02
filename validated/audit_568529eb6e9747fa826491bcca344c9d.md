### Title
`bridge_circuit` never verifies that operator funds paid the withdrawer — only that a public OP_RETURN names an operator - ([File: circuits-lib/src/bridge_circuit/mod.rs])

### Summary
`bridge_circuit::bridge_circuit` (and its host-side mirror `bridge_circuit_host::prove_bridge_circuit`) only checks that the SPV-proven `payout_spv` transaction (1) spends the exact withdrawal outpoint registered on Citrea and (2) carries an OP_RETURN output containing *some* operator's x-only pubkey. It never checks that any value in that transaction actually originated from that operator's wallet. Since the OP_RETURN is a free-form output anyone can add, and the withdrawal-outpoint is simply whatever outpoint the withdrawer chose to register via Citrea's `withdraw()`, a withdrawer can fully self-fund and self-sign the payout transaction and still have the circuit accept it as an operator-fronted payout.

### Finding Description
The claimed binding is:
`ATTRIBUTION: funding_source(payout_tx.outputs[user_output]) == operator.wallet` — i.e. the BTC paid to the withdrawer in the accepted `payout_spv` transaction must have come from the named operator's own funds, not the withdrawer's pre-existing UTXO.

Tracing `bridge_circuit` in `circuits-lib/src/bridge_circuit/mod.rs:137-236`:
- SPV validity of `input.payout_spv` is checked (mined tx, valid Merkle inclusion). [1](#0-0) 
- The withdrawal outpoint from the storage proof is compared only against `payout_spv.transaction.input[payout_input_index].previous_output` (txid+vout equality) — nothing about the *value* of that input, or about any *other* input, is checked. [2](#0-1) 
- The only "operator attribution" performed is parsing an OP_RETURN output and folding the embedded x-only pubkey into `deposit_constant` — this is a plain, unauthenticated data read from a script anyone can craft; there is no signature check or input-ownership check tying that pubkey to the actual signer/funder of the transaction. [3](#0-2) 

Compare with the honest flow in `core/src/builder/transaction/operator_reimburse.rs::create_payout_txhandler`, where the operator is expected to add extra funding inputs and control the OP_RETURN, and `core/src/operator.rs::Operator::withdraw`, which calls `fund_raw_transaction` to add the operator's own inputs before signing the OP_RETURN with their identity: [4](#0-3) [5](#0-4) 

However, nothing in `bridge_circuit` requires this "extra funding input" pattern. The withdrawal-outpoint registered on Citrea (`withdraw(txId, vout)`) records only an outpoint — not an amount or a "must be dust/small" constraint — and Citrea has no way to enforce that the outpoint the withdrawer chooses is dust rather than an already fully-valued UTXO they own: [6](#0-5) 

Exploit flow:
1. Withdrawer deposits `bridge_amount` normally and gets L2 credit, then registers withdrawal outpoint `O` on Citrea via `withdraw(txId, vout)` where `O` is a UTXO the withdrawer already fully controls and that already holds enough value to cover the withdrawal amount + fee (no dust UTXO needed).
2. Withdrawer builds and signs, entirely with their own key (any sighash flag, e.g. SIGHASH_ALL/DEFAULT), a transaction spending only `O`, paying themselves the withdrawal amount, and appending an OP_RETURN containing any operator's public x-only pubkey (public information, taken from that operator's kickoff tx on-chain).
3. Withdrawer broadcasts it; it confirms; an SPV proof is produced for it.
4. This `payout_spv`, together with the matching storage proof/HCP/LCP, is fed into `bridge_circuit`. Every existing guard passes: SPV verifies, the input at `payout_input_index` matches the registered withdrawal outpoint, and the OP_RETURN parses to a valid operator xonly key that feeds `deposit_constant`.
5. The resulting journal is bit-for-bit identical in shape to a genuine operator-funded payout's journal, since `deposit_constant`/journal never encode who funded the payout, only which operator pubkey happened to be written into the OP_RETURN.

This lets whichever party is named in that OP_RETURN (a colluding/dishonest operator, since only an operator's kickoff/Reimburse flow benefits from this proof) pass the disprove/verification path of the Reimburse process for a payout it never actually funded, because the circuit has no mechanism to distinguish "operator's own money paid the user" from "user's own money paid the user, with an operator's name stapled on."

### Impact Explanation
If a kickoff citing this self-funded `payout_spv` is not successfully disproved (because the circuit itself cannot detect the forgery), the `Reimburse` transaction pays the named operator the full deposited amount from the `MoveToVaultTx` output (`create_reimburse_txhandler`, output value = the full vault deposit value): [7](#0-6) 
This is BTC leaving a move-to-vault UTXO without a matching operator-fronted withdrawal, and an operator credited/reimbursed for a payout it never funded — both explicitly Critical-severity impact categories. This is repeatable per deposit/withdrawal and per operator, and the blast radius scales with every deposit whose withdrawal outpoint a colluding withdrawer is willing to self-fund.

### Likelihood Explanation
Requires a dishonest or colluding operator to actually submit the kickoff/Reimburse chain naming themselves via the OP_RETURN (an honest operator's own software, e.g. `Operator::withdraw`, will not produce such a transaction on its own). The unprivileged half of the attack — constructing, signing, and broadcasting a fully self-funded "payout" transaction with an arbitrary OP_RETURN — is trivial and costs only normal Bitcoin fees; the withdrawer needs no operator key material to add the OP_RETURN, since it is merely public information copied from the target operator's on-chain kickoff transaction. The gap is a genuine missing invariant in the circuit's public-input construction, independent of whether the "beneficiary" side of the exploit is executed.

### Recommendation
`bridge_circuit` must enforce an attribution binding between the operator identity and the actual funding of the payout, not merely the presence of an OP_RETURN. Options: require and verify a signature from the operator's key over the payout transaction (or over specific additional payout inputs), and/or require that the transaction contain at least one additional input whose previous output's scriptPubkey is provably the operator's known address/key (checked in-circuit against the operator's registered pubkey used elsewhere in `deposit_constant`), so that self-funded single-input payouts can never satisfy the circuit's constraints.

### Proof of Concept
```rust
// In circuits-lib/src/bridge_circuit/mod.rs test module (or a new test file):
// 1. Build a BridgeCircuitInput where payout_spv.transaction has exactly one input,
//    spending the registered withdrawal outpoint, signed solely with the withdrawer's
//    own key (SIGHASH_ALL), and one OP_RETURN output embedding operator_xonly_pk
//    copied from an unrelated, legitimate kickoff_tx.
// 2. Build a second BridgeCircuitInput that is a "genuine" operator-funded payout:
//    same withdrawal outpoint as input 0, plus an additional operator-funded input,
//    same OP_RETURN operator_xonly_pk.
// 3. Call SuccinctBridgeCircuitPublicInputs::new() on both and compare
//    host_journal_hash() outputs (via deposit_constant/journal_hash):
//    assert_eq!(shape_of(journal_1), shape_of(journal_2));
// This demonstrates that bridge_circuit's committed journal (and hence its guest-side
// panics/asserts) cannot distinguish the two cases, i.e. ATTRIBUTION(payout, operator)
// is never checked — both pass identical checks in `bridge_circuit()`.
```

### Citations

**File:** circuits-lib/src/bridge_circuit/mod.rs (L162-169)
```rust
    let mmr = input.hcp.chain_state.block_hashes_mmr.clone();

    if !input.payout_spv.verify(mmr) {
        panic!(
            "Invalid SPV proof for txid: {}",
            input.payout_spv.transaction.compute_txid()
        );
    }
```

**File:** circuits-lib/src/bridge_circuit/mod.rs (L182-204)
```rust
    // Storage proof verification for deposit tx index and withdrawal outpoint
    let (user_wd_outpoint, vout, move_txid) =
        verify_storage_proofs(&input.sp, light_client_circuit_output.l2_state_root);

    let user_wd_txid = bitcoin::Txid::from_byte_array(*user_wd_outpoint);

    let payout_input_index: usize = input.payout_input_index as usize;

    assert_eq!(
        user_wd_txid,
        input.payout_spv.transaction.input[payout_input_index]
            .previous_output
            .txid,
        "Invalid withdrawal transaction ID"
    );

    assert_eq!(
        vout,
        input.payout_spv.transaction.input[payout_input_index]
            .previous_output
            .vout,
        "Invalid withdrawal transaction output index"
    );
```

**File:** circuits-lib/src/bridge_circuit/mod.rs (L206-229)
```rust
    let first_op_return_output = get_first_op_return_output(&input.payout_spv.transaction)
        .expect("Payout transaction must have an OP_RETURN output");

    let round_txid = input.kickoff_tx.input[0]
        .previous_output
        .txid
        .to_byte_array();

    let kickoff_round_vout = input.kickoff_tx.input[0].previous_output.vout;

    let operator_xonlypk: [u8; 32] = parse_op_return_data(&first_op_return_output.script_pubkey)
        .expect("Invalid operator xonlypk")
        .try_into()
        .expect("Invalid xonlypk");

    let deposit_constant = deposit_constant(
        operator_xonlypk,
        input.watchtower_challenge_connector_start_idx,
        &input.all_tweaked_watchtower_pubkeys,
        *move_txid,
        round_txid,
        kickoff_round_vout,
        input.hcp.genesis_state_hash,
    );
```

**File:** core/src/builder/transaction/operator_reimburse.rs (L341-384)
```rust
pub fn create_reimburse_txhandler(
    move_txhandler: &TxHandler,
    round_txhandler: &TxHandler,
    kickoff_txhandler: &TxHandler,
    kickoff_idx: usize,
    paramset: &'static ProtocolParamset,
    operator_reimbursement_address: &bitcoin::Address,
) -> Result<TxHandler, BridgeError> {
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

**File:** core/src/operator.rs (L620-674)
```rust
        let payout_txhandler = builder::transaction::create_payout_txhandler(
            input_utxo,
            output_txout,
            self.signer.xonly_public_key,
            in_signature,
            self.config.protocol_paramset().network,
        )?;

        // tracing::info!("Payout txhandler: {:?}", hex::encode(bitcoin::consensus::serialize(&payout_txhandler.get_cached_tx())));

        let sighash = payout_txhandler.calculate_sighash_txin(0, in_signature.sighash_type)?;

        SECP.verify_schnorr(
            &in_signature.signature,
            &Message::from_digest(*sighash.as_byte_array()),
            user_xonly_pk,
        )
        .wrap_err("Failed to verify signature received from user for payout txin. Ensure the signature uses SinglePlusAnyoneCanPay sighash type.")?;

        let fee_rate = self
            .rpc
            .get_fee_rate_kvb(
                self.config.protocol_paramset.network,
                &self.config.mempool_api_host,
                &self.config.mempool_api_endpoint,
                self.config.tx_sender_limits.mempool_fee_rate_multiplier,
                self.config.tx_sender_limits.mempool_fee_rate_offset_sat_kvb,
                self.config.tx_sender_limits.fee_rate_hard_cap,
            )
            .await?;

        // send payout tx using RBF
        let funded_tx = self
            .rpc
            .fund_raw_transaction(
                payout_txhandler.get_cached_tx(),
                Some(&bitcoincore_rpc::json::FundRawTransactionOptions {
                    add_inputs: Some(true),
                    include_unsafe: Some(false),
                    change_address: None,
                    change_position: Some(1),
                    change_type: None,
                    include_watching: None,
                    lock_unspents: Some(false),
                    fee_rate: Some(Amount::from_sat(fee_rate.to_sat_per_kvb())),
                    subtract_fee_from_outputs: None,
                    replaceable: None,
                    conf_target: None,
                    estimate_mode: None,
                }),
                None,
            )
            .await
            .wrap_err("Failed to fund raw transaction")?
            .hex;
```

**File:** core/src/test/withdraw.rs (L133-144)
```rust
        let citrea_withdrawal_tx = citrea_client
            .contract
            .withdraw(
                FixedBytes::from(withdrawal_utxo.txid.to_raw_hash().to_byte_array()),
                FixedBytes::from(withdrawal_utxo.vout.to_le_bytes()),
            )
            .value(U256::from(
                config.protocol_paramset().bridge_amount.to_sat() * SATS_TO_WEI_MULTIPLIER,
            ))
            .send()
            .await
            .unwrap();
```
