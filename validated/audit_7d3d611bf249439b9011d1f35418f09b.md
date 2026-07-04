### Title
Missing Declared-Class Existence Check in `execute_replace_class` Allows Setting Contract to Undeclared Class Hash — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The StarkNet OS `execute_replace_class` syscall handler (both the Sierra and deprecated Cairo 0 variants) updates a contract's `class_hash` in state without verifying that the supplied class hash is actually declared in `contract_class_changes`. This is the direct analog of the solmate `safeTransfer` issue: just as solmate silently succeeds when calling `transfer` on a non-existent contract, the OS silently succeeds when replacing a class with an undeclared hash. Any subsequent call to the affected contract will fail irrecoverably at the OS proof level, permanently freezing all funds held by that contract.

---

### Finding Description

In `execute_replace_class` (Sierra path):

```cairo
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

The function reads `class_hash` directly from the syscall request and writes it into `contract_state_changes` with **no lookup into `contract_class_changes`** to confirm the class was ever declared. The TODO comment on line 898 explicitly acknowledges this missing check. [1](#0-0) 

The identical omission exists in the deprecated path: [2](#0-1) 

By contrast, `execute_entry_point` — the function that is called every time a contract is invoked — **does** require the class hash to resolve to a declared compiled class:

```cairo
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=execution_context.class_hash
);
// ...
let (compiled_class_fact: CompiledClassFact*) = find_element(
    array_ptr=compiled_class_facts_bundle.compiled_class_facts,
    ...
    key=compiled_class_hash,   // 0 if class was never declared
);
``` [3](#0-2) 

If `class_hash` is undeclared, `dict_read` on `contract_class_changes` returns `0` (the default uninitialized value, per `UNINITIALIZED_CLASS_HASH = 0`), and `find_element` with `key=0` will panic/abort because no compiled class with hash `0` is registered in the bundle. This makes the contract permanently uncallable. [4](#0-3) 

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once a contract's `class_hash` is set to an undeclared value via `replace_class`:

1. Every future call to that contract reaches `execute_entry_point`.
2. `dict_read` on `contract_class_changes` returns `0` for the undeclared hash.
3. `find_element` with `key=0` fails (no compiled class with hash `0` exists).
4. The OS proof for any block containing a call to that contract cannot be generated.
5. The contract is permanently bricked — all ERC-20 balances, NFTs, or other assets held by it are frozen with no recovery path.

---

### Likelihood Explanation

**Medium.** The sequencer's off-chain Rust layer likely performs a class-existence check before including a `replace_class` transaction in a block. However:

- The OS Cairo code is the authoritative protocol verifier. The absence of the check at the OS level means the invariant is not enforced by the proof system itself.
- A sequencer implementation bug, a future sequencer upgrade that omits the check, or a malicious sequencer can include a `replace_class` with an undeclared hash.
- Any contract deployer (unprivileged) can deploy a contract whose constructor or any entry point calls `replace_class(arbitrary_felt)`. The OS will accept it without complaint.
- The TODO comment (`// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.`) confirms the developers are aware the check is missing and it is not yet implemented. [5](#0-4) 

---

### Recommendation

Inside `execute_replace_class` (both Sierra and deprecated variants), after reading `class_hash` from the request, perform a lookup in `contract_class_changes` to confirm the class is declared (i.e., its compiled class hash is non-zero):

```cairo
let class_hash = request.class_hash;

// Verify the class is declared.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
assert_not_zero(compiled_class_hash);  // Revert if class is not declared.
```

This mirrors the check already performed in `execute_entry_point` and closes the gap between what the sequencer enforces off-chain and what the OS enforces on-chain.

---

### Proof of Concept

1. Attacker deploys contract `A` with an entry point that executes:
   ```
   replace_class(0xdeadbeef)   // 0xdeadbeef is never declared
   ```
2. Attacker calls contract `A`. The sequencer (if it lacks the check) includes the transaction.
3. The OS processes the block. `execute_replace_class` writes `class_hash=0xdeadbeef` into `contract_state_changes` for contract `A` with no validation.
4. In the same or a subsequent block, any call to contract `A` reaches `execute_entry_point`:
   - `dict_read(contract_class_changes, key=0xdeadbeef)` → returns `0` (undeclared).
   - `find_element(..., key=0)` → panics; no compiled class with hash `0` exists.
5. The block proof cannot be generated. Contract `A` is permanently frozen.
6. All funds (tokens, NFTs) held by contract `A` are irrecoverable. [6](#0-5) [2](#0-1) [3](#0-2)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L877-916)
```text
// Replaces the class.
func execute_replace_class{
    range_check_ptr,
    syscall_ptr: felt*,
    contract_state_changes: DictAccess*,
    revert_log: RevertLogEntry*,
}(contract_address: felt) {
    alloc_locals;
    let request = cast(syscall_ptr + RequestHeader.SIZE, ReplaceClassRequest*);

    // Reduce gas.
    let success = reduce_syscall_gas_and_write_response_header(
        total_gas_cost=REPLACE_CLASS_GAS_COST, request_struct_size=ReplaceClassRequest.SIZE
    );
    if (success == FALSE) {
        // Not enough gas to execute the syscall.
        return ();
    }

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

    assert [revert_log] = RevertLogEntry(selector=CHANGE_CLASS_ENTRY, value=state_entry.class_hash);
    let revert_log = &revert_log[1];

    return ();
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deprecated_execute_syscalls.cairo (L307-329)
```text
func execute_replace_class{contract_state_changes: DictAccess*, revert_log: RevertLogEntry*}(
    contract_address, syscall_ptr: ReplaceClass*
) {
    alloc_locals;
    let class_hash = syscall_ptr.class_hash;

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
    assert [revert_log] = RevertLogEntry(selector=CHANGE_CLASS_ENTRY, value=state_entry.class_hash);
    let revert_log = &revert_log[1];

    return ();
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L154-167)
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
    local compiled_class: CompiledClass* = compiled_class_fact.compiled_class;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/commitment.cairo (L16-16)
```text
const UNINITIALIZED_CLASS_HASH = 0;
```
