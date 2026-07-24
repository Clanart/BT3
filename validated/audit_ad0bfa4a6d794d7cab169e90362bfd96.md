### Title
`host_deposit_constant` passes `deposit_value_bytes` instead of `move_txid` to `deposit_constant`, causing permanent journal-hash mismatch that blocks all operator reimbursements — (`bridge-circuit-host/src/structs.rs`)

---

### Summary

`host_deposit_constant` in `bridge-circuit-host/src/structs.rs` calls the shared `deposit_constant` function with `deposit_value_bytes` (the EIP-1186 storage-proof value) in the slot that the function signature, the ZK circuit, the transaction builder, and the verifier all expect to receive `move_txid`. Because the circuit always uses the correct `move_txid`, the host-side journal hash computed in `prove_bridge_circuit` will never match the circuit's committed journal hash, causing `prove_bridge_circuit` to unconditionally return `Err("Journal hash mismatch")`. An operator who has fronted BTC for a withdrawal can never generate a valid bridge-circuit proof, cannot defend against a challenge, and permanently loses both the fronted BTC and their collateral.

---

### Finding Description

**`deposit_constant` function signature** (`circuits-lib/src/bridge_circuit/mod.rs`):

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
``` [1](#0-0) 

**ZK circuit call — correct** (`circuits-lib/src/bridge_circuit/mod.rs`):

```rust
let deposit_constant = deposit_constant(
    operator_xonlypk,
    input.watchtower_challenge_connector_start_idx,
    &input.all_tweaked_watchtower_pubkeys,
    *move_txid,          // ← move_txid from verify_storage_proofs
    round_txid,
    kickoff_round_vout,
    input.hcp.genesis_state_hash,
);
``` [2](#0-1) 

**Host-side call — wrong** (`bridge-circuit-host/src/structs.rs`):

```rust
let deposit_value_bytes: [u8; 32] = deposit_storage_proof.value.to_be_bytes::<32>();

Ok(deposit_constant(
    operator_xonlypk,
    input.watchtower_challenge_connector_start_idx,
    &input.all_tweaked_watchtower_pubkeys,
    deposit_value_bytes,   // ← deposit amount, NOT move_txid
    round_txid,
    kickoff_round_vout,
    input.hcp.genesis_state_hash,
))
``` [3](#0-2) 

`host_deposit_constant` is called by `SuccinctBridgeCircuitPublicInputs::new`, which is called inside `prove_bridge_circuit` to compute the expected `journal_hash`:

```rust
let public_inputs: SuccinctBridgeCircuitPublicInputs =
    SuccinctBridgeCircuitPublicInputs::new(bridge_circuit_input.clone())?;
let journal_hash = public_inputs.host_journal_hash();
// ...
if *journal_hash.as_bytes() != succinct_receipt_journal {
    return Err(eyre!("Journal hash mismatch"));
}
``` [4](#0-3) 

Because `deposit_value_bytes` (a 32-byte big-endian encoding of the deposit's EVM storage value) is virtually never equal to `move_txid` (a 32-byte Bitcoin transaction ID), the host-computed `deposit_constant` will always differ from the circuit-computed one. The `journal_hash` check therefore always fails, and `prove_bridge_circuit` always returns an error.

For comparison, every other call site passes `move_txid` correctly:

- Transaction builder: [5](#0-4) 
- Verifier disprove path: [6](#0-5) 

---

### Impact Explanation

An operator fronts BTC to pay a user's withdrawal. To be reimbursed, the operator must call `prove_bridge_circuit` to generate a valid Groth16 proof and submit it on-chain. Because `prove_bridge_circuit` unconditionally returns `Err("Journal hash mismatch")`, the operator:

1. Cannot generate a valid bridge-circuit proof.
2. Cannot respond to a BitVM challenge with a valid proof.
3. Will have their collateral slashed and will not recover the fronted BTC.

This is a **permanent, total loss** of the operator's fronted BTC and collateral for every withdrawal they process. The bridge's reimbursement mechanism is completely broken.

---

### Likelihood Explanation

The bug is triggered every time an operator attempts to prove a withdrawal — i.e., on every normal bridge operation. No special attacker input is required; the mismatch is structural and deterministic. Any operator running the production code will hit this on their first withdrawal attempt.

---

### Recommendation

Replace `deposit_value_bytes` with the actual `move_txid` in `host_deposit_constant`. The `move_txid` is already available in `BridgeCircuitInput` via the storage proof verification path. Specifically, deserialize the `storage_proof_deposit_txid` field to obtain the move txid bytes (as the circuit does via `verify_storage_proofs`), or expose `move_txid` directly in `BridgeCircuitInput` and pass it through. The fix must make the host-side `deposit_constant` computation identical to the circuit-side computation.

---

### Proof of Concept

1. `deposit_constant` function signature names its 4th parameter `move_txid`. [7](#0-6) 
2. The circuit passes `*move_txid` (from `verify_storage_proofs`). [8](#0-7) 
3. `host_deposit_constant` passes `deposit_value_bytes` instead. [9](#0-8) 
4. `prove_bridge_circuit` computes `journal_hash` from the host's (wrong) `deposit_constant` and compares it to the circuit's journal output. [4](#0-3) 
5. Since `deposit_value_bytes ≠ move_txid` in all real cases, the comparison at line 239 always fails, and `prove_bridge_circuit` always returns `Err("Journal hash mismatch")`.

### Citations

**File:** circuits-lib/src/bridge_circuit/mod.rs (L221-229)
```rust
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

**File:** bridge-circuit-host/src/structs.rs (L505-515)
```rust
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
```

**File:** bridge-circuit-host/src/bridge_circuit_host.rs (L205-241)
```rust
    let public_inputs: SuccinctBridgeCircuitPublicInputs =
        SuccinctBridgeCircuitPublicInputs::new(bridge_circuit_input.clone())?;

    let journal_hash = public_inputs.host_journal_hash();

    let mut binding = ExecutorEnv::builder();
    let env = binding
        .write_slice(
            &borsh::to_vec(&bridge_circuit_input)
                .wrap_err("Failed to serialize bridge circuit input")?,
        )
        .add_assumption(bridge_circuit_host_params.headerchain_receipt)
        .add_assumption(bridge_circuit_host_params.lcp_receipt)
        .build()
        .map_err(|e| eyre!("Failed to build execution environment: {}", e))?;

    let prover = default_prover();

    tracing::info!("Checks complete, proving bridge circuit to generate STARK proof");

    let succinct_receipt = prover
        .prove_with_opts(env, bridge_circuit_elf, &ProverOpts::succinct())
        .map_err(|e| eyre!("Failed to generate bridge circuit proof: {}", e))?
        .receipt;

    tracing::info!("Bridge circuit proof (STARK) generated");

    let succinct_receipt_journal: [u8; 32] = succinct_receipt
        .clone()
        .journal
        .bytes
        .try_into()
        .map_err(|_| eyre!("Failed to convert journal bytes to array"))?;

    if *journal_hash.as_bytes() != succinct_receipt_journal {
        return Err(eyre!("Journal hash mismatch"));
    }
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
