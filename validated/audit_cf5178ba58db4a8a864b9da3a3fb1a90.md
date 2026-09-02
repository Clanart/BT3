## Analysis

**Binding claimed to hold:** `deposit_constant.operator_xonlypk == the bitcoin pubkey of the party whose own funds actually paid the withdrawal output in the payout transaction`.

**What the code actually enforces:** only that `operator_xonlypk` equals the bytes found by `get_first_op_return_output` / `parse_op_return_data` on the mined payout transaction — nothing ties that value to who supplied the funding inputs of that transaction.

### Trace

`create_payout_txhandler` builds the payout tx with a single signed input (the withdrawer's dust UTXO) and three outputs: the user's payout, a CPFP anchor, and an `OP_RETURN` carrying `operator_xonly_pk`, then applies the withdrawer's key-spend signature at index 0: [1](#0-0) 

The withdrawer's signature is required to use `SinglePlusAnyoneCanPay`, both enforced at parse time: [2](#0-1) 

and verified later at build time by whichever operator completes the tx: [3](#0-2) 

Under BIP341, `SIGHASH_SINGLE` only commits to the output at the same index as the signed input (index 0, the payout output); `ANYONECANPAY` lets any other input be substituted. The circuits-lib sighash routine itself special-cases `Single`/`None` to skip hashing all outputs: [4](#0-3) 

This means the anchor output and, critically, the `OP_RETURN` (attribution output) are **never covered by the signature at all**. Anyone possessing the withdrawer's signature (visible on gRPC, or once broadcast, in the mempool) can build a rival transaction that still pays the user correctly (satisfying the sighash-committed output) but funds it from their own wallet and attaches an arbitrary `operator_xonly_pk` — or multiple `OP_RETURN` outputs — of their choosing.

Attribution is then derived purely from `get_first_op_return_output`, both by the verifier when indexing payouts: [5](#0-4) 

and by the bridge circuit / host when computing `deposit_constant` for disprove/reimbursement proofs: [6](#0-5) [7](#0-6) [8](#0-7) 

`is_kickoff_malicious` only cross-checks that the OP_RETURN-derived pubkey matches the kickoff sender's own pubkey and that the committed blockhash matches — it never checks that the named operator actually supplied the funding inputs of the payout transaction: [9](#0-8) 

So an attacker (any party who can see/replay a `SinglePlusAnyoneCanPay` signature — e.g. the withdrawer themselves, or anyone observing a pending payout tx in the mempool) can construct and broadcast a transaction that pays the withdrawal correctly but names an arbitrary/uninvolved operator in the (first) `OP_RETURN`. Once mined, `update_finalized_payouts` records that operator as payer, `is_kickoff_malicious` accepts a subsequent kickoff by that operator as legitimate, and the bridge circuit's `deposit_constant`/`journal_hash` will validate that operator's disprove-defense claim — even though that operator never funded anything.

### Title
Payout `OP_RETURN` attribution is unsigned, letting anyone attribute reimbursement credit to an operator who never funded the withdrawal - ([File: circuits-lib/src/bridge_circuit/mod.rs])

### Summary
The withdrawer's authorization signature for the payout transaction is required to be `SinglePlusAnyoneCanPay`, which under BIP341 commits only to the payout output at index 0 and leaves every other output — including the attribution-critical `OP_RETURN` carrying `operator_x

### Citations

**File:** core/src/builder/transaction/operator_reimburse.rs (L414-436)
```rust
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

**File:** core/src/rpc/parser/operator.rs (L174-187)
```rust
    if input_signature.sighash_type == TapSighashType::Default {
        tracing::warn!(
            "Input signature for withdrawal {} has sighash type default, setting to SinglePlusAnyoneCanPay", params.withdrawal_id,
        );
        input_signature.sighash_type = TapSighashType::SinglePlusAnyoneCanPay;
    }

    // enforce sighash type here
    if input_signature.sighash_type != TapSighashType::SinglePlusAnyoneCanPay {
        return Err(Status::invalid_argument(format!(
            "Input signature has wrong sighash type, SinglePlusAnyoneCanPay expected, got {}",
            input_signature.sighash_type
        )));
    }
```

**File:** core/src/operator.rs (L620-637)
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
```

**File:** circuits-lib/src/bridge_circuit/mod.rs (L206-219)
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
```

**File:** circuits-lib/src/bridge_circuit/mod.rs (L686-692)
```rust
/// Retrieves the first output of a transaction that is an OP_RETURN script. Used in various
/// contexts to extract metadata or constants from transactions.
pub fn get_first_op_return_output(tx: &CircuitTransaction) -> Option<&TxOut> {
    tx.output
        .iter()
        .find(|out| out.script_pubkey.is_op_return())
}
```

**File:** circuits-lib/src/bridge_circuit/mod.rs (L801-810)
```rust
    if sighash != TapSighashType::None && sighash != TapSighashType::Single {
        // Manually compute sha_outputs
        let mut enc_outputs = sha256::Hash::engine();
        for txout in tx.output.iter() {
            txout.consensus_encode(&mut enc_outputs).expect(expect_msg);
        }
        sha256::Hash::from_engine(enc_outputs)
            .consensus_encode(writer)
            .expect(expect_msg);
    }
```

**File:** core/src/verifier.rs (L1882-1890)
```rust
        let Some(operator_xonly_pk) = operator_xonly_pk_opt else {
            tracing::warn!("No operator xonly pk found in payout tx OP_RETURN, assuming malicious");
            return Ok(true);
        };

        if operator_xonly_pk != kickoff_data.operator_xonly_pk {
            tracing::warn!("Operator xonly pk for the payout does not match with the kickoff_data");
            return Ok(true);
        }
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

**File:** bridge-circuit-host/src/structs.rs (L485-503)
```rust
    let first_op_return_output = get_first_op_return_output(&input.payout_spv.transaction)
        .ok_or(BridgeCircuitHostParamsError::InvalidOperatorPubkey)?;

    let deposit_storage_proof: EIP1186StorageProof =
        serde_json::from_str(&input.sp.storage_proof_deposit_txid).map_err(|e| {
            BridgeCircuitHostParamsError::StorageProofDeserializationError(e.to_string())
        })?;

    let round_txid = input.kickoff_tx.input[0]
        .previous_output
        .txid
        .to_byte_array();

    let kickoff_round_vout = input.kickoff_tx.input[0].previous_output.vout;

    let operator_xonlypk: [u8; 32] = parse_op_return_data(&first_op_return_output.script_pubkey)
        .ok_or(BridgeCircuitHostParamsError::InvalidOperatorPubkey)?
        .try_into()
        .map_err(|_| BridgeCircuitHostParamsError::InvalidOperatorPubkey)?;
```
