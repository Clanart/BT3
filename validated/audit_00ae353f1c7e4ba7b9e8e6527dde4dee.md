### Title
`host_deposit_constant` passes `deposit_value_bytes` instead of `move_txid` to `deposit_constant`, causing journal hash mismatch that permanently blocks bridge circuit proof generation — (`bridge-circuit-host/src/structs.rs`)

---

### Summary

The host-side `host_deposit_constant` function passes `deposit_value_bytes` (the raw EIP-1186 storage-proof value field) as the `move_txid` positional argument to `deposit_constant`, while the ZK guest circuit correctly passes the actual `move_txid` returned by `verify_storage_proofs`. Because the two callers feed different 32-byte values into the same hash, the host's pre-computed `journal_hash` never matches the journal committed by the guest, causing `prove_bridge_circuit` to always abort with "Journal hash mismatch". An operator who is challenged can never produce a valid bridge-circuit proof, so their collateral is slashable by any challenger.

---

### Finding Description

**Guest circuit** (`circuits-lib/src/bridge_circuit/mod.rs`, lines 183–228):

```rust
let (user_wd_outpoint, vout, move_txid) =
    verify_storage_proofs(&input.sp, light_client_circuit_output.l2_state_root);
// ...
let deposit_constant = deposit_constant(
    operator_xonlypk,
    input.watchtower_challenge_connector_start_idx,
    &input.all_tweaked_watchtower_pubkeys,
    *move_txid,          // ← correct: Bitcoin txid of the move-to-vault tx
    round_txid,
    kickoff_round_vout,
    input.hcp.genesis_state_hash,
);
```

**Host-side pre-check** (`bridge-circuit-host/src/structs.rs`, lines 488–515):

```rust
let deposit_storage_proof: EIP1186StorageProof =
    serde_json::from_str(&input.sp.storage_proof_deposit_txid)...;
// ...
let deposit_value_bytes: [u8; 32] = deposit_storage_proof.value.to_be_bytes::<32>();

Ok(deposit_constant(
    operator_xonlypk,
    input.watchtower_challenge_connector_start_idx,
    &input.all_tweaked_watchtower_pubkeys,
    deposit_value_bytes,   // ← WRONG: raw storage-slot value, not move_txid
    round_txid,
    kickoff_round_vout,
    input.hcp.genesis_state_hash,
))
```

The `deposit_constant` function's own signature names the 4th parameter `move_txid: [u8; 32]` and hashes it as the first field of the pre-image:

```rust
let pre_deposit_constant = [
    &move_txid,          // ← position 0 in the hash
    &watchtower_pubkeys_digest,
    &operator_xonlypk,
    ...
].concat();
```

`move_txid` is a 32-byte Bitcoin transaction ID; `deposit_value_bytes` is a big-endian encoding of the EIP-1186 storage slot value (a U256 deposit amount). They are semantically and numerically different for every real deposit.

`prove_bridge_circuit` then computes `journal_hash` from the host's (wrong) `deposit_constant` and compares it against the journal actually committed by the guest:

```rust
if *journal_hash.as_bytes() != succinct_receipt_journal {
    return Err(eyre!("Journal hash mismatch"));
}
```

Because the two `deposit_constant` values differ, this check always fails and the function returns an error before returning the Groth16 proof.

---

### Impact Explanation

The bridge circuit proof is the operator's only mechanism to defend against a BitVM challenge. When a challenger spends the challenge connector, the operator must call `prove_bridge_circuit` and post the resulting Groth16 proof via Assert transactions. Because `prove_bridge_circuit` always aborts at the journal-hash check, the operator can never produce a valid proof. The challenger wins by default, and the operator's full collateral (`operator_challenge_amount`, configured at 200 000 000 sat on regtest, larger on mainnet) is burned. Every honest operator is permanently exposed to this slashing path the moment any challenger initiates a BitVM challenge.

---

### Likelihood Explanation

Any party that can afford the small cost of spending a watchtower-challenge connector can trigger the challenge flow. The operator's inability to respond is deterministic — it does not depend on timing, network conditions, or any probabilistic factor. The bug fires on the very first call to `prove_bridge_circuit` for any real deposit, making exploitation trivially reliable.

---

### Recommendation

In `host_deposit_constant`, replace `deposit_value_bytes` with the actual `move_txid` extracted from the deposit storage proof, mirroring what `verify_storage_proofs` returns in the guest. Concretely, deserialize the storage proof for the deposit txid slot and use its 32-byte key/value as the move transaction ID, exactly as the guest circuit does. Add a unit test that runs both the guest circuit and `host_deposit_constant` on the same `BridgeCircuitInput` and asserts the two `deposit_constant` outputs are equal.

---

### Proof of Concept

1. Complete a deposit so a `move_txid` exists on-chain and in the Citrea contract.
2. Have the operator broadcast a kickoff transaction.
3. Have any party spend the challenge connector (initiating a BitVM challenge).
4. The operator calls `prove_bridge_circuit` with the correct `BridgeCircuitInput`.
5. The guest circuit executes successfully and commits `journal_hash_guest = blake3(deposit_constant(move_txid, ...), ...)`.
6. The host computes `journal_hash_host = blake3(deposit_constant(deposit_value_bytes, ...), ...)`.
7. Because `move_txid ≠ deposit_value_bytes`, `journal_hash_guest ≠ journal_hash_host`.
8. `prove_bridge_circuit` returns `Err("Journal hash mismatch")`.
9. The operator cannot post Assert transactions; the challenge timeout elapses; the challenger burns the operator's collateral. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** bridge-circuit-host/src/structs.rs (L488-515)
```rust
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
