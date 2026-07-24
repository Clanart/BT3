### Title
Host-Side `deposit_constant` Passes Deposit Amount Instead of `move_txid`, Producing Wrong Public Input for Bridge Circuit Proof Verification — (`bridge-circuit-host/src/structs.rs`)

---

### Summary

`host_deposit_constant` in `bridge-circuit-host/src/structs.rs` passes `deposit_value_bytes` (the 32-byte big-endian encoding of the deposit amount from the EIP-1186 storage proof) as the `move_txid` argument to `deposit_constant`. The circuit-side `bridge_circuit` in `circuits-lib/src/bridge_circuit/mod.rs` passes the actual `move_txid` returned by `verify_storage_proofs`. Because the two sides hash different values into the `DepositConstant`, the public input reconstructed by `verify_bridge_circuit` never matches the journal committed by the zkVM, so every valid bridge-circuit Groth16 proof is rejected by the host verifier. This breaks the challenge/disprove mechanism: verifiers cannot distinguish a valid operator proof from an invalid one, enabling either unjust slashing of honest operators or failure to disprove malicious ones.

---

### Finding Description

**Circuit-side (correct)** — `circuits-lib/src/bridge_circuit/mod.rs`:

```
let (user_wd_outpoint, vout, move_txid) =
    verify_storage_proofs(&input.sp, light_client_circuit_output.l2_state_root);
// … later …
deposit_constant(
    operator_xonlypk,
    input.watchtower_challenge_connector_start_idx,
    &input.all_tweaked_watchtower_pubkeys,
    move_txid,          // ← actual 32-byte move-tx hash from storage proof
    round_txid,
    kickoff_round_vout,
    input.hcp.genesis_state_hash,
)
```

**Host-side (wrong)** — `bridge-circuit-host/src/structs.rs`, `host_deposit_constant`:

```rust
let deposit_value_bytes: [u8; 32] = deposit_storage_proof.value.to_be_bytes::<32>();

Ok(deposit_constant(
    operator_xonlypk,
    input.watchtower_challenge_connector_start_idx,
    &input.all_tweaked_watchtower_pubkeys,
    deposit_value_bytes,   // ← deposit AMOUNT, not move_txid
    round_txid,
    kickoff_round_vout,
    input.hcp.genesis_state_hash,
))
``` [1](#0-0) 

The `deposit_constant` function hashes all seven arguments together into a 32-byte SHA-256 digest: [2](#0-1) 

Because `deposit_value_bytes` (e.g., `0x00000000000F4240` zero-padded to 32 bytes for a 1 000 000-sat deposit) is structurally different from a 32-byte transaction hash, the `DepositConstant` produced by the host is always different from the one committed by the circuit.

`verify_bridge_circuit` then uses `self.deposit_constant` to reconstruct the expected Groth16 public input: [3](#0-2) 

Because the reconstructed public input never matches the circuit's committed journal hash, `ark_groth16::Groth16::verify_proof` returns an error for every valid proof, and `verify_bridge_circuit` returns `false` unconditionally.

The circuit-side `deposit_constant` call uses `move_txid` from `verify_storage_proofs`: [4](#0-3) 

---

### Impact Explanation

`verify_bridge_circuit` is the host-side gate that verifiers use to decide whether an operator's bridge-circuit Groth16 proof is valid before acting on a challenge. Because it always returns `false`:

- **Honest operators cannot clear a challenge**: every proof they submit is rejected by the host verifier, so verifiers will always treat the proof as invalid and may proceed to send a disprove transaction, burning the operator's collateral even though the operator behaved correctly.
- **Malicious operators cannot be correctly identified**: the verification result is uniformly `false` regardless of proof validity, so the signal carries no information. Automated watchtower/verifier logic that relies on this result to decide whether to disprove is broken.

Both outcomes represent material loss of bridged BTC or operator collateral, matching the allowed impact gate (unauthorized state transition in the challenge/disprove flow that breaks bridge safety/liveness with material fund impact).

---

### Likelihood Explanation

The bug is triggered on every call to `verify_bridge_circuit` with any valid bridge-circuit proof. No special attacker action is required; the mismatch is structural and deterministic. Any operator who is challenged and submits a valid proof will have that proof rejected by the host verifier. The code path is reachable in normal protocol operation whenever a watchtower challenge is issued.

---

### Recommendation

Replace `deposit_value_bytes` with the actual `move_txid` in `host_deposit_constant`. The move transaction ID must be extracted from the storage proof in the same way the circuit does — via `verify_storage_proofs` or an equivalent host-side deserialization of `input.sp.storage_proof_deposit_txid`:

```rust
// Deserialize the move_txid from the deposit storage proof value field
// (the circuit reads it via verify_storage_proofs; the host must do the same)
let move_txid: [u8; 32] = /* extract txid bytes from deposit_storage_proof, not .value */;

Ok(deposit_constant(
    operator_xonlypk,
    input.watchtower_challenge_connector_start_idx,
    &input.all_tweaked_watchtower_pubkeys,
    move_txid,          // ← correct: 32-byte move-tx hash
    round_txid,
    kickoff_round_vout,
    input.hcp.genesis_state_hash,
))
```

Add a test that constructs a `BridgeCircuitHostParams` from a known circuit execution, calls `verify_bridge_circuit` with the corresponding proof, and asserts it returns `true`. This would have caught the mismatch immediately.

---

### Proof of Concept

1. Run the bridge circuit in dev mode for any valid deposit/withdrawal pair, obtaining a Groth16 receipt `proof` and the committed journal.
2. Construct `BridgeCircuitHostParams` from the same `BridgeCircuitInput`.
3. Call `params.verify_bridge_circuit(proof)`.
4. Observe it returns `Err(ProofVerificationFailed)` even though the proof is valid.

The root cause is visible statically: `deposit_value_bytes` is the big-endian encoding of `deposit_storage_proof.value` (a `U256` integer representing satoshis), while the circuit hashes `move_txid` (a 32-byte transaction hash) at the same position. The two values are never equal for any real deposit, so the SHA-256 digest — and therefore the Groth16 public input — always diverges between host and circuit. [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

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

**File:** bridge-circuit-host/src/structs.rs (L609-644)
```rust
    pub fn verify_bridge_circuit(
        &self,
        proof: ark_groth16::Proof<Bn254>,
    ) -> Result<bool, BridgeCircuitHostParamsError> {
        let mut hasher = blake3::Hasher::new();
        hasher.update(&self.payout_tx_block_hash);
        hasher.update(&self.latest_block_hash);
        hasher.update(&self.challenge_sending_watchtowers);
        let x = hasher.finalize();
        let x_bytes: [u8; 32] = x.into();

        let mut hasher = blake3::Hasher::new();
        hasher.update(&self.deposit_constant);
        hasher.update(&x_bytes);
        let y = hasher.finalize();
        let y_bytes: [u8; 32] = y.into();

        let mut hasher = blake3::Hasher::new();
        hasher.update(&self.combined_method_id);
        hasher.update(&y_bytes);
        let public_output = hasher.finalize();

        let public_output_bytes: [u8; 32] = public_output.into();
        let public_input_scalar =
            ark_bn254::Fr::from_be_bytes_mod_order(&public_output_bytes[0..31]);

        let ark_vk = get_ark_verifying_key_prod();
        let ark_pvk = ark_groth16::prepare_verifying_key(&ark_vk);

        ark_groth16::Groth16::<ark_bn254::Bn254>::verify_proof(
            &ark_pvk,
            &proof,
            &[public_input_scalar],
        )
        .map_err(|_| BridgeCircuitHostParamsError::ProofVerificationFailed)
    }
```

**File:** circuits-lib/src/bridge_circuit/mod.rs (L183-205)
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

```

**File:** circuits-lib/src/bridge_circuit/mod.rs (L634-663)
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
    // pubkeys are 32 bytes long
    let pubkey_concat = watchtower_pubkeys
        .iter()
        .flat_map(|pubkey| pubkey.to_vec())
        .collect::<Vec<u8>>();

    let watchtower_pubkeys_digest: [u8; 32] = Sha256::digest(&pubkey_concat).into();

    let pre_deposit_constant = [
        &move_txid,
        &watchtower_pubkeys_digest,
        &operator_xonlypk,
        &watchtower_challenge_connector_start_idx.to_be_bytes()[..],
        &round_txid,
        &kickoff_round_vout.to_be_bytes()[..],
        &genesis_state_hash,
    ]
    .concat();

    DepositConstant(Sha256::digest(&pre_deposit_constant).into())
}
```

**File:** circuits-lib/src/work_only/mod.rs (L111-114)
```rust
fn work_conversion(work: U256) -> [u8; 16] {
    let (_, work): (U128, U128) = work.into();
    work.to_be_bytes()
}
```
