### Title
Bridge circuit accepts a payout transaction without checking output value/script to the withdrawer, and no sighash-type restriction binds the withdrawal signature to a specific output - (File: circuits-lib/src/bridge_circuit/mod.rs)

### Summary
`bridge_circuit` (`circuits-lib/src/bridge_circuit/mod.rs:190-204`) only verifies that `input.payout_spv.transaction.input[payout_input_index].previous_output` matches the withdrawal outpoint recorded on Citrea; it never checks that any output of that transaction pays the withdrawal amount to the withdrawer's script. Because `Operator::withdraw` (`core/src/operator.rs:630-637`) accepts an attacker-chosen `in_signature.sighash_type` with no restriction to an output-committing type, a withdrawer can sign with a non-output-committing sighash flag (e.g. `SIGHASH_NONE|ANYONECANPAY`) and later confirm a different transaction that spends the registered withdrawal UTXO but sends the value elsewhere, framing an operator's payout-checker/kickoff pipeline into producing a valid Reimburse claim for a payout that never happened.

### Finding Description
Binding claimed by the protocol: `value_paid_to(withdrawer_script) in accepted payout tx == withdrawal_amount recorded at citrea_idx`.

Trace:
- `verify_storage_proofs` (`circuits-lib/src/bridge_circuit/storage_proof.rs:44-133`) only returns `(user_wd_outpoint, vout, move_txid)` — an outpoint identifier, never an amount or destination script.
- `bridge_circuit` uses this outpoint solely to assert that `payout_spv.transaction.input[payout_input_index].previous_output` == that outpoint (`mod.rs:190-204`). No output of `payout_spv.transaction` is inspected for value or script pubkey anywhere in `bridge_circuit`, `verify_watchtower_challenges`, `total_work_and_watchtower_flags`, or `get_first_op_return_output` (`mod.rs:688-692`, only used to read the operator xonly-pubkey for `deposit_constant`).
- The only place that could bind the payout's output content to the withdrawer is Bitcoin's own signature check when the withdrawal UTXO is spent via Taproot key-path spend (`create_payout_txhandler`, `core/src/builder/transaction/operator_reimburse.rs:407-436`, `set_p2tr_key_spend_witness`).
- `Operator::withdraw` computes and verifies this signature using the *caller-supplied* `in_signature.sighash_type` with no enforcement of a specific (output-committing) sighash type (`core/src/operator.rs:630-637`); the surrounding error message only *suggests* `SinglePlusAnyoneCanPay` but does not require it.
- If the withdrawer (attacker, fully in control of their own withdrawal request per the rules — they choose the UTXO, the signature, and its sighash flag) signs with `SIGHASH_NONE|ANYONECANPAY` (or plain `SIGHASH_NONE`), the resulting signature does not commit to any transaction output (see `taproot_encode_signing_data_to_with_annex_digest`, `mod.rs:801-810` and `850-862`: the output-commitment branches are skipped entirely for `None`, and for `anyone_can_pay` the input index is not even encoded). The operator's own `verify_schnorr` check still succeeds because it only re-derives the sighash of whatever transaction the operator itself constructed — it says nothing about other transactions that could reuse the same signature.
- The attacker can then broadcast (or race to confirm) a different transaction that spends the exact same withdrawal outpoint with the same signature but arbitrary outputs (e.g. 0 sats to the withdrawer, rest to fees or an attacker address), and can freely forge the `OP_RETURN` payload naming any operator's x-only pubkey, since `update_finalized_payouts` (`core/src/verifier.rs:2283-2352`) derives `operator_xonly_pk` purely from that OP_RETURN with no independent check.
- If this attacker transaction confirms, `PayoutCheckerTask::run_once` (`core/src/task/payout_checker.rs:39-111`) will find an "unhandled payout" keyed to the framed operator's xonly-pubkey and call `handle_finalized_payout`, which drives the kickoff/Assert flow and eventually produces a `BridgeCircuitInput` whose `payout_input_index` correctly points at the spend of the withdrawal outpoint. `bridge_circuit` will accept it because it never checks the output value/script, producing a valid `journal_hash` that lets Reimburse succeed.

None of the guards listed in the audit rules close this gap: `verify_storage_proofs` only proves the outpoint identity, not the amount/script; `SECP.verify_schnorr` only proves *a* valid signature exists for *some* sighash, not that outputs are pinned; there is no `is_profitable`/amount check inside `bridge_circuit` or `handle_finalized_payout`; and the presigned transaction graph does not cover the payout tx (it is a spend of a user-owned UTXO, not a Clementine-graph tx).

### Impact Explanation
Bridge value moves out of the move-to-vault UTXO via Reimburse credited to an operator (the one named in the forged OP_RETURN) even though the withdrawer never received the withdrawal amount — matching the Critical category "an operator reimbursed for a payout it never funded" / "BTC leaving a move-to-vault UTXO without a matching fronted withdrawal." This is repeatable per withdrawal a malicious withdrawer initiates and per operator they choose to frame with a forged OP_RETURN, and does not require operator, verifier, or aggregator privilege — only control over one's own withdrawal request/signature and the ability to broadcast/RBF a competing Bitcoin transaction.

### Likelihood Explanation
Preconditions: the attacker must be the withdrawer of their own Citrea withdrawal, and must choose a non-output-committing sighash flag for `in_signature` when calling the `withdraw`/`optimistic_payout` gRPC — this is entirely within an unprivileged user's control per the stated capabilities, with no code path forcing `SinglePlusAnyoneCanPay`. The attacker also needs to win a fee race/RBF against the operator's legitimately-broadcast payout so their bogus-output transaction confirms instead; this only costs mining fees. Feasibility is high given code inspection shows no sighash-type restriction and no output verification in `bridge_circuit`.

### Recommendation
1. Enforce a fixed, output-committing sighash type (`SIGHASH_SINGLE|ANYONECANPAY` or `SIGHASH_ALL`) for the withdrawal-authorizing signature in `Operator::withdraw`/`sign_optimistic_payout`, rejecting any other `sighash_type`.
2. In `bridge_circuit`, explicitly check that the payout transaction contains an output whose script_pubkey and value match the withdrawal's registered destination and amount (these must be committed on Citrea at withdrawal time, not just the outpoint), rather than relying solely on `previous_output` equality of the input.
3. Add bounds checking for `payout_input_index` against `transaction.input.len()` to avoid separate panics/DoS-adjacent issues, though this is secondary to the missing output binding.

### Proof of Concept
```
cargo test -p circuits-lib bridge_circuit_missing_output_binding -- --nocapture
```
Test plan:
1. Build a `BridgeCircuitInput` using existing test fixtures (as in `circuits-lib/src/bridge_circuit/mod.rs` test module and `storage_proof.rs` test data) where `payout_spv.transaction.input[payout_input_index].previous_output` correctly equals the withdrawal outpoint/vout returned by `verify_storage_proofs`.
2. Construct `payout_spv.transaction` with a single non-OP_RETURN output whose `value == Amount::ZERO` and `script_pubkey` different from the withdrawer's registered address (plus a required OP_RETURN output with an arbitrary operator xonly pubkey to satisfy `get_first_op_return_output`).
3. Call `bridge_circuit(guest, work_only_image_id)` (or directly exercise the assertions/`get_first_op_return_output` path) and assert it does **not** panic and successfully produces a `journal_hash`.
4. Assert on both sides of the claimed binding: `withdrawal_amount_at_citrea_idx` (a nonzero constant from the Citrea bridge contract's withdrawal record) vs. `value_paid_to(withdrawer_script)` computed from `payout_spv.transaction.output` (assert it equals 0), demonstrating the two are never checked equal anywhere along the accepted path. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** circuits-lib/src/bridge_circuit/mod.rs (L182-207)
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

    let first_op_return_output = get_first_op_return_output(&input.payout_spv.transaction)
        .expect("Payout transaction must have an OP_RETURN output");
```

**File:** circuits-lib/src/bridge_circuit/mod.rs (L801-862)
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

    // Data about this input:
    let mut spend_type = 0u8;
    if annex_hash.is_some() {
        spend_type |= 1u8;
    }
    if leaf_hash_code_separator.is_some() {
        spend_type |= 2u8;
    }
    spend_type.consensus_encode(writer).expect(expect_msg);

    if anyone_can_pay {
        let txin = tx.tx_in(input_index).expect("invalid input index");
        let previous_output =
            get_for_prevouts(prevouts, input_index).expect("invalid prevout for input index");
        txin.previous_output
            .consensus_encode(writer)
            .expect(expect_msg);
        previous_output
            .borrow()
            .value
            .consensus_encode(writer)
            .expect(expect_msg);
        previous_output
            .borrow()
            .script_pubkey
            .consensus_encode(writer)
            .expect(expect_msg);
        txin.sequence.consensus_encode(writer).expect(expect_msg);
    } else {
        (input_index as u32)
            .consensus_encode(writer)
            .expect(expect_msg);
    }

    if let Some(hash) = annex_hash {
        hash.consensus_encode(writer).expect(expect_msg);
    }

    // Data about this output:
    if sighash == TapSighashType::Single {
        let mut enc_single_output = sha256::Hash::engine();
        let output = tx
            .output
            .get(input_index)
            .expect("SIGHASH_SINGLE requires a corresponding output");
        output
            .consensus_encode(&mut enc_single_output)
            .expect(expect_msg);
        let hash = sha256::Hash::from_engine(enc_single_output);
        hash.consensus_encode(writer).expect(expect_msg);
    }
```

**File:** circuits-lib/src/bridge_circuit/storage_proof.rs (L44-133)
```rust
pub fn verify_storage_proofs(
    storage_proof: &StorageProof,
    state_root: [u8; 32],
) -> (WithdrawalOutpointTxid, u32, MoveTxid) {
    let utxo_storage_proof: EIP1186StorageProof =
        serde_json::from_str(&storage_proof.storage_proof_utxo)
            .expect("Failed to deserialize UTXO storage proof");

    let vout_storage_proof: EIP1186StorageProof =
        serde_json::from_str(&storage_proof.storage_proof_vout)
            .expect("Failed to deserialize vout storage proof");

    let deposit_storage_proof: EIP1186StorageProof =
        serde_json::from_str(&storage_proof.storage_proof_deposit_txid)
            .expect("Failed to deserialize deposit storage proof");

    let storage_address: U256 = {
        let mut keccak = Keccak256::new();
        keccak.update(UTXOS_STORAGE_INDEX);
        let hash = keccak.finalize();
        U256::from_be_bytes(
            <[u8; 32]>::try_from(&hash[..]).expect("Hash slice has incorrect length"),
        )
    };

    let storage_key_utxo: alloy_primitives::Uint<256, 4> =
        storage_address + U256::from(storage_proof.index * 2);

    let storage_key_vout: alloy_primitives::Uint<256, 4> =
        storage_address + U256::from(storage_proof.index * 2 + 1);

    let storage_address_deposit: U256 = {
        let mut keccak = Keccak256::new();
        keccak.update(DEPOSIT_STORAGE_INDEX);
        let hash = keccak.finalize();
        U256::from_be_bytes(
            <[u8; 32]>::try_from(&hash[..]).expect("Hash slice has incorrect length"),
        )
    };

    let deposit_storage_key: alloy_primitives::Uint<256, 4> =
        storage_address_deposit + U256::from(storage_proof.index);

    let deposit_storage_key_bytes = deposit_storage_key.to_be_bytes::<32>();

    if deposit_storage_key_bytes != deposit_storage_proof.key.as_b256().0 {
        panic!(
            "Invalid deposit storage key. left: {:?} right: {:?}",
            deposit_storage_key_bytes,
            deposit_storage_proof.key.as_b256().0
        );
    }

    if storage_key_utxo.to_be_bytes() != utxo_storage_proof.key.as_b256().0 {
        panic!(
            "Invalid withdrawal UTXO storage key. left: {:?} right: {:?}",
            storage_key_utxo.to_be_bytes::<32>(),
            utxo_storage_proof.key.as_b256().0
        );
    }

    if storage_key_vout.to_be_bytes() != vout_storage_proof.key.as_b256().0 {
        panic!(
            "Invalid withdrawal vout storage key. left: {:?} right: {:?}",
            storage_key_vout.to_be_bytes::<32>(),
            vout_storage_proof.key.as_b256().0
        );
    }

    storage_verify(&utxo_storage_proof, state_root);

    storage_verify(&deposit_storage_proof, state_root);

    storage_verify(&vout_storage_proof, state_root);

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

**File:** core/src/verifier.rs (L2311-2343)
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

            tracing::info!(
                "A new payout tx detected for withdrawal {}, payout txid: {:?}, operator xonly pk: {:?}",
                idx,
                payout_txid,
                operator_xonly_pk
            );

            payout_txs_and_payer_operator_idx.push((
                idx,
                payout_txid,
                operator_xonly_pk,
                block_hash,
            ));
        }
```

**File:** core/src/task/payout_checker.rs (L39-111)
```rust
    async fn run_once(&mut self) -> Result<Self::Output, BridgeError> {
        let mut dbtx = self.db.begin_transaction().await?;
        let unhandled_payout = self
            .db
            .get_first_unhandled_payout_by_operator_xonly_pk(
                Some(&mut dbtx),
                self.operator.signer.xonly_public_key,
            )
            .await?;

        if unhandled_payout.is_none() {
            return Ok(false);
        }

        let (citrea_idx, move_to_vault_txid, payout_tx_blockhash) =
            unhandled_payout.expect("Must be Some");

        tracing::info!(
            "Unhandled payout found for withdrawal {}, move_txid: {}",
            citrea_idx,
            move_to_vault_txid
        );

        let deposit_data = self
            .db
            .get_deposit_data_with_move_tx(Some(&mut dbtx), move_to_vault_txid)
            .await?;
        if deposit_data.is_none() {
            return Err(eyre::eyre!("Fronted withdrawal for move tx {move_to_vault_txid} found, but the signatures for the deposit are not found in the db.").into());
        }

        let deposit_data = deposit_data.expect("Must be Some");

        let kickoff_txid = self
            .operator
            .handle_finalized_payout(
                &mut dbtx,
                deposit_data.get_deposit_outpoint(),
                payout_tx_blockhash,
            )
            .await?;

        // fetch and save the LCP for if we get challenged and need to provide proof of payout later
        let (_, payout_block_height) = self
            .operator
            .db
            .get_block_info_from_hash(Some(&mut dbtx), payout_tx_blockhash)
            .await?
            .ok_or_eyre("Couldn't find payout blockhash in bitcoin sync")?;

        let _ = self
            .operator
            .citrea_client
            .fetch_validate_and_store_lcp(
                payout_block_height as u64,
                citrea_idx,
                &self.operator.db,
                Some(&mut dbtx),
                self.operator.config.protocol_paramset(),
            )
            .await?;

        #[cfg(feature = "automation")]
        self.operator.end_round(&mut dbtx).await?;

        self.db
            .mark_payout_handled(Some(&mut dbtx), citrea_idx, kickoff_txid)
            .await?;

        dbtx.commit().await?;

        Ok(true)
    }
```
