### Title
`execute_replace_class` Does Not Verify That the New Class Hash Is Declared — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS accepts an arbitrary class hash from the caller and updates the contract's state entry without verifying that the class hash corresponds to a previously declared contract class. This is a direct analog to M-17: just as `tokenURI` returned data for non-existent NFT IDs, `replace_class` accepts and commits non-existent class hashes. The consequence is that any contract can permanently corrupt its own class hash, making itself permanently uncallable and freezing all funds it holds.

---

### Finding Description

In `execute_replace_class` (lines 877–916 of `syscall_impls.cairo`), the OS reads `request.class_hash` and immediately writes it into `contract_state_changes` with no check that the hash was ever declared:

```cairo
let class_hash = request.class_hash;

// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.
local state_entry: StateEntry*;
%{ GetContractAddressStateEntry %}

tempvar new_state_entry = new StateEntry(
    class_hash=class_hash, storage_ptr=state_entry.storage_ptr, nonce=state_entry.nonce
);

dict_update{dict_ptr=contract_state_changes}(
    key=contract_address,
    prev_value=cast(state_entry, felt),
    new_value=cast(new_state_entry, felt),
);
```

The TODO comment is an explicit developer acknowledgment that this check is missing. [1](#0-0) 

After the replacement, the contract's `class_hash` in `contract_state_changes` is set to the undeclared value. When any subsequent transaction calls this contract, `execute_entry_point` performs:

1. `dict_read{dict_ptr=contract_class_changes}(key=undeclared_class_hash)` — returns `0` (`UNINITIALIZED_CLASS_HASH`) because the class was never declared.
2. `find_element(..., key=0)` — fails with an assertion error because no compiled class with hash `0` exists in `compiled_class_facts_bundle`. [2](#0-1) 

`find_element` is not a soft search — it asserts the element exists. A missing key causes an unrecoverable OS failure for any block that includes a call to the affected contract.

The `UNINITIALIZED_CLASS_HASH` constant is defined as `0`: [3](#0-2) 

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once a contract's class hash is replaced with an undeclared value, the contract becomes permanently uncallable at the OS level. No transaction can successfully execute against it. Any ERC-20 tokens, ETH, or other assets held by the contract are permanently frozen with no recovery path, because:

- The corrupted state is committed to the global state root.
- Every future attempt to call the contract causes the OS prover to fail on `find_element`.
- There is no mechanism to "un-replace" the class hash from outside the contract (the contract itself is now uncallable).

---

### Likelihood Explanation

**High.** Any deployed contract can issue the `replace_class` syscall. The OS enforces no validity constraint on the provided class hash. An attacker who controls any deployed contract — including one they deploy themselves — can trigger this with a single transaction. The sequencer may or may not validate this at the mempool layer, but the OS Cairo code itself is the authoritative enforcement layer for the proof, and it contains no such check.

---

### Recommendation

Before updating `contract_state_changes`, verify that `request.class_hash` exists in `contract_class_changes` (i.e., its compiled class hash is non-zero). This is already noted as a TODO in the code. Concretely:

```cairo
// Read the compiled class hash for the requested class.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
// Reject if the class has not been declared.
with_attr error_message("Class hash is not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

This mirrors the check that `execute_declare_transaction` enforces via `assert_not_zero(compiled_class_hash)` before writing to `contract_class_changes`. [4](#0-3) 

---

### Proof of Concept

1. Attacker deploys contract `C` holding user funds (e.g., acts as a vault).
2. Attacker sends a transaction from `C` that calls `replace_class(0xdeadbeef)`, where `0xdeadbeef` is not a declared class hash.
3. The OS processes `execute_replace_class`:
   - No existence check is performed.
   - `contract_state_changes[C].class_hash` is set to `0xdeadbeef`.
4. The block is proven and the corrupted state is committed to the global state root.
5. In any subsequent block, a user sends a transaction calling `C.__execute__`.
6. `execute_entry_point` reads `contract_class_changes[0xdeadbeef]` → returns `0`.
7. `find_element(..., key=0)` asserts failure — the OS cannot prove the block.
8. All funds in `C` are permanently frozen.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L896-910)
```text
    let class_hash = request.class_hash;

    // TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.
    local state_entry: StateEntry*;
    %{ GetContractAddressStateEntry %}

    tempvar new_state_entry = new StateEntry(
        class_hash=class_hash, storage_ptr=state_entry.storage_ptr, nonce=state_entry.nonce
    );

    dict_update{dict_ptr=contract_state_changes}(
        key=contract_address,
        prev_value=cast(state_entry, felt),
        new_value=cast(new_state_entry, felt),
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L154-166)
```text
    let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
        key=execution_context.class_hash
    );

    // The key must be at offset 0.
    static_assert CompiledClassFact.hash == 0;
    let compiled_class_facts_bundle = block_context.os_global_context.compiled_class_facts_bundle;
    let (compiled_class_fact: CompiledClassFact*) = find_element(
        array_ptr=compiled_class_facts_bundle.compiled_class_facts,
        elm_size=CompiledClassFact.SIZE,
        n_elms=compiled_class_facts_bundle.n_compiled_class_facts,
        key=compiled_class_hash,
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/commitment.cairo (L16-16)
```text
const UNINITIALIZED_CLASS_HASH = 0;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L816-818)
```text
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
```
