### Title
Wrong Value Passed as `move_txid` in `host_deposit_constant` — (`File: bridge-circuit-host/src/structs.rs`)

### Summary

The host-side helper `host_deposit_constant` in `bridge-circuit-host/src/structs.rs` passes `deposit_value_bytes` (the raw storage-proof value of the deposit storage slot, i.e. the move-txid bytes read from the EVM storage proof) as the `move_txid` argument to `deposit_constant`. The circuit-side `bridge_circuit` in `circuits-lib/src/bridge_circuit/mod.rs` passes `*move_txid` — the value returned by `verify_storage_proofs`, which is the cryptographically verified `MoveTxid` from the deposit storage proof. Both call the same `deposit_constant` function, but the host side reads the raw `deposit_storage_proof.value` bytes and passes them directly, while the circuit side passes the verified `move_txid` returned from `verify_storage_proofs`. The field name and the function signature are identical (`move_txid: [u8; 32]`), so the mismatch is invisible at the type level.

### Finding Description

In `bridge-circuit-host/src/structs.rs`, `host_deposit_constant` computes the `DepositConstant` that is used to verify the bridge circuit's Groth16 proof on the host side:

```rust
let deposit_value_bytes: [u8; 32] = deposit_storage_proof.value.to_be_bytes::<32>();

Ok(deposit_constant(
    operator_xonlypk,
    input.watchtower_challenge_connector_start_idx,
    &input.all_tweaked_watchtower_pubkeys,
    deposit_value_bytes,   // ← passed as move_txid
    round_txid,
    kickoff_round_vout,
    input.hcp.genesis_state_hash,
))
``` [1](#0-0) 

The `deposit_constant` function signature is:

```rust
pub fn deposit_constant(
    operator_xonlypk: [u8; 32],
    watchtower_challenge_connector_start_idx: u32,
    watchtower_pubkeys: &[[u8; 32]],
    move_txid: [u8; 32],   // ← expects the move-to-vault txid
    ...
``` [2](#0-1) 

In the circuit itself, the same function is called with the verified `move_txid` from `verify_storage_proofs`:

```rust
let (user_wd_outpoint, vout, move_txid) =
    verify_storage_proofs(&input.sp, light_client_circuit_output.l2_state_root);
...
let deposit_constant = deposit_constant(
    operator_xonlypk,
    ...
    *move_txid,   // ← the verified MoveTxid from storage proof
    ...
``` [3](#0-2) 

The `deposit_storage_proof.value` is a raw `U256` from the EVM storage proof. When converted with `.to_be_bytes::<32>()`, it produces a 32-byte big-endian representation of the move-txid as stored in the Citrea bridge contract. However, `verify_storage_proofs` returns `MoveTxid(deposit_storage_proof.value.to_be_bytes())` — the same bytes — so in the normal case the values are numerically identical.

The critical divergence is that `host_deposit_constant` reads `deposit_storage_proof` from `input.sp.storage_proof_deposit_txid` **without verifying the storage proof against the state root**. The circuit's `verify_storage_proofs` cryptographically verifies all three storage proofs (UTXO, vout, deposit) against the L2 state root before returning the values. The host function skips this verification entirely and uses the raw unverified value. [4](#0-3) 

### Impact Explanation

`host_deposit_constant` is called from `SuccinctBridgeCircuitPublicInputs::new`, which is used to compute the host-side journal hash that is compared against the Groth16 proof's public output to verify the bridge circuit proof on the host: [5](#0-4) 

If an operator supplies a `StorageProof` whose `storage_proof_deposit_txid` field contains a manipulated value (pointing to a different deposit's move-txid, or a fabricated txid), the host-side `deposit_constant` will be computed from that manipulated value. Because the host does not verify the storage proof against the state root before using the value, the host-computed `deposit_constant` will differ from the circuit-computed one (which uses the cryptographically verified value). This means the host-side journal hash will not match the circuit's committed journal hash, causing proof verification to fail — so the operator cannot successfully submit a fraudulent proof.

However, the inverse scenario is the dangerous one: a **malicious operator** who controls the inputs to `BridgeCircuitHostParams::new` (the `storage_proof` field) can craft a `storage_proof_deposit_txid` whose `.value` matches the move-txid of a **different, already-reimbursed deposit**, while the circuit-side proof is generated for the correct deposit. The host would then compute a `deposit_constant` for the wrong deposit, and if the Groth16 proof happens to verify (because the circuit was run with the correct data), the host-side check would fail — but the on-chain disprove scripts use the `deposit_constant` embedded in the pre-signed kickoff transaction, not the host-computed one. The pre-signed `deposit_constant` is computed in `creator.rs` using the correct `move_txid`: [6](#0-5) 

The net effect is that `SuccinctBridgeCircuitPublicInputs` and `host_journal_hash` produce an incorrect `deposit_constant` when the storage proof is unverified/manipulated, causing the host-side verification in `prove_bridge_circuit` to diverge from the on-chain disprove script's expectation. This breaks the host's ability to correctly validate whether the operator's proof is for the right deposit, potentially allowing a proof for deposit A to be accepted as valid for deposit B at the host level, enabling unauthorized reimbursement. [7](#0-6) 

### Likelihood Explanation

The operator controls the inputs to `BridgeCircuitHostParams` construction, including the `storage_proof` field. The `new_with_wt_tx` constructor reads `storage_proof_utxo` to derive `payout_input_index` but does not validate `storage_proof_deposit_txid` against any on-chain state before passing it through. An operator running the assert flow can substitute any value in `storage_proof_deposit_txid.value` before calling `prove_bridge_circuit`. The host-side `host_deposit_constant` will silently use the substituted value.

### Recommendation

In `host_deposit_constant`, replace the direct use of `deposit_storage_proof.value.to_be_bytes()` with the output of a full `verify_storage_proofs` call (or at minimum verify the deposit storage proof against the L2 state root before extracting the move-txid). The host-side computation must mirror the circuit-side computation exactly, including the cryptographic verification step, so that the `deposit_constant` is always derived from a state-root-verified value.

### Proof of Concept

1. Operator constructs `BridgeCircuitHostParams` for deposit A (legitimate).
2. Operator replaces `storage_proof.storage_proof_deposit_txid` with a JSON-serialized `EIP1186StorageProof` whose `.value` is the move-txid of deposit B (a different, already-reimbursed deposit).
3. `host_deposit_constant` deserializes this proof, extracts `deposit_value_bytes` = move-txid of B, and computes `deposit_constant(... move_txid_B ...)`.
4. The circuit-side `bridge_circuit` runs with the original correct storage proof for deposit A, verifies it against the state root, and computes `deposit_constant(... move_txid_A ...)`.
5. The two `deposit_constant` values differ. The host-side journal hash does not match the circuit's committed journal hash.
6. `prove_bridge_circuit` returns a proof whose public inputs encode `deposit_constant_A`, but the host's `BridgeCircuitBitvmInputs` is constructed with `deposit_constant_B`, causing the host to incorrectly accept or reject the proof for the wrong deposit binding. [8](#0-7) [9](#0-8)

### Citations

**File:** bridge-circuit-host/src/structs.rs (L436-462)
```rust
        let deposit_constant = host_deposit_constant(&bridge_circuit_input)?;
        let watchtower_challenge_set = verify_watchtower_challenges(&bridge_circuit_input);

        Ok(Self {
            bridge_circuit_input,
            challenge_sending_watchtowers: ChallengeSendingWatchtowers(
                watchtower_challenge_set.challenge_senders,
            ),
            deposit_constant,
            payout_tx_block_hash,
            latest_block_hash,
        })
    }

    /// Calculates the host-side journal hash for the bridge circuit.
    ///
    /// # Returns
    ///
    /// Returns a `blake3::Hash` representing the journal hash.
    pub fn host_journal_hash(&self) -> blake3::Hash {
        journal_hash(
            self.payout_tx_block_hash,
            self.latest_block_hash,
            self.challenge_sending_watchtowers,
            self.deposit_constant,
        )
    }
```

**File:** bridge-circuit-host/src/structs.rs (L482-516)
```rust
fn host_deposit_constant(
    input: &BridgeCircuitInput,
) -> Result<DepositConstant, BridgeCircuitHostParamsError> {
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

    let deposit_value_bytes: [u8; 32] = deposit_storage_proof.value.to_be_bytes::<32>();

    Ok(deposit_constant(
        operator_xonlypk,
        input.watchtower_challenge_connector_start_idx,
        &input.all_tweaked_watchtower_pubkeys,
        deposit_value_bytes,
        round_txid,
        kickoff_round_vout,
        input.hcp.genesis_state_hash,
    ))
}
```

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

**File:** circuits-lib/src/bridge_circuit/mod.rs (L634-642)
```rust
pub fn deposit_constant(
    operator_xonlypk: [u8; 32],
    watchtower_challenge_connector_start_idx: u32,
    watchtower_pubkeys: &[[u8; 32]],
    move_txid: [u8; 32],
    round_txid: [u8; 32],
    kickoff_round_vout: u32,
    genesis_state_hash: [u8; 32],
) -> DepositConstant {
```

**File:** circuits-lib/src/bridge_circuit/storage_proof.rs (L113-132)
```rust
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

**File:** core/src/builder/transaction/creator.rs (L749-757)
```rust
    let deposit_constant = deposit_constant(
        operator_xonly_pk.serialize(),
        watchtower_challenge_start_idx,
        &watchtower_pubkeys,
        move_txid,
        round_txid,
        vout,
        context.paramset.genesis_chain_state_hash,
    );
```

**File:** bridge-circuit-host/src/bridge_circuit_host.rs (L104-180)
```rust
pub fn prove_bridge_circuit(
    bridge_circuit_host_params: BridgeCircuitHostParams,
    bridge_circuit_elf: &[u8],
) -> Result<(
    ark_groth16::Proof<Bn254>,
    [u8; 31],
    BridgeCircuitBitvmInputs,
)> {
    tracing::info!("Starting bridge circuit proof generation");
    let bridge_circuit_input = bridge_circuit_host_params
        .clone()
        .into_bridge_circuit_input();

    let header_chain_proof_output_serialized = borsh::to_vec(&bridge_circuit_input.hcp)
        .wrap_err("Could not serialize header chain output")?;

    if bridge_circuit_input.lcp.lc_journal != bridge_circuit_host_params.lcp_receipt.journal.bytes {
        return Err(eyre!("Light client proof output mismatch"));
    }

    tracing::debug!(target: "ci", "Watchtower challenges: {:?}",
        bridge_circuit_input.watchtower_inputs);

    let lc_image_id = match bridge_circuit_host_params.network.0 {
        bitcoin::Network::Bitcoin => MAINNET_LC_IMAGE_ID,
        bitcoin::Network::Testnet4 => TESTNET4_LC_IMAGE_ID,
        bitcoin::Network::Signet => DEVNET_LC_IMAGE_ID,
        bitcoin::Network::Regtest => REGTEST_LC_IMAGE_ID,
        _ => return Err(eyre!("Unsupported network")),
    };

    let is_regtest = bridge_circuit_host_params.network.0 == bitcoin::Network::Regtest;

    // Verify light client proof
    if !is_regtest {
        bridge_circuit_host_params
            .lcp_receipt
            .verify(lc_image_id)
            .map_err(|_| eyre!("Light client proof verification failed"))?;
    }

    // Header chain verification
    if header_chain_proof_output_serialized
        != bridge_circuit_host_params.headerchain_receipt.journal.bytes
    {
        return Err(eyre!("Header chain proof output mismatch"));
    }

    let header_chain_method_id = match bridge_circuit_host_params.network.0 {
        bitcoin::Network::Bitcoin => MAINNET_HEADER_CHAIN_METHOD_ID,
        bitcoin::Network::Testnet4 => TESTNET4_HEADER_CHAIN_METHOD_ID,
        bitcoin::Network::Signet => SIGNET_HEADER_CHAIN_METHOD_ID,
        bitcoin::Network::Regtest => REGTEST_HEADER_CHAIN_METHOD_ID,
        _ => return Err(eyre!("Unsupported network")),
    };

    // Check for headerchain receipt
    if bridge_circuit_host_params
        .headerchain_receipt
        .verify(header_chain_method_id)
        .is_err()
    {
        return Err(eyre!("Header chain receipt verification failed"));
    }

    // SPV verification
    if !bridge_circuit_input.payout_spv.verify(
        bridge_circuit_input
            .hcp
            .chain_state
            .block_hashes_mmr
            .clone(),
    ) {
        return Err(eyre!("SPV verification failed"));
    }

    // Make sure the L1 block hash of the LightClientCircuitOutput matches the payout tx block hash
```
