Confirmed: `verify_storage_proofs` only extracts and verifies the withdrawal outpoint's `txid`/`vout` and the `move_txid` from Citrea storage — there is no output-amount field verified against L2 state at all. Combined with the earlier trace, the `bridge_circuit` function itself never inspects any `TxOut.value` in the payout transaction.

### Title
`bridge_circuit` never verifies that any BTC value is delivered to the withdrawer, allowing a fee-only payout tx to satisfy reimbursement - (File: circuits-lib/src/bridge_circuit/mod.rs)

### Summary
`bridge_circuit` in `circuits-lib/src/bridge_circuit/mod.rs` only checks that the input at `payout_input_index` spends the exact withdrawal outpoint (`txid`/`vout`) and that some output in the transaction is a valid OP_RETURN carrying an `operator_xonlypk`. It never checks the value of any output, so a transaction that sends 100% of the input's value to miner fees (with only an OP_RETURN output besides the spent input) is accepted and committed exactly the same as a real payout, letting an operator (or a colluding withdrawer) claim full reimbursement for a withdrawal that was never actually funded.

### Finding Description
Binding claimed to hold: `sum(BTC delivered to any address other than miners in the accepted payout tx) == withdrawal amount owed`. Tracing `bridge_circuit` ( [1](#0-0) ) shows the only assertions made about the payout transaction are:

1. `input[payout_input_index].previous_output.txid/vout == (user_wd_outpoint, vout)` from `verify_storage_proofs`.
2. A first OP_RETURN output exists somewhere in `payout_spv.transaction` and decodes to a 32-byte `operator_xonlypk` (`get_first_op_return_output` / `parse_op_return_data`).

`verify_storage_proofs` (`circuits-lib/src/bridge_circuit/storage_proof.rs:44-133`) only extracts and Merkle-verifies the withdrawal outpoint's `txid` and `vout` and the `move_txid` from the L2 storage root — it never reads or verifies any withdrawal *amount* field from Citrea state. No other part of `bridge_circuit` reads `input.payout_spv.transaction.output[..].value`.

Because the withdrawal UTXO is owned by the withdrawer's own Bitcoin key (a P2TR output), the withdrawer can spend it with any sighash type and any transaction shape they like — the honest flow (`Operator::withdraw`, `core/src/operator.rs:560-637`) is merely a *convention*: it requires `SinglePlusAnyoneCanPay` and calls `SECP.verify_schnorr` against a specific sighash tied to `create_payout_txhandler`'s output, but nothing on-chain or in the circuit forces the withdrawer to use that flow. A withdrawer can instead build and broadcast their own transaction: spend the exact withdrawal outpoint, add a single OP_RETURN output containing any valid operator's public x-only pubkey (public information, taken from `operators_xonly_pks` config), and leave the entire remaining value as miner fee — no output pays the withdrawer or anyone else.

Once mined and finalized, this transaction satisfies both checks in `bridge_circuit`: the outpoint matches, and the OP_RETURN decodes to a real operator pubkey. `Verifier::is_kickoff_malicious` (`core/src/verifier.rs:1859-1914`) only re-checks that the OP_RETURN's `operator_xonly_pk` matches `kickoff_data.operator_xonly_pk` and that the committed blockhash matches — it never checks output values either. If that operator (who is the exact class of actor this BitVM challenge/disprove/reimbursement mechanism is designed to keep honest) submits a kickoff referencing this transaction, the whole verifier pipeline and `bridge_circuit` will validate it as a legitimate payout and let the operator collect the full `bridge_amount` via Reimburse.

### Impact Explanation
An operator can be reimbursed the full vault/bridge amount for a withdrawal it never funded — literally zero BTC left the move-to-vault UTXO to any wallet besides miner fees on a dust withdrawal UTXO. This matches the Critical category "an operator reimbursed for a payout it never funded." The attack is repeatable per withdrawal/deposit and does not depend on any specific operator; any operator's public xonly pubkey can be embedded by an unprivileged withdrawer, and any operator who chooses to exploit this (which is exactly the malicious-operator threat model the BitVM reimbursement/disprove flow exists to defend against) can claim reimbursement it never earned.

### Likelihood Explanation
No mainnet or live Citrea is required to demonstrate the gap — it is a unit-level flaw in `bridge_circuit`'s and `verify_storage_proofs`'s value-blindness, reproducible entirely within `circuits-lib` using a crafted `BridgeCircuitInput`/`payout_spv.transaction`. Preconditions: a registered withdrawal outpoint (attacker controls its key since the withdrawer is unprivileged-but-key-holding by construction) and knowledge of a public operator xonly pubkey. Attacker cost is a single Bitcoin transaction fee. Full end-to-end reimbursement additionally requires an operator to submit the matching kickoff (outside attacker's unprivileged reach alone), but the circuit-level defect that makes this possible is squarely in `bridge_circuit`.

### Recommendation
Add an explicit amount check in `bridge_circuit`: require the payout transaction to contain an output (at a well-defined index, e.g. index 0, mirroring `create_payout_txhandler`'s layout) whose `script_pubkey`/`value` satisfy the withdrawal amount committed on the Citrea side (extend `StorageProof`/`verify_storage_proofs` to also prove and return the withdrawal amount from L2 storage, then assert `input.payout_spv.transaction.output[payout_output_index].value == withdrawal_amount_from_storage_proof` minus any allowed operator fee bound). Do not rely solely on `payout_input_index` and OP_RETURN presence.

### Proof of Concept
In `circuits-lib/src/bridge_circuit/mod.rs` (or a new test module), construct a `BridgeCircuitInput` where:
- `payout_spv.transaction` has exactly two outputs: an OP_RETURN with a valid 32-byte operator xonly pubkey, and no user-payout output (or a dust/anchor-sized output only), with `tx.output.iter().map(|o| o.value).sum() < tx.input[payout_input_index]'s prevout value` (i.e., the difference is consumed entirely as fee).
- `input[payout_input_index].previous_output` set to `(user_wd_outpoint, vout)` matching a mocked `verify_storage_proofs` result.
- Assert that `bridge_circuit` (or the equivalent checks it performs, since full `guest.commit` requires a zkVM harness) does not panic and produces a journal hash, i.e., `sum(payout_tx outputs value where script != OP_RETURN and script != anchor) == 0` while the function still succeeds — demonstrating the binding `sum(BTC delivered to withdrawer) == withdrawal amount owed` is violated (0 != owed amount) yet the circuit accepts. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** circuits-lib/src/bridge_circuit/mod.rs (L182-229)
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

**File:** core/src/verifier.rs (L1859-1914)
```rust
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

        let wt_derive_path = WinternitzDerivationPath::Kickoff(
            kickoff_data.round_idx,
            kickoff_data.kickoff_idx,
            self.config.protocol_paramset(),
        );
        let commits = extract_winternitz_commits(
            kickoff_witness,
            &[wt_derive_path],
            self.config.protocol_paramset(),
        )?;
        let blockhash_data = commits.first();
        // only last 20 bytes of the blockhash is committed
        let truncated_blockhash = &payout_blockhash[12..];
        if let Some(committed_blockhash) = blockhash_data {
            if committed_blockhash != truncated_blockhash {
                tracing::warn!("Payout blockhash does not match committed hash: committed: {:?}, truncated payout blockhash: {:?}",
                        blockhash_data, truncated_blockhash);
                return Ok(true);
            }
        } else {
            return Err(eyre::eyre!("Couldn't retrieve committed data from witness").into());
        }
        Ok(false)
```
