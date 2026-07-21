### Title
Virtual OS Output Embeds Wrong Config Hash Field, Causing All Client-Side Proving Transactions to Fail Validation in Encrypted-State-Diff Deployments - (File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/os_utils__virtual.cairo)

### Summary

The virtual OS output header is constructed using `os_global_context.starknet_os_config_hash` (the full config hash, which includes the public-keys hash when state-diff encryption is enabled) instead of `os_global_context.virtual_os_config_hash` (the config hash without public keys, which is what the proof-facts validator always checks against). When a deployment enables state-diff encryption (`public_keys_hash != DEFAULT_PUBLIC_KEYS_HASH`), the two values diverge, and every Invoke V3 transaction carrying client-side proof facts is rejected at pre-validation with a config-hash mismatch error.

### Finding Description

`OsGlobalContext` carries two distinct config-hash fields:

- `starknet_os_config_hash` — Pedersen(`version`, `chain_id`, `fee_token_address` [, `public_keys_hash` if non-zero])
- `virtual_os_config_hash` — Pedersen(`version`, `chain_id`, `fee_token_address`) — always without public keys [1](#0-0) 

In `os_utils__virtual.cairo`, `get_block_os_output_header` constructs the `OsOutputHeader` and writes the **wrong** field:

```cairo
starknet_os_config_hash=os_global_context.starknet_os_config_hash,  // BUG: should be virtual_os_config_hash
``` [2](#0-1) 

`process_os_output` then copies this value verbatim into the `VirtualOsOutputHeader` that becomes the proof facts:

```cairo
starknet_os_config_hash=header.starknet_os_config_hash,
``` [3](#0-2) 

The Cairo-side validator `check_proof_facts` then asserts:

```cairo
assert os_output_header.starknet_os_config_hash = virtual_os_config_hash;
``` [4](#0-3) 

The Rust-side `validate_proof_facts` in the blockifier performs the same check, always computing `virtual_os_config_hash` (without public keys):

```rust
let virtual_os_config_hash = OsChainInfo::from(chain_info)
    .compute_virtual_os_config_hash()
    .expect("Failed to compute OS config hash");
``` [5](#0-4) 

`compute_virtual_os_config_hash` always calls `compute_os_config_hash(None)`, stripping public keys unconditionally: [6](#0-5) 

When `public_keys_hash == DEFAULT_PUBLIC_KEYS_HASH` (0), both fields are equal and the bug is silent. When `public_keys_hash != 0` (state-diff encryption enabled), `starknet_os_config_hash != virtual_os_config_hash`, so the proof facts produced by the virtual OS contain the wrong config hash value, and every transaction carrying those facts is rejected.

### Impact Explanation

Every Invoke V3 transaction that carries client-side proof facts (produced by `VirtualSnosProver`) will fail `perform_pre_validation_stage` with `InvalidProofFacts("Virtual OS config hash mismatch")` in any deployment that enables state-diff encryption. The gateway/mempool will reject all such transactions before sequencing. No funds are at risk, but the entire client-side proving feature is non-functional in that deployment configuration.

This matches: **High. Mempool/gateway/RPC admission accepts invalid transactions or rejects valid transactions before sequencing.**

### Likelihood Explanation

The bug is latent on standard Starknet mainnet/testnet where `public_keys_hash = 0`. It activates in any deployment that sets non-zero public keys for state-diff encryption. The trigger is unprivileged: any user submitting a valid client-side proving transaction in such a deployment will hit the rejection. No special attacker capability is required.

### Recommendation

In `os_utils__virtual.cairo`, change line 68 from:

```cairo
starknet_os_config_hash=os_global_context.starknet_os_config_hash,
```

to:

```cairo
starknet_os_config_hash=os_global_context.virtual_os_config_hash,
``` [2](#0-1) 

This mirrors the fix in the external report (passing the correct identifier instead of the wrong one from the same struct), and aligns the virtual OS output with what both the Cairo `check_proof_facts` and the Rust `validate_proof_facts` expect.

### Proof of Concept

1. Deploy the sequencer with a non-zero `public_keys_hash` (state-diff encryption enabled). This causes `starknet_os_config_hash != virtual_os_config_hash` in `OsGlobalContext`.
2. Run `VirtualSnosProver::prove_transaction` for any Invoke V3 transaction. The virtual OS executes `get_block_os_output_header`, embedding `os_global_context.starknet_os_config_hash` (full, with public keys) into the `VirtualOsOutputHeader.starknet_os_config_hash` field.
3. The resulting `ProofFacts` carry `config_hash = starknet_os_config_hash` (wrong value).
4. Submit the transaction with these proof facts. `perform_pre_validation_stage` calls `validate_proof_facts`, which computes `virtual_os_config_hash` (without public keys) and compares it to `snos_proof_facts.config_hash`. The values differ.
5. The transaction is rejected: `InvalidProofFacts("Virtual OS config hash mismatch. Computed virtual OS config hash: X, expected virtual OS config hash: Y.")`. [7](#0-6)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/os.cairo (L306-319)
```text
    local virtual_os_config_hash;
    if (public_keys_hash != DEFAULT_PUBLIC_KEYS_HASH) {
        tempvar starknet_os_config_without_public_keys = new StarknetOsConfig(
            chain_id=chain_id,
            fee_token_address=fee_token_address,
            public_keys_hash=DEFAULT_PUBLIC_KEYS_HASH,
        );
        let (hash_without_keys) = get_starknet_os_config_hash{hash_ptr=pedersen_ptr}(
            starknet_os_config=starknet_os_config_without_public_keys
        );
        assert virtual_os_config_hash = hash_without_keys;
    } else {
        assert virtual_os_config_hash = starknet_os_config_hash;
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/os_utils__virtual.cairo (L61-72)
```text
    tempvar os_output_header = new OsOutputHeader(
        state_update_output=state_update_output,
        prev_block_number=block_context.block_info_for_execute.block_number,
        new_block_number=0,
        prev_block_hash=prev_block_hash,
        new_block_hash=0,
        os_program_hash=0,
        starknet_os_config_hash=os_global_context.starknet_os_config_hash,
        use_kzg_da=FALSE,
        full_output=TRUE,
    );
    return os_output_header;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/os_utils__virtual.cairo (L104-110)
```text
    assert [output_header_placeholder] = VirtualOsOutputHeader(
        output_version=VIRTUAL_OS_OUTPUT_VERSION,
        base_block_number=header.prev_block_number,
        base_block_hash=header.prev_block_hash,
        starknet_os_config_hash=header.starknet_os_config_hash,
        n_l2_to_l1_messages=n_l2_to_l1_messages,
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execution_constraints.cairo (L78-80)
```text
    // validate that the proof facts config hash is the true hash of the OS config.
    assert os_output_header.starknet_os_config_hash = virtual_os_config_hash;

```

**File:** crates/blockifier/src/transaction/account_transaction.rs (L333-344)
```rust
        let chain_info = &block_context.chain_info;
        // TODO(Meshi): Cache this computation as part of the chain context.
        let virtual_os_config_hash = OsChainInfo::from(chain_info)
            .compute_virtual_os_config_hash()
            .expect("Failed to compute OS config hash");
        let proof_config_hash = snos_proof_facts.config_hash;
        if virtual_os_config_hash != proof_config_hash {
            return Err(TransactionPreValidationError::InvalidProofFacts(format!(
                "Virtual OS config hash mismatch. Computed virtual OS config hash: \
                 {virtual_os_config_hash}, expected virtual OS config hash: {proof_config_hash}."
            )));
        }
```

**File:** crates/starknet_api/src/core.rs (L153-156)
```rust
    /// Computes the virtual OS config hash (without public keys).
    pub fn compute_virtual_os_config_hash(&self) -> Result<Felt, StarknetApiError> {
        self.compute_os_config_hash(None)
    }
```
