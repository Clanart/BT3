### Title
Unbounded `proof_facts_size` in Invoke Transactions Enables Prover Resource Exhaustion Leading to Network Halt - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

The `proof_facts_size` field of an invoke transaction is loaded from user-controlled transaction data and passed directly into an unbounded Poseidon hash loop inside `compute_invoke_transaction_hash`. Unlike every other variable-length array field in the same code path — `signature_len`, `calldata_size`, `constructor_calldata_size` — no `assert_nn_le(proof_facts_size, SIERRA_ARRAY_LEN_BOUND - 1)` guard is present. An unprivileged transaction sender can craft an invoke transaction whose `proof_facts_size` is an astronomically large felt value, forcing the OS to attempt hashing an unbounded number of elements and making it impossible for the prover to generate a valid proof for the block, causing a network halt.

---

### Finding Description

**Vulnerable code path — `execute_invoke_function_transaction`:**

`proof_facts_size` is loaded from the transaction hint with no upper-bound assertion:

```cairo
local proof_facts_size;
local proof_facts: felt*;
%{ TxProofFacts %}
``` [1](#0-0) 

It is then forwarded directly into `compute_invoke_transaction_hash`:

```cairo
let transaction_hash = compute_invoke_transaction_hash(
    ...
    proof_facts_size=proof_facts_size,
    proof_facts=proof_facts,
);
``` [2](#0-1) 

Inside `compute_invoke_transaction_hash`, the entire `proof_facts` array is iterated element-by-element via `poseidon_hash_update_with_nested_hash`:

```cairo
if (proof_facts_size != 0) {
    poseidon_hash_update_with_nested_hash(
        data_ptr=proof_facts, data_length=proof_facts_size
    );
}
``` [3](#0-2) 

`check_proof_facts`, called after the hash, only enforces a **minimum** size (`assert_le(ProofHeader.SIZE + VirtualOsOutputHeader.SIZE, proof_facts_size)`), never a maximum: [4](#0-3) 

**Contrast with every other variable-length field in the same file:**

| Field | Guard |
|---|---|
| `signature_len` | `assert_nn_le(signature_len, SIERRA_ARRAY_LEN_BOUND - 1)` |
| `calldata_size` | `assert_nn_le(tx_execution_context.calldata_size, SIERRA_ARRAY_LEN_BOUND - 1)` |
| `constructor_calldata_size` | `assert_nn_le(constructor_calldata_size, SIERRA_ARRAY_LEN_BOUND - 1)` |
| `proof_facts_size` | **no upper-bound check** | [5](#0-4) [6](#0-5) [7](#0-6) 

`SIERRA_ARRAY_LEN_BOUND` is defined as `2^32`: [8](#0-7) 

Without the guard, `proof_facts_size` is a raw felt, meaning it can be any value up to the field prime (~2^251).

---

### Impact Explanation

Each element of `proof_facts` requires one Poseidon builtin invocation. The Cairo VM has a finite step budget per block. If `proof_facts_size` is set to a value such as `2^200`, the OS program would require an astronomically large number of Poseidon steps — far beyond any feasible proof budget — making it impossible for the prover to generate a valid STARK proof for the block. The block cannot be finalized on L1, and if the sequencer does not detect and skip the malformed transaction, the network cannot confirm new transactions: **total network shutdown**.

This matches the allowed impact: **High — Network not being able to confirm new transactions (total network shutdown)**.

---

### Likelihood Explanation

The `proof_facts` field is a user-supplied field in an invoke transaction (version 3). Any unprivileged transaction sender can set it to an arbitrary value. The OS is the protocol-level ground truth for what constitutes a valid transaction; the missing bound in the OS means the protocol itself does not reject oversized `proof_facts_size`. If the sequencer relies on the OS to enforce array bounds (as it does for calldata and signature, which are also only bounded in the OS), it will forward the malformed transaction to the prover, triggering the resource exhaustion. The `proof_facts` feature is new (recursive virtual-OS proofs), making it plausible that sequencer-side validation has not yet been hardened for this field.

---

### Recommendation

Add the same upper-bound assertion used for every other variable-length array field, immediately after loading `proof_facts_size` from the hint:

```cairo
local proof_facts_size;
local proof_facts: felt*;
%{ TxProofFacts %}
assert_nn_le(proof_facts_size, SIERRA_ARRAY_LEN_BOUND - 1);  // ADD THIS
``` [1](#0-0) 

---

### Proof of Concept

1. Craft an invoke transaction (version 3) with `proof_facts_size = 2**200` and `proof_facts` pointing to any valid memory region.
2. Submit the transaction to the sequencer. The transaction hash is computed correctly (the hash commits to the large size), so nonce and signature checks pass.
3. The sequencer includes the transaction in a block (no OS-level rejection exists for this field).
4. The prover runs the OS on the block. Inside `compute_invoke_transaction_hash`, `poseidon_hash_update_with_nested_hash` attempts to consume `2^200` Poseidon builtin cells.
5. The prover exhausts its step budget and cannot produce a valid proof.
6. The block cannot be finalized; the network halts.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L218-218)
```text
    assert_nn_le(signature_len, SIERRA_ARRAY_LEN_BOUND - 1);
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L279-281)
```text
    local proof_facts_size;
    local proof_facts: felt*;
    %{ TxProofFacts %}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L285-292)
```text
        let transaction_hash = compute_invoke_transaction_hash(
            common_fields=common_tx_fields,
            execution_context=tx_execution_context,
            account_deployment_data_size=account_deployment_data_size,
            account_deployment_data=account_deployment_data,
            proof_facts_size=proof_facts_size,
            proof_facts=proof_facts,
        );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L485-485)
```text
    assert_nn_le(tx_execution_context.calldata_size, SIERRA_ARRAY_LEN_BOUND - 1);
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L534-534)
```text
    assert_nn_le(constructor_calldata_size, SIERRA_ARRAY_LEN_BOUND - 1);
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L209-213)
```text
        if (proof_facts_size != 0) {
            poseidon_hash_update_with_nested_hash(
                data_ptr=proof_facts, data_length=proof_facts_size
            );
        }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execution_constraints.cairo (L40-44)
```text
    if (proof_facts_size == 0) {
        return ();
    }

    assert_le(ProofHeader.SIZE + VirtualOsOutputHeader.SIZE, proof_facts_size);
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/constants.cairo (L22-22)
```text
const SIERRA_ARRAY_LEN_BOUND = 4294967296;  // 2^32
```
