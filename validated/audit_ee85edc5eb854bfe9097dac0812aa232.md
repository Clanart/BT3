Looking at the exact struct sizes and the Rust pre-validation parser to determine if the off-by-one gap is real.

**Struct sizes:**
- `ProofHeader`: 3 fields (`proof_version`, `proof_variant`, `program_hash`) → SIZE = 3
- `VirtualOsOutputHeader`: 5 fields (`output_version`, `base_block_number`, `base_block_hash`, `starknet_os_config_hash`, `n_l2_to_l1_messages`) → SIZE = 5
- Cairo minimum: 3 + 5 = **8**

**Rust parser minimum** (`fields.rs` lines 665–698):
- Splits at 2: `[proof_version, variant_marker]`
- Then requires `[program_hash, output_version, block_number_felt, block_hash, config_hash, ..]` from the remainder (5 more, with `..` allowing extras)
- Rust minimum: 2 + 5 = **7**

The Rust parser does not require `n_l2_to_l1_messages` (index 7), but the Cairo `assert_le` does. This is a real, concrete off-by-one gap.

---

### Title
Rust proof-facts pre-validator accepts 7-field arrays that Cairo OS `assert_le` rejects with an unrecoverable abort — (`execution_constraints.cairo`)

### Summary

The Rust pre-validation layer (`validate_proof_facts`) requires a minimum of **7** felts in `proof_facts`, while the Cairo OS `check_proof_facts` requires a minimum of **8** (`ProofHeader.SIZE + VirtualOsOutputHeader.SIZE`). An attacker can craft a 7-field proof facts array with all semantically valid values, pass Rust pre-validation, get the transaction included in a block, and then cause the SNOS prover to abort with an unrecoverable Cairo assertion failure when it tries to prove that block — preventing the network from ever confirming new transactions.

### Finding Description

**Cairo OS check** (`execution_constraints.cairo`, line 44):
```cairo
assert_le(ProofHeader.SIZE + VirtualOsOutputHeader.SIZE, proof_facts_size);
// = assert_le(3 + 5, proof_facts_size)  →  assert_le(8, proof_facts_size)
``` [1](#0-0) 

`ProofHeader` has 3 fields and `VirtualOsOutputHeader` has 5 fields (including `n_l2_to_l1_messages`): [2](#0-1) 

**Rust pre-validator** (`fields.rs`, lines 665–698) splits at 2 and then pattern-matches with `..` (trailing wildcard), requiring only 7 total felts — it never checks for the 8th field (`n_l2_to_l1_messages`): [3](#0-2) 

The Rust `SnosProofFacts` struct does not include `n_l2_to_l1_messages` at all: [4](#0-3) 

`validate_proof_facts` is the only gate before the transaction reaches the block: [5](#0-4) 

The `proof_facts_size` fed to the Cairo OS is set directly from `proof_facts.len()` by the hint processor — it is not independently clamped or re-validated: [6](#0-5) 

### Impact Explanation

`assert_le` in Cairo (`starkware.cairo.common.math`) is a hard, unrecoverable assertion. When it fails, the Cairo VM raises an error that aborts the entire OS execution — it is not catchable and does not produce a per-transaction revert. A single such transaction included in a block makes that block **unprovable**. The sequencer cannot skip it (the block is already committed), so the network halts: **High — total network shutdown**.

### Likelihood Explanation

All values needed to pass Rust pre-validation are public:
- `PROOF_VERSION`, `VIRTUAL_SNOS`, `VIRTUAL_OS_OUTPUT_VERSION` — public constants
- Allowed `program_hash` — public constant from `ALLOWED_VIRTUAL_OS_PROGRAM_HASHES_0`
- Valid `block_number` and `block_hash` — readable from on-chain state
- `config_hash` — computable from public chain info

Any unprivileged user with an Invoke V3 transaction can construct this payload.

### Recommendation

Add an explicit length check in the Rust `ProofFactsVariant::try_from` that requires `proof_facts.0.len() >= ProofHeader::SIZE + VirtualOsOutputHeader::SIZE` (i.e., `>= 8`) before any field extraction, mirroring the Cairo `assert_le` guard exactly. Alternatively, include `n_l2_to_l1_messages` in the Rust parsing so the minimum naturally becomes 8.

### Proof of Concept

1. Compute valid values: `proof_version = PROOF_VERSION`, `variant_marker = VIRTUAL_SNOS`, `program_hash = ALLOWED_VIRTUAL_OS_PROGRAM_HASHES_0`, `output_version = VIRTUAL_OS_OUTPUT_VERSION`, `block_number` = any stored block old enough, `block_hash` = its stored hash, `config_hash` = computed virtual OS config hash.
2. Construct `proof_facts = [proof_version, variant_marker, program_hash, output_version, block_number, block_hash, config_hash]` — exactly 7 felts.
3. Submit an Invoke V3 transaction with this `proof_facts`.
4. Rust `validate_proof_facts` passes (7 ≥ 7, all values valid).
5. Transaction is included in a block.
6. SNOS runs `check_proof_facts`: `proof_facts_size = 7 ≠ 0`, so it reaches `assert_le(8, 7)` → Cairo VM abort.
7. Block is unprovable; network cannot advance.

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

**File:** crates/starknet_api/src/transaction/fields.rs (L730-736)
```rust
pub struct SnosProofFacts {
    pub proof_version: Felt,
    pub program_hash: StarkHash,
    pub block_number: BlockNumber,
    pub block_hash: BlockHash,
    pub config_hash: StarkHash,
}
```

**File:** crates/blockifier/src/transaction/account_transaction.rs (L351-368)
```rust
    pub fn perform_pre_validation_stage<S: State + StateReader>(
        &self,
        state: &mut S,
        tx_context: &TransactionContext,
    ) -> TransactionPreValidationResult<()> {
        let tx_info = &tx_context.tx_info;
        Self::handle_nonce(state, tx_info, self.execution_flags.strict_nonce_check)?;

        if self.execution_flags.charge_fee {
            self.check_fee_bounds(tx_context)?;

            verify_can_pay_committed_bounds(state, tx_context).map_err(Box::new)?;
        }

        self.validate_proof_facts(&tx_context.block_context, state)?;

        Ok(())
    }
```

**File:** crates/starknet_os/src/hints/hint_implementation/execution/implementation.rs (L354-356)
```rust
    ctx.insert_value(Ids::ProofFacts, proof_facts_base)?;
    ctx.insert_value(Ids::ProofFactsSize, proof_facts.len())?;
    Ok(())
```
