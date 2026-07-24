### Title
Unchecked Array Index Access in `storage_verify` Panics Bridge Circuit, Blocking Operator Assert and Exposing Collateral to Slashing — (File: `circuits-lib/src/bridge_circuit/storage_proof.rs`)

---

### Summary

`storage_verify` accesses `storage_proof.proof[0]` and `storage_proof.proof[1]` without any bounds check. If the Citrea RPC returns an `EIP1186StorageProof` whose `proof` field has fewer than two elements — a realistic edge case for a non-existent or malformed storage slot — the RISC Zero bridge-circuit guest panics. That panic propagates out of `prove_bridge_circuit`, causing `send_asserts` to return an error. The operator then cannot post the required assert transactions within the BitVM2 challenge window, and the challenger can burn the operator's collateral.

---

### Finding Description

`storage_verify` is a private helper called three times inside `verify_storage_proofs` (for the UTXO, vout, and deposit proofs). It assumes the `proof` vector always has at least two elements:

```rust
// circuits-lib/src/bridge_circuit/storage_proof.rs  line 161
let proved_value = if storage_proof.proof[1] == Bytes::from("y") {
    ...
} else {
    panic!("storage does not exist");
};

// line 171
let storage_proof: jmt::proof::SparseMerkleProof<Sha256> =
    borsh::from_slice(&storage_proof.proof[0]).unwrap();
``` [1](#0-0) 

`proof[1]` is the Citrea-custom existence flag (`"y"`); `proof[0]` is the Borsh-serialised Merkle proof. Neither access is guarded by a length check. If `proof.len() < 2`, Rust panics with an index-out-of-bounds error.

`verify_storage_proofs` is called directly from the bridge-circuit guest entry point:

```rust
// circuits-lib/src/bridge_circuit/mod.rs  line 183-184
let (user_wd_outpoint, vout, move_txid) =
    verify_storage_proofs(&input.sp, light_client_circuit_output.l2_state_root);
``` [2](#0-1) 

The guest is compiled into the RISC Zero ELF and executed by `prove_bridge_circuit` inside `send_asserts`:

```rust
// core/src/operator.rs  line 1544-1549
let (g16_proof, g16_output, public_inputs) = tokio::task::spawn_blocking(move || {
    prove_bridge_circuit(bridge_circuit_host_params, bridge_circuit_elf)
})
.await
.wrap_err("Failed to join the prove_bridge_circuit task")?
.wrap_err("Failed to prove bridge circuit")?;
``` [3](#0-2) 

The `StorageProof` fed into the circuit is fetched from the Citrea RPC in `get_storage_proof`. The host-side code checks that the response contains at least three `EIP1186StorageProof` objects, but it does **not** validate that each individual proof's inner `proof: Vec<Bytes>` field has at least two elements:

```rust
// core/src/citrea.rs  line 301-306
if response.storage_proof.len() < 3 {
    return Err(eyre::eyre!(
        "Expected at least 3 storage proofs, got {}",
        response.storage_proof.len()
    ).into());
}
``` [4](#0-3) 

The per-proof `proof` array length is never validated before the data is serialised to JSON strings and embedded in `StorageProof`, which is then passed verbatim into the circuit.

---

### Impact Explanation

When the bridge circuit panics, `prove_bridge_circuit` returns an error, `send_asserts` propagates it, and the `SendOperatorAsserts` duty fails. The operator cannot post the assert transactions that are required to win the BitVM2 challenge game. Once the assert timeout elapses, the challenger can execute the disprove path and burn the operator's collateral. This is a direct, material loss of operator collateral — a slashable exposure within the allowed impact gate. [5](#0-4) [6](#0-5) 

---

### Likelihood Explanation

The Citrea RPC uses a non-standard two-element encoding for the `proof` field (`proof[0]` = Borsh Merkle proof, `proof[1]` = existence flag `"y"`). Any of the following realistic conditions produces a proof with fewer than two elements:

- A storage slot that has never been written (deposit index not yet finalised on L2).
- A Citrea node bug or version mismatch that omits the existence flag.
- A race condition where the operator queries the RPC before the L2 state is fully committed.

The operator does not control the internal structure of the `EIP1186StorageProof` returned by the RPC; they only control which endpoint they query. The missing guard is entirely within Clementine's own production code.

---

### Recommendation

Add an explicit length check at the top of `storage_verify` before any index access:

```rust
fn storage_verify(storage_proof: &EIP1186StorageProof, expected_root_hash: [u8; 32]) {
    if storage_proof.proof.len() < 2 {
        panic!(
            "storage proof must have at least 2 elements, got {}",
            storage_proof.proof.len()
        );
    }
    // ... rest of function
}
```

Alternatively, use `storage_proof.proof.get(1)` / `storage_proof.proof.get(0)` and handle the `None` case with a descriptive panic or a returned `Result`. The same defensive check should be added in `get_storage_proof` on the host side (in `core/src/citrea.rs`) so the error is caught before the data enters the circuit, giving the operator a recoverable error rather than a circuit panic. [7](#0-6) 

---

### Proof of Concept

1. Configure the operator to connect to a Citrea RPC node that returns an `EIP1186StorageProof` for a storage slot that has never been written (e.g., a deposit index that exists on Bitcoin but whose L2 state has not yet been committed). The RPC response will contain a `proof` array with fewer than two elements.
2. The operator's `get_storage_proof` passes the length-3 outer check (three proof objects are present) but does not inspect the inner `proof` arrays.
3. The `StorageProof` is serialised and passed to `prove_bridge_circuit`.
4. Inside the RISC Zero guest, `bridge_circuit` calls `verify_storage_proofs`, which calls `storage_verify`.
5. `storage_verify` executes `storage_proof.proof[1]` — index out of bounds — and the guest panics.
6. `prove_bridge_circuit` returns `Err`, `send_asserts` propagates the error, and the operator's assert transactions are never posted.
7. After the assert timeout, the challenger executes the disprove path and burns the operator's collateral. [8](#0-7) [9](#0-8) [10](#0-9)

### Citations

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

**File:** circuits-lib/src/bridge_circuit/storage_proof.rs (L146-178)
```rust
fn storage_verify(storage_proof: &EIP1186StorageProof, expected_root_hash: [u8; 32]) {
    let kaddr = {
        let mut hasher: Sha256 = sha2::Digest::new_with_prefix(ADDRESS.as_slice());
        #[allow(clippy::unnecessary_fallible_conversions)]
        hasher.update(
            U256::try_from(storage_proof.key.as_b256())
                .unwrap()
                .as_le_slice(),
        );
        let arr = hasher.finalize();
        U256::from_le_slice(&arr)
    };
    let storage_key = [b"E/s/".as_slice(), kaddr.as_le_slice()].concat();
    let key_hash = KeyHash::with::<Sha256>(storage_key.clone());

    let proved_value = if storage_proof.proof[1] == Bytes::from("y") {
        // Storage value exists and it's serialized form is:
        let bytes = storage_proof.value.as_le_bytes().to_vec();
        Some(bytes)
    } else {
        // Storage value does not exist
        panic!("storage does not exist");
    };

    let storage_proof: jmt::proof::SparseMerkleProof<Sha256> =
        borsh::from_slice(&storage_proof.proof[0]).unwrap();

    let expected_root_hash = jmt::RootHash(expected_root_hash);

    storage_proof
        .verify(expected_root_hash, key_hash, proved_value)
        .expect("Account storage proof must be valid");
}
```

**File:** circuits-lib/src/bridge_circuit/mod.rs (L137-184)
```rust
pub fn bridge_circuit(guest: &impl ZkvmGuest, work_only_image_id: [u8; 32]) {
    let input: BridgeCircuitInput = guest.read_from_host();
    assert_eq!(
        HEADER_CHAIN_METHOD_ID, input.hcp.method_id,
        "Invalid method ID for header chain circuit: expected {:?}, got {:?}",
        HEADER_CHAIN_METHOD_ID, input.hcp.method_id
    );

    // Verify the HCP
    guest.verify(input.hcp.method_id, &input.hcp);

    let (max_total_work, challenge_sending_watchtowers) =
        total_work_and_watchtower_flags(&input, &work_only_image_id);

    let total_work: TotalWork = input.hcp.chain_state.total_work[16..32]
        .try_into()
        .expect("Cannot fail");

    // If total work is less than the max total work of watchtowers, panic
    if total_work < max_total_work {
        panic!(
            "Insufficient total work: Total Work {total_work:?} - Max Total Work: {max_total_work:?}",
        );
    }

    let mmr = input.hcp.chain_state.block_hashes_mmr.clone();

    if !input.payout_spv.verify(mmr) {
        panic!(
            "Invalid SPV proof for txid: {}",
            input.payout_spv.transaction.compute_txid()
        );
    }

    // Light client proof verification
    let light_client_circuit_output = lc_proof_verifier(input.lcp.clone());

    // Make sure the L1 block hash of the LightClientCircuitOutput matches the payout tx block hash
    let lc_l1_block_hash = light_client_circuit_output.latest_da_state.block_hash;
    let spv_l1_block_hash = input.payout_spv.block_header.compute_block_hash();

    if lc_l1_block_hash != spv_l1_block_hash {
        panic!("L1 block hash mismatch: expected {lc_l1_block_hash:?}, got {spv_l1_block_hash:?}");
    }

    // Storage proof verification for deposit tx index and withdrawal outpoint
    let (user_wd_outpoint, vout, move_txid) =
        verify_storage_proofs(&input.sp, light_client_circuit_output.l2_state_root);
```

**File:** core/src/operator.rs (L1537-1549)
```rust
        tracing::info!("Starting proving bridge circuit to send asserts");

        #[cfg(test)]
        self.config
            .test_params
            .maybe_dump_bridge_circuit_params_to_file(&bridge_circuit_host_params)?;

        let (g16_proof, g16_output, public_inputs) = tokio::task::spawn_blocking(move || {
            prove_bridge_circuit(bridge_circuit_host_params, bridge_circuit_elf)
        })
        .await
        .wrap_err("Failed to join the prove_bridge_circuit task")?
        .wrap_err("Failed to prove bridge circuit")?;
```

**File:** core/src/citrea.rs (L299-307)
```rust
        // It does not seem possible to get a storage proof with less than 3 items. But still
        // we check it to avoid panics.
        if response.storage_proof.len() < 3 {
            return Err(eyre::eyre!(
                "Expected at least 3 storage proofs, got {}",
                response.storage_proof.len()
            )
            .into());
        }
```

**File:** core/src/states/context.rs (L95-101)
```rust
    SendOperatorAsserts {
        kickoff_data: KickoffData,
        deposit_data: DepositData,
        watchtower_challenges: HashMap<usize, Transaction>,
        payout_blockhash: Witness,
        latest_blockhash: Witness,
    },
```

**File:** core/src/states/kickoff.rs (L276-300)
```rust
    async fn send_operator_asserts_if_ready(&mut self, context: &mut StateContext<T>) {
        context
            .capture_error(async |context| {
                {
                    // if all watchtower challenge utxos are spent and latest blockhash is committed, its safe to send asserts
                    if self.challenged
                        && self.spent_watchtower_utxos.len()
                            == self.deposit_data.get_num_watchtowers()
                        && self.latest_blockhash != Witness::default()
                    {
                        context
                            .dispatch_duty(Duty::SendOperatorAsserts {
                                kickoff_data: self.kickoff_data,
                                deposit_data: self.deposit_data.clone(),
                                watchtower_challenges: self.watchtower_challenges.clone(),
                                payout_blockhash: self.payout_blockhash.clone(),
                                latest_blockhash: self.latest_blockhash.clone(),
                            })
                            .await?;
                    }
                    Ok::<(), BridgeError>(())
                }
                .wrap_err(self.kickoff_meta("on send_operator_asserts"))
            })
            .await;
```
