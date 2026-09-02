### Title
Bridge circuit never checks that the withdrawer is actually paid before committing a reimbursement journal - ([File: circuits-lib/src/bridge_circuit/mod.rs])

### Summary
`bridge_circuit` binds the payout transaction to the registered withdrawal only by comparing `input[payout_input_index].previous_output` (txid+vout) to the `(user_wd_outpoint, vout)` returned by `verify_storage_proofs`. It never inspects `payout_spv.transaction.output` for a payment to the withdrawer's script/amount, and `verify_storage_proofs` never even reads a withdrawal amount from Citrea storage. Since the withdrawal UTXO is a plain key-controlled UTXO owned solely by the withdrawer, the withdrawer can spend it however they like (e.g., all-to-fee/OP_RETURN, 0 sats to themselves) and any operator using that transaction as their `payout_spv` will still pass every circuit check and be reimbursed.

### Finding Description
The broken binding, as claimed: `value paid to withdrawer's script in payout_spv.transaction.output` == `amount the Bridge contract recorded as owed`. Tracing the code shows this equality is **never evaluated**:

- `verify_storage_proofs` only derives `(wd_outpoint, vout, move_txid)` from Citrea's `UTXOS_STORAGE_INDEX` slots; there is no amount/value read from storage at all. [1](#0-0) 
- `bridge_circuit` uses that tuple only to assert the *identity* of the spent outpoint (txid and vout of `input[payout_input_index].previous_output`), never any output value or destination script: [2](#0-1) 
- The only other payout-transaction requirement is that *some* OP_RETURN output exists carrying the operator's x-only pubkey (used for `deposit_constant`), which says nothing about payment to the withdrawer: [3](#0-2) 
- `payout_input_index` is used directly as an index with no bound check against `transaction.input.len()` beyond Rust's runtime panic-on-out-of-bounds (which is not a value-binding check).

The only place amount/script correctness is enforced is off-circuit, voluntarily, in the honest operator's own tx-construction code path (`Operator::withdraw`), where the operator computes the sighash for `SinglePlusAnyoneCanPay` and calls `SECP.verify_schnorr` before broadcasting: [4](#0-3) 
and in `create_payout_txhandler`, which places `output_txout` at index 0 to match the SIGHASH_SINGLE commitment: [5](#0-4) 

This is not a circuit-enforced or consensus-enforced guarantee: the withdrawal "UTXO" registered by the user is a UTXO controlled solely by the withdrawer's own private key (a `Actor`/user-owned taproot key-spend output, as seen in test helpers building it), not a cooperative multisig output: [6](#0-5) 
Because the withdrawer alone controls this key, nothing on Bitcoin or in the circuit prevents them from signing and broadcasting an entirely different transaction spending that exact outpoint with SIGHASH_ALL (or any type) to an OP_RETURN/fee/self output instead of using the pre-agreed `SinglePlusAnyoneCanPay` signature bound to `output_script_pubkey`/`output_amount`. Any transaction spending that outpoint — regardless of who signed it or for what sighash — satisfies `bridge_circuit`'s only checks (outpoint identity + an OP_RETURN containing a valid operator pubkey), because the circuit performs no signature/witness verification on `payout_spv.transaction.input[payout_input_index]` and no output-value verification at all.

Existing guards do not close this gap: `Operator::is_profitable`/`SECP.verify_schnorr` checks in `operator.rs::withdraw` only apply if the *honest* code path is used to build the payout tx; they are bypassed entirely if a different (attacker/withdrawer-crafted or colluding-operator-crafted) transaction spending the same outpoint is used as `payout_spv` input to the circuit. `verify_storage_proofs`, `SPV::verify`, and `lc_proof_verifier` only prove the transaction is real and included in the chain — they say nothing about its outputs. There is no watchtower/Disprove mechanism that can catch this, since the fabricated proof is fully self-consistent and "valid" under the circuit's own (incomplete) rules — this is a soundness bug in the circuit's business logic, not a computational-integrity bug that Disprove can catch.

### Impact Explanation
An operator whose kickoff/assert chain incorporates such a `payout_spv` (either because they are complicit, are the withdrawer themselves, or accept an externally supplied transaction as "proof of payout") reaches `journal_hash` commitment and drives Assert → ChallengeTimeout/Reimburse to completion, releasing the 10 BTC move-to-vault UTXO with zero (or near-zero) actual payment to the withdrawer. This is exactly the listed Critical categories: "BTC leaving a move-to-vault UTXO without a matching fronted withdrawal" and "an operator reimbursed for a payout it never funded." It is repeatable per deposit/withdrawal and per operator, since the missing check is structural in `bridge_circuit`, not tied to a specific deposit's data.

### Likelihood Explanation
The precondition is that some operator (who need not hold any special key beyond ordinary operator collateral, and who is not a verifier/aggregator/watchtower) uses a payout transaction that was not honestly constructed via `Operator::withdraw`'s signature-verified path — e.g., the operator is the withdrawer of their own self-registered "withdrawal" (fully controllable, zero collusion needed with any third party), or accepts/produces a payout tx that spends the registered outpoint to OP_RETURN/fee. Constructing the OP_RETURN with a real operator's x-only pubkey requires no secret (`operator_xonlypk` is public). Cost is only Bitcoin transaction fees, and it is reusable across any deposit/withdrawal cycle that operator manages, making this fully feasible and repeatable with no mainnet/live Citrea dependency for demonstrating the circuit-level gap.

### Recommendation
Extend `verify_storage_proofs` (and the corresponding Citrea `UTXOS_STORAGE_INDEX` schema) to also commit and return the expected withdrawal amount/destination script, and have `bridge_circuit` explicitly assert that `payout_spv.transaction.output[payout_output_index]` pays that exact script and amount (analogous to the SIGHASH_SINGLE binding already used off-circuit), instead of only checking which outpoint is consumed.

### Proof of Concept
```rust
// circuits-lib/src/bridge_circuit/storage_proof.rs (test module)
#[test]
fn test_bridge_circuit_accepts_zero_payment_to_withdrawer() {
    // 1. Build a StorageProof whose UTXOS_STORAGE_INDEX slot commits to
    //    (attacker_dust_utxo_txid, vout) exactly as registered via Citrea `withdraw`.
    // 2. Craft `payout_tx`:
    //    - input[0].previous_output = attacker_dust_utxo (spent with attacker's own
    //      SIGHASH_ALL signature, not the pre-agreed SinglePlusAnyoneCanPay one)
    //    - output[0] = OP_RETURN(operator_xonlypk)
    //    - output[1] = anchor/fee output; NO output pays attacker's real destination script
    // 3. Build matching SPV (merkle proof + block header) for payout_tx, and a valid
    //    light client proof / header chain proof consistent with it.
    // 4. Assemble BridgeCircuitInput { payout_spv, payout_input_index: 0, sp, hcp, lcp, ... }.
    // 5. Call bridge_circuit(...) (or verify_storage_proofs + the assert_eq! block directly)
    //    and assert it does NOT panic, proving:
    //       LHS: value paid to withdrawer's script in payout_tx.output == 0
    //       RHS: amount owed per withdrawal registration (output_amount from WithdrawParams) == N > 0
    //    LHS != RHS yet bridge_circuit still succeeds -> guest.commit(journal_hash) reached.
}
```

### Citations

**File:** circuits-lib/src/bridge_circuit/storage_proof.rs (L119-132)
```rust
    let buf: [u8; 32] = vout_storage_proof.value.to_be_bytes();

    // ENDIANNESS SHOULD BE CHECKED THIS FIELD IS 4 BYTES in the contract
    let vout = u32::from_le_bytes(
        buf[28..32]
            .try_into()
            .expect("Vout value conversion failed"),
    );

    let wd_outpoint = WithdrawalOutpointTxid(utxo_storage_proof.value.to_be_bytes());

    let move_txid = MoveTxid(deposit_storage_proof.value.to_be_bytes());

    (wd_outpoint, vout, move_txid)
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

**File:** core/src/operator.rs (L614-637)
```rust
        let user_xonly_pk = &input_utxo
            .txout
            .script_pubkey
            .try_get_taproot_pk()
            .wrap_err("Input utxo script pubkey is not a valid taproot script")?;

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

**File:** core/src/test/common/setup_utils.rs (L480-543)
```rust
async fn generate_withdrawal_utxo(config: &BridgeConfig, rpc: &ExtendedBitcoinRpc) -> UTXO {
    let signer = Actor::new(config.secret_key, config.protocol_paramset().network);

    const WITHDRAWAL_EMPTY_UTXO_SATS: bitcoin::Amount = bitcoin::Amount::from_sat(550);

    let dust_outpoint = rpc
        .send_to_address(&signer.address, WITHDRAWAL_EMPTY_UTXO_SATS)
        .await
        .expect("Failed to send to address");

    UTXO {
        outpoint: dust_outpoint,
        txout: bitcoin::TxOut {
            value: WITHDRAWAL_EMPTY_UTXO_SATS,
            script_pubkey: signer.address.script_pubkey(),
        },
    }
}

fn sign_withdrawal_output(
    config: &BridgeConfig,
    dust_utxo: &UTXO,
    withdrawal_address: &bitcoin::Address,
    withdrawal_amount: bitcoin::Amount,
) -> (bitcoin::TxOut, taproot::Signature) {
    let signer = Actor::new(config.secret_key, config.protocol_paramset().network);
    let txin = builder::transaction::input::SpendableTxIn::new(
        dust_utxo.outpoint,
        dust_utxo.txout.clone(),
        vec![],
        None,
    );
    let txout = bitcoin::TxOut {
        value: withdrawal_amount,
        script_pubkey: withdrawal_address.script_pubkey(),
    };
    let unspent_txout = builder::transaction::output::UnspentTxOut::from_partial(txout.clone());

    let tx = builder::transaction::TxHandlerBuilder::new(TransactionType::Payout)
        .with_version(NON_STANDARD_V3)
        .add_input(
            NormalSignatureKind::NotStored,
            txin,
            SpendPath::KeySpend,
            builder::transaction::DEFAULT_SEQUENCE,
        )
        .add_output(unspent_txout.clone())
        .finalize();

    let sighash = tx
        .calculate_sighash_txin(0, sighash::TapSighashType::SinglePlusAnyoneCanPay)
        .expect("Failed to calculate sighash");

    let sig = signer
        .sign_with_tweak_data(sighash, builder::sighash::TapTweakData::KeyPath(None), None)
        .expect("Failed to sign");

    let sig = taproot::Signature {
        signature: sig,
        sighash_type: sighash::TapSighashType::SinglePlusAnyoneCanPay,
    };

    (txout, sig)
}
```
