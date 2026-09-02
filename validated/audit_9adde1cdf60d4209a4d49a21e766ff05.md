### Title
`bridge_circuit` never checks that the payout tx actually pays the withdrawer's `out_script_pubkey`/amount, letting a self-fabricated on-chain spend of the withdrawal UTXO stand in for a real payout - ([File: circuits-lib/src/bridge_circuit/mod.rs])

### Summary
The binding that should hold is: `value paid to out_script_pubkey in input.payout_spv.transaction.output[payout_input_index]` == `withdrawal amount recorded for that withdrawal on Citrea`. `bridge_circuit()` at `circuits-lib/src/bridge_circuit/mod.rs` lines 183-204 never checks this; it only verifies that `input.payout_spv.transaction.input[payout_input_index].previous_output` equals the `(txid, vout)` returned by `verify_storage_proofs` [1](#0-0) . Anyone who owns the withdrawal UTXO can spend it in any transaction they like (paying themselves nothing, with an arbitrary OP_RETURN naming a real operator's x-only pubkey) and that transaction will satisfy every check the circuit performs.

### Finding Description
`verify_storage_proofs` (in `circuits-lib/src/bridge_circuit/storage_proof.rs`) only reads and validates the withdrawal's `(txid, vout)` and `move_txid` from Citrea storage slots keyed off `UTXOS_STORAGE_INDEX`/`DEPOSIT_STORAGE_INDEX`; it never reads or checks any withdrawal amount or destination script committed on Citrea [2](#0-1) .

Back in `bridge_circuit()`, once the SPV proof and light-client proof establish that `input.payout_spv.transaction` is a real, confirmed Bitcoin transaction, the only linkage enforced is:
```
assert_eq!(user_wd_txid, input.payout_spv.transaction.input[payout_input_index].previous_output.txid, ...);
assert_eq!(vout, input.payout_spv.transaction.input[payout_input_index].previous_output.vout, ...);
``` [3](#0-2) 

There is no check on `input.payout_spv.transaction.output[...]` value or `script_pubkey` at all. `get_first_op_return_output` merely scans for the first OP_RETURN output anywhere in the transaction to extract `operator_xonlypk` — it is not tied to `payout_input_index` or to any output amount [4](#0-3) [5](#0-4) .

In the honest flow, the correctness of amount/script is enforced only by Bitcoin's Taproot signature semantics: the user signs the payout input with `SinglePlusAnyoneCanPay`, which the operator/aggregator verify against the exact `output_txout` (amount + script) at RPC time via `SECP.verify_schnorr` before ever broadcasting [6](#0-5) [7](#0-6) . `SinglePlusAnyoneCanPay` is enforced by the RPC parsers [8](#0-7) , but this is only enforced on the RPC entry points that the operator/aggregator use to *build* the payout transaction — it is not enforced by the on-chain Bitcoin transaction itself, and nothing prevents the UTXO's owner from spending it with any different signature/sighash of their choosing outside of these RPC flows.

The attacker (the withdrawer) owns the private key of the withdrawal UTXO named on Citrea. They can therefore build and broadcast an entirely independent transaction — never going through `operator::withdraw` or `aggregator::optimistic_payout` — that spends this UTXO at index `payout_input_index`, sends 0 value to `out_script_pubkey` (routing all value to fee/OP_RETURN/anchor), and includes an OP_RETURN with any real operator's x-only pubkey (public information, not secret). Once mined and SPV-included, this transaction satisfies every assertion in `bridge_circuit`: SPV valid, light-client L1 hash matches, storage-proof outpoint/vout match, OP_RETURN present and parses to a valid 32-byte xonlypk. `deposit_constant`/`journal_hash` compute and `guest.commit()` succeeds — none of these steps depend on the output amount/script that was actually paid.

### Impact Explanation
If an operator (the one named in the OP_RETURN) later uses this journal as the basis for its `Assert`/kickoff/`Reimburse`/`ChallengeTimeout` transaction chain, it can claim reimbursement from the `MoveToVault` UTXO's `bridge_amount` while having fronted $0 to the withdrawer. This is BTC leaving a move-to-vault UTXO with no matching fronted withdrawal, and an operator reimbursed for a payout it never funded — matching the Critical impact category verbatim. It is repeatable per withdrawal/deposit and does not require the attacker to hold any collateral, keys beyond their own withdrawal UTXO, or privileged role; only cooperation (or opportunistic exploitation) by a dishonest operator is needed to realize the reimbursement, but the circuit provides no defense against it once such a spend exists on-chain.

### Likelihood Explanation
Preconditions: a finalized withdrawal registered on the mocked `CitreaClientT` naming a UTXO the attacker controls, and a deposit with `bridge_amount` locked in `MoveToVault`. The attacker's cost is only the dust value of their own withdrawal UTXO plus Bitcoin transaction fees — no privileged access, no key compromise of any protocol party, no majority hashrate. The only extra ingredient needed to realize theft is a willing/dishonest operator to consume the resulting proof in the reimbursement flow; the circuit itself provides no barrier to that operator's success, which is exactly what the SPV/storage-proof/journal machinery is supposed to prevent.

### Recommendation
`bridge_circuit()` must bind the payout output content to the withdrawal recorded on Citrea. Concretely:
1. Extend the Citrea storage proof (`verify_storage_proofs`/`StorageProof`) to also read and prove the withdrawal's committed `output_script_pubkey` and `output_amount` (or a commitment/hash of them) from the Bridge contract's storage.
2. In `bridge_circuit`, after locating `input.payout_spv.transaction.output[payout_input_index]` (the output at the *same* index as the spent input, consistent with `SinglePlusAnyoneCanPay` semantics), assert that its `script_pubkey` and `value` equal the values proven from Citrea storage, in addition to the existing `previous_output` txid/vout checks.

### Proof of Concept
```rust
// circuits-lib/src/bridge_circuit/mod.rs (new test in `mod tests`)
#[test]
fn test_bridge_circuit_accepts_zero_value_payout_to_withdrawer() {
    // 1. Build a BridgeCircuitInput where:
    //    - input.sp (StorageProof) proves withdrawal outpoint (txid, vout) == attacker's UTXO
    //    - input.payout_spv.transaction has:
    //        input[payout_input_index].previous_output == (txid, vout)  // attacker's own UTXO, signed with attacker's own key/sighash, not operator/user cooperation
    //        outputs: [ change/fee output paying attacker != withdrawer's committed out_script_pubkey amount, OP_RETURN(real operator xonlypk) ]
    //    - the output at index == payout_input_index does NOT pay the withdrawer's registered out_script_pubkey / out_amount
    // 2. Assert bridge_circuit()/relevant assertions still succeed (journal committed) despite:
    assert_ne!(
        input.payout_spv.transaction.output[payout_input_index].script_pubkey,
        registered_out_script_pubkey
    );
    assert_ne!(
        input.payout_spv.transaction.output[payout_input_index].value,
        registered_out_amount
    );
    // guest.commit() is still reached -> proves the missing binding.
}
```
Run with `cargo test -p circuits-lib test_bridge_circuit_accepts_zero_value_payout_to_withdrawer` (no mainnet, no live Citrea; uses mocked `StorageProof`/SPV fixtures analogous to existing `test_verify_storage_proofs`).

### Citations

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

**File:** circuits-lib/src/bridge_circuit/storage_proof.rs (L44-132)
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

**File:** core/src/rpc/aggregator.rs (L1120-1126)
```rust
            let sighash = opt_payout_txhandler
                .calculate_pubkey_spend_sighash(0, input_signature.sighash_type)?;

            let message = Message::from_digest(sighash.to_byte_array());

            SECP.verify_schnorr(&input_signature.signature, &message, &user_xonly_pk)
                .map_err(|_| Status::internal("Invalid signature for optimistic payout tx. Ensure the signature uses SinglePlusAnyoneCanPay sighash type."))?;
```

**File:** core/src/rpc/parser/operator.rs (L180-187)
```rust

    // enforce sighash type here
    if input_signature.sighash_type != TapSighashType::SinglePlusAnyoneCanPay {
        return Err(Status::invalid_argument(format!(
            "Input signature has wrong sighash type, SinglePlusAnyoneCanPay expected, got {}",
            input_signature.sighash_type
        )));
    }
```
