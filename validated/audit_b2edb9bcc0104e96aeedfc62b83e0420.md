### Title
Rust `ProofFactsVariant::try_from` Accepts 7-Felt Proof Facts While Cairo OS `check_proof_facts` Requires Minimum 8 Felts — (`crates/starknet_api/src/transaction/fields.rs`)

---

### Summary

The Rust proof-facts parser accepts a minimum of **7 felts**, but the Cairo OS `check_proof_facts` enforces a minimum of **8 felts** (`ProofHeader.SIZE + VirtualOsOutputHeader.SIZE = 3 + 5`). An attacker can craft a 7-felt `ProofFacts` payload that passes gateway/blockifier pre-validation yet causes a hard Cairo assertion failure when the block is executed by SNOS, breaking the proving pipeline for that block.

---

### Finding Description

**Cairo layout** (`virtual_os_output.cairo`):

```cairo
struct ProofHeader {          // SIZE = 3
    proof_version: felt,      // index 0
    proof_variant: felt,      // index 1
    program_hash: felt,       // index 2
}
struct VirtualOsOutputHeader { // SIZE = 5
    output_version: felt,      // index 3
    base_block_number: felt,   // index 4
    base_block_hash: felt,     // index 5
    starknet_os_config_hash: felt, // index 6
    n_l2_to_l1_messages: felt, // index 7  ← required by Cairo
}
```

`check_proof_facts` asserts:

```cairo
assert_le(ProofHeader.SIZE + VirtualOsOutputHeader.SIZE, proof_facts_size);
// assert_le(8, proof_facts_size)  → minimum 8 felts required
``` [1](#0-0) [2](#0-1) 

**Rust layout** (`fields.rs`):

```rust
let Some(([proof_version, variant_marker], snos_fields)) =
    proof_facts.0.split_at_checked(2)   // indices 0-1
...
let [program_hash, output_version, block_number_felt, block_hash, config_hash, ..] =
    snos_fields                          // indices 2-6, then `..` ignores the rest
```

The `..` wildcard means the pattern matches with **zero** trailing elements, so the minimum accepted length is **7 felts** (indices 0–6). The `n_l2_to_l1_messages` field at index 7 is never required. [3](#0-2) 

`validate_proof_facts` in the blockifier calls only `ProofFactsVariant::try_from`, so the 7-felt payload passes the entire Rust pre-validation stage: [4](#0-3) 

---

### Impact Explanation

A 7-felt `ProofFacts` payload:

1. **Passes** `ProofFactsVariant::try_from` → admitted by gateway and mempool.
2. **Passes** `validate_proof_facts` in the blockifier → transaction is executed and included in a block.
3. **Fails** `check_proof_facts` in SNOS with a hard `assert_le(8, 7)` Cairo assertion → the OS execution aborts, the block cannot be proven.

This matches **High — Mempool/gateway/RPC admission accepts invalid transactions before sequencing**, and potentially **Critical — wrong revert result from blockifier/execution logic for accepted input** (the blockifier accepts and executes a transaction the OS will reject).

---

### Likelihood Explanation

Any unprivileged user can submit an `InvokeV3` transaction with a manually crafted 7-element `proof_facts` array. No special privilege or state is required. The gateway performs no independent field-count check beyond `ProofFactsVariant::try_from`.

---

### Recommendation

Update `ProofFactsVariant::try_from` to require at least 8 felts, matching the Cairo OS minimum:

```rust
// After splitting off [proof_version, variant_marker]:
let [program_hash, output_version, block_number_felt, block_hash, config_hash,
     _n_l2_to_l1_messages, ..] =   // require n_l2_to_l1_messages to be present
    snos_fields
else {
    return Err(StarknetApiError::InvalidProofFacts(format!(
        "SNOS proof facts is too small with {} fields (minimum 8 required)",
        proof_facts.0.len()
    )));
};
```

Alternatively, reuse `VirtualOsOutput::from_raw_output` (which already enforces the full layout) as the single source of truth for parsing, as the existing TODO comment suggests: [5](#0-4) [6](#0-5) 

---

### Proof of Concept

```rust
use starknet_api::transaction::fields::{
    ProofFacts, ProofFactsVariant, PROOF_VERSION, VIRTUAL_SNOS,
    VIRTUAL_OS_OUTPUT_VERSION,
};

fn test_7_felt_proof_facts_passes_rust_but_fails_cairo() {
    // Construct a 7-felt ProofFacts — missing n_l2_to_l1_messages (index 7).
    let proof_facts = ProofFacts::from(vec![
        PROOF_VERSION,               // 0: proof_version
        VIRTUAL_SNOS,                // 1: variant_marker
        Felt::from(0xdeadbeef_u64),  // 2: program_hash
        VIRTUAL_OS_OUTPUT_VERSION,   // 3: output_version
        Felt::from(100_u64),         // 4: base_block_number
        Felt::from(0xabcd_u64),      // 5: base_block_hash
        Felt::from(0x1234_u64),      // 6: starknet_os_config_hash
        // index 7 (n_l2_to_l1_messages) is ABSENT
    ]);
    assert_eq!(proof_facts.0.len(), 7);

    // Rust validation passes (requires only 7 felts).
    assert!(ProofFactsVariant::try_from(&proof_facts).is_ok());

    // Cairo OS would execute: assert_le(8, 7) → FAIL
    // ProofHeader.SIZE + VirtualOsOutputHeader.SIZE = 3 + 5 = 8 > 7
}
```

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execution_constraints.cairo (L44-44)
```text
    assert_le(ProofHeader.SIZE + VirtualOsOutputHeader.SIZE, proof_facts_size);
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/virtual_os_output.cairo (L53-67)
```text
struct ProofHeader {
    proof_version: felt,
    proof_variant: felt,
    program_hash: felt,
}

// The header of the virtual OS output.
struct VirtualOsOutputHeader {
    output_version: felt,
    // The block number and hash that this run is based on.
    base_block_number: felt,
    base_block_hash: felt,
    starknet_os_config_hash: felt,
    n_l2_to_l1_messages: felt,
}
```

**File:** crates/starknet_api/src/transaction/fields.rs (L665-698)
```rust
        let Some(([proof_version, variant_marker], snos_fields)) =
            proof_facts.0.split_at_checked(2)
        else {
            return Err(StarknetApiError::InvalidProofFacts(format!(
                "Proof facts must have at least 2 fields, got {}",
                proof_facts.0.len()
            )));
        };

        // Validate that the first element is PROOF_VERSION.
        if *proof_version != PROOF_VERSION {
            return Err(StarknetApiError::InvalidProofFacts(format!(
                "Expected first field to be {} (PROOF_VERSION), but got {}",
                PROOF_VERSION, proof_version
            )));
        }

        // Validate that the second element is VIRTUAL_SNOS.
        if *variant_marker != VIRTUAL_SNOS {
            return Err(StarknetApiError::InvalidProofFacts(format!(
                "Non-SNOS proofs are not currently supported. Expected second field to be {} \
                 (VIRTUAL_SNOS), but got {}",
                VIRTUAL_SNOS, variant_marker
            )));
        }

        let [program_hash, output_version, block_number_felt, block_hash, config_hash, ..] =
            snos_fields
        else {
            return Err(StarknetApiError::InvalidProofFacts(format!(
                "SNOS proof facts is too small with {} fields",
                proof_facts.0.len()
            )));
        };
```

**File:** crates/starknet_api/src/transaction/fields.rs (L700-700)
```rust
        // TODO(Yoni): reuse VirtualOsOutput parsing.
```

**File:** crates/blockifier/src/transaction/account_transaction.rs (L305-311)
```rust
        let proof_facts = invoke_tx.proof_facts();
        let snos_proof_facts = match ProofFactsVariant::try_from(&proof_facts)
            .map_err(|e| TransactionPreValidationError::InvalidProofFacts(e.to_string()))?
        {
            ProofFactsVariant::Empty => return Ok(()),
            ProofFactsVariant::Snos(snos_proof_facts) => snos_proof_facts,
        };
```

**File:** crates/starknet_os/src/io/virtual_os_output.rs (L32-68)
```rust
    pub fn from_raw_output(raw_output: &[Felt]) -> Result<Self, OsOutputError> {
        let mut iter = raw_output.iter().copied();

        let version = wrap_missing(iter.next(), "version")?;
        let expected_version = VIRTUAL_OS_OUTPUT_VERSION;
        if version != expected_version {
            return Err(OsOutputError::InvalidOsOutputField {
                value_name: "version".to_string(),
                val: version,
                message: format!("expected {expected_version}"),
            });
        }
        let base_block_number = BlockNumber(wrap_missing_as(iter.next(), "base_block_number")?);
        let base_block_hash = wrap_missing(iter.next(), "base_block_hash")?;
        let starknet_os_config_hash = wrap_missing(iter.next(), "starknet_os_config_hash")?;
        let n_l2_to_l1_messages: usize = wrap_missing_as(iter.next(), "n_l2_to_l1_messages")?;

        // Read the hashes array.
        let mut messages_to_l1_hashes = Vec::with_capacity(n_l2_to_l1_messages);
        for i in 0..n_l2_to_l1_messages {
            let hash = wrap_missing(iter.next(), &format!("messages_to_l1_hashes[{}]", i))?;
            messages_to_l1_hashes.push(hash);
        }

        // Verify that we have consumed all output.
        if iter.next().is_some() {
            return Err(OsOutputError::OutputNotExhausted);
        }

        Ok(Self {
            version,
            base_block_number,
            base_block_hash,
            starknet_os_config_hash,
            messages_to_l1_hashes,
        })
    }
```
