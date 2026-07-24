### Title
Wrong Variable Passed to `deposit_constant` in `host_deposit_constant` — Bridge Circuit Proof Permanently Fails — (`File: bridge-circuit-host/src/structs.rs`)

### Summary

`host_deposit_constant` in `bridge-circuit-host/src/structs.rs` passes `deposit_value_bytes` (the EVM storage-proof deposit amount) as the `move_txid` argument to `deposit_constant`. The circuit inside the zkVM passes the actual `move_txid`. The two sides therefore compute different `deposit_constant` values, causing every journal-hash check to fail. An operator can never successfully generate a valid bridge-circuit proof, cannot disprove a watchtower challenge, and has their collateral permanently at risk of slashing.

---

### Finding Description

`deposit_constant` (defined in `circuits-lib/src/bridge_circuit/mod.rs`) has the following signature:

```rust
pub fn deposit_constant(
    operator_xonlypk: [u8; 32],
    watchtower_challenge_connector_start_idx: u32,
    watchtower_pubkeys: &[[u8; 32]],
    move_txid: [u8; 32],          // ← 4th parameter
    round_txid: [u8; 32],
    kickoff_round_vout: u32,
    genesis_state_hash: [u8; 32],
) -> DepositConstant
```

**Circuit side** (`circuits-lib/src/bridge_circuit/mod.rs`, lines 221-229) — correct:

```rust
let deposit_constant = deposit_constant(
    operator_xonlypk,
    input.watchtower_challenge_connector_start_idx,
    &input.all_tweaked_watchtower_pubkeys,
    *move_txid,          // ← Bitcoin move-to-vault txid
    round_txid,
    kickoff_round_vout,
    input.hcp.genesis_state_hash,
);
```

**Host side** (`bridge-circuit-host/src/structs.rs`, lines 505-515) — **wrong**:

```rust
let deposit_value_bytes: [u8; 32] = deposit_storage_proof.value.to_be_bytes::<32>();

Ok(deposit_constant(
    operator_xonlypk,
    input.watchtower_challenge_connector_start_idx,
    &input.all_tweaked_watchtower_pubkeys,
    deposit_value_bytes,   // ← BUG: deposit amount, not move_txid
    round_txid,
    kickoff_round_vout,
    input.hcp.genesis_state_hash,
))
```

`deposit_value_bytes` is the big-endian U256 encoding of the deposit amount read from the EVM storage proof — a completely different 32-byte value from the Bitcoin `move_txid`.

`host_deposit_constant` is called unconditionally in `SuccinctBridgeCircuitPublicInputs::new` (line 436), which populates the `deposit_constant` field used in `host_journal_hash()` and then in `BridgeCircuitBitvmInputs.deposit_constant` (line 293). [1](#0-0) [2](#0-1) [3](#0-2) 

---

### Impact Explanation

The `deposit_constant` is a binding commitment that ties a specific deposit (move-txid, operator key, watchtower set, round, genesis hash) to the bridge-circuit proof. It is embedded in the BitVM kickoff script during the deposit phase via `creator.rs` and `verifier.rs` — both of which correctly pass `move_txid`. [4](#0-3) [5](#0-4) 

Because the host computes a different `deposit_constant` than the circuit commits in its journal, two things break simultaneously:

1. **Journal-hash mismatch**: `host_journal_hash()` hashes the wrong `deposit_constant`, so the host's expected journal hash never matches the proof's actual journal hash. Every bridge-circuit proof the operator generates is rejected.

2. **BitVM public-input mismatch**: `BridgeCircuitBitvmInputs.deposit_constant` is wrong, so `verify_bridge_circuit` computes the wrong Groth16 public input scalar and the Groth16 proof verification fails. [6](#0-5) [7](#0-6) [8](#0-7) 

Consequence: the operator can never successfully complete the assert/disprove flow. Any watchtower challenge goes unanswered, the operator's collateral is slashable, and the bridge's payout/reimbursement mechanism is permanently broken for every deposit.

---

### Likelihood Explanation

This code path is exercised every time an operator must respond to a challenge (the core safety mechanism of the bridge). There is no conditional guard or fallback. The bug is deterministic — it fires on every invocation of `SuccinctBridgeCircuitPublicInputs::new` with any real deposit. [9](#0-8) 

---

### Recommendation

Replace `deposit_value_bytes` with the actual `move_txid` in `host_deposit_constant`. The move txid must be extracted from the payout transaction's input (the withdrawal outpoint txid), mirroring how the circuit obtains it via `verify_storage_proofs`:

```rust
// Correct fix: derive move_txid from the storage proof (as the circuit does)
let (user_wd_outpoint, _vout, move_txid) =
    verify_storage_proofs_host(&input.sp, ...)?;

Ok(deposit_constant(
    operator_xonlypk,
    input.watchtower_challenge_connector_start_idx,
    &input.all_tweaked_watchtower_pubkeys,
    *move_txid,          // ← correct
    round_txid,
    kickoff_round_vout,
    input.hcp.genesis_state_hash,
))
```

The `deposit_value_bytes` variable is unused after the fix and should be removed.

---

### Proof of Concept

1. Operator initiates a kickoff for deposit `D` with `move_txid = T`.
2. The BitVM kickoff script encodes `deposit_constant(operator_pk, ..., T, round_txid, vout, genesis_hash)`.
3. A watchtower challenges the operator.
4. The operator calls `SuccinctBridgeCircuitPublicInputs::new(bridge_circuit_input)`.
5. `host_deposit_constant` computes `deposit_constant(operator_pk, ..., deposit_value_bytes, round_txid, vout, genesis_hash)` where `deposit_value_bytes ≠ T`.
6. The circuit inside the zkVM computes `deposit_constant(operator_pk, ..., T, round_txid, vout, genesis_hash)` and commits it in the journal.
7. The host's `host_journal_hash()` uses the wrong `deposit_constant`, so it does not match the proof's journal hash — proof is rejected.
8. The operator cannot submit a valid disprove transaction.
9. The challenge timeout expires; the operator's collateral is slashed and the bridge payout is lost. [10](#0-9) [11](#0-10)

### Citations

**File:** bridge-circuit-host/src/structs.rs (L421-448)
```rust
    pub fn new(
        bridge_circuit_input: BridgeCircuitInput,
    ) -> Result<Self, BridgeCircuitHostParamsError> {
        let latest_block_hash: LatestBlockhash =
            bridge_circuit_input.hcp.chain_state.best_block_hash[12..32]
                .try_into()
                .map_err(|_| BridgeCircuitHostParamsError::InvalidKickoffTx)?;

        let payout_tx_block_hash: PayoutTxBlockhash = bridge_circuit_input
            .payout_spv
            .block_header
            .compute_block_hash()[12..32]
            .try_into()
            .map_err(|_| BridgeCircuitHostParamsError::InvalidKickoffTx)?;

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
```

**File:** bridge-circuit-host/src/structs.rs (L455-462)
```rust
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

**File:** bridge-circuit-host/src/structs.rs (L569-588)
```rust
    pub fn calculate_groth16_public_input(&self) -> blake3::Hash {
        let concatenated_data = [
            self.payout_tx_block_hash,
            self.latest_block_hash,
            self.challenge_sending_watchtowers,
        ]
        .concat();
        let x = blake3::hash(&concatenated_data);
        let hash_bytes = x.as_bytes();

        let concat_journal = [self.deposit_constant, *hash_bytes].concat();

        let journal_hash = blake3::hash(&concat_journal);

        let hash_bytes = journal_hash.as_bytes();

        let concat_input = [self.combined_method_id, *hash_bytes].concat();

        blake3::hash(&concat_input)
    }
```

**File:** circuits-lib/src/bridge_circuit/mod.rs (L183-229)
```rust
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

**File:** core/src/verifier.rs (L2478-2486)
```rust
        let deposit_constant = deposit_constant(
            kickoff_data.operator_xonly_pk.serialize(),
            watchtower_challenge_start_idx,
            &watchtower_pubkeys,
            move_txid,
            round_txid,
            vout,
            self.config.protocol_paramset.genesis_chain_state_hash,
        );
```

**File:** bridge-circuit-host/src/bridge_circuit_host.rs (L286-296)
```rust
    Ok((
        ark_groth16_proof,
        g16_output,
        BridgeCircuitBitvmInputs {
            payout_tx_block_hash: public_inputs.payout_tx_block_hash.0,
            latest_block_hash: public_inputs.latest_block_hash.0,
            challenge_sending_watchtowers: public_inputs.challenge_sending_watchtowers.0,
            deposit_constant: public_inputs.deposit_constant.0,
            combined_method_id: combined_method_id_constant,
        },
    ))
```
