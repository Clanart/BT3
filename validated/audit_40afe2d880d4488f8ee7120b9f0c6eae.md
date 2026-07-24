### Title
`host_deposit_constant` Passes `deposit_value_bytes` Instead of `move_txid` to `deposit_constant`, Causing Permanent Journal Hash Mismatch That Prevents Operators from Generating Valid Bridge Circuit Proofs — (File: `bridge-circuit-host/src/structs.rs`)

### Summary

The `host_deposit_constant` function in `bridge-circuit-host/src/structs.rs` passes `deposit_value_bytes` (the EIP-1186 storage proof value, i.e. the deposit amount) as the 4th argument to `deposit_constant`, while the circuit in `circuits-lib/src/bridge_circuit/mod.rs` passes `*move_txid` (the move transaction ID extracted from the verified storage proof). Because the `deposit_constant` hash is an input to the journal hash committed by the circuit, the host-side journal hash check in `prove_bridge_circuit` will always fail with "Journal hash mismatch", permanently preventing operators from generating valid bridge circuit proofs.

### Finding Description

The `deposit_constant` function has the following signature:

```rust
pub fn deposit_constant(
    operator_xonlypk: [u8; 32],
    watchtower_challenge_connector_start_idx: u32,
    watchtower_pubkeys: &[[u8; 32]],
    move_txid: [u8; 32],   // ← 4th parameter
    round_txid: [u8; 32],
    kickoff_round_vout: u32,
    genesis_state_hash: [u8; 32],
) -> DepositConstant
``` [1](#0-0) 

**In the circuit** (`bridge_circuit` function), the 4th argument is `*move_txid`, the move transaction ID returned by `verify_storage_proofs`:

```rust
let (user_wd_outpoint, vout, move_txid) =
    verify_storage_proofs(&input.sp, light_client_circuit_output.l2_state_root);
...
let deposit_constant = deposit_constant(
    operator_xonlypk,
    input.watchtower_challenge_connector_start_idx,
    &input.all_tweaked_watchtower_pubkeys,
    *move_txid,   // ← move transaction ID
    round_txid,
    kickoff_round_vout,
    input.hcp.genesis_state_hash,
);
``` [2](#0-1) 

**In the host** (`host_deposit_constant`), the 4th argument is `deposit_value_bytes`, the big-endian encoding of the deposit amount from the EIP-1186 storage proof:

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

The host's `deposit_constant` feeds into `host_journal_hash`, which is compared against the circuit's committed journal in `prove_bridge_circuit`:

```rust
if *journal_hash.as_bytes() != succinct_receipt_journal {
    return Err(eyre!("Journal hash mismatch"));
}
``` [4](#0-3) 

Because `deposit_value_bytes` (a deposit amount) and `*move_txid` (a 32-byte transaction ID) are different values for every real deposit, the two `deposit_constant` hashes will never match, and `prove_bridge_circuit` will always return an error before the Groth16 proof is returned.

The same `move_txid` argument is used consistently in `creator.rs` and `verifier.rs`, confirming the host is the outlier: [5](#0-4) [6](#0-5) 

### Impact Explanation

`prove_bridge_circuit` is the sole code path through which an operator generates the Groth16 proof required to defend against a BitVM2 challenge. Because the journal hash check unconditionally fails, no operator can ever produce a valid proof. Any challenger who opens a challenge against an honest operator will win by default: the operator's collateral UTXO becomes spendable via the disprove path, resulting in permanent loss of operator collateral. This satisfies the "slashable exposure of operator collateral" impact criterion.

### Likelihood Explanation

The mismatch is structural and deterministic — it fires on every call to `prove_bridge_circuit` for every deposit on every network. No special attacker capability is required; any party that can send a challenge transaction (any verifier or watchtower) can trigger the slash. The only prerequisite is that a kickoff has been broadcast, which is a normal protocol step.

### Recommendation

In `host_deposit_constant`, replace `deposit_value_bytes` with the move transaction ID extracted from the storage proof, matching the circuit's computation:

```rust
// Derive move_txid from the storage proof the same way the circuit does,
// e.g. by decoding the storage slot value as a txid [u8; 32].
let move_txid: [u8; 32] = /* extract from deposit_storage_proof */;

Ok(deposit_constant(
    operator_xonlypk,
    input.watchtower_challenge_connector_start_idx,
    &input.all_tweaked_watchtower_pubkeys,
    move_txid,   // ← must match the circuit
    round_txid,
    kickoff_round_vout,
    input.hcp.genesis_state_hash,
))
```

Add a unit test that serialises a `BridgeCircuitInput`, runs both `host_deposit_constant` and the circuit's `deposit_constant` call path, and asserts they produce identical `DepositConstant` values.

### Proof of Concept

1. Operator broadcasts a kickoff transaction for a valid payout.
2. A challenger sends a challenge transaction.
3. Operator calls `prove_bridge_circuit` to generate the defending Groth16 proof.
4. Inside `prove_bridge_circuit`, `SuccinctBridgeCircuitPublicInputs::new` calls `host_deposit_constant`, which computes `deposit_constant` using `deposit_value_bytes` (e.g. `0x0000…05F5E100` for a 1 BTC deposit).
5. The circuit runs and computes `deposit_constant` using `*move_txid` (e.g. `0xABCD…`), a completely different 32-byte value.
6. The circuit commits `journal_hash(payout_tx_blockhash, latest_blockhash, challenge_sending_watchtowers, deposit_constant_from_move_txid)`.
7. The host checks `journal_hash_from_deposit_value != circuit_journal` → returns `Err("Journal hash mismatch")`.
8. The operator has no proof to submit; the challenge period expires; the challenger spends the operator's collateral output. [7](#0-6) [8](#0-7)

### Citations

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
