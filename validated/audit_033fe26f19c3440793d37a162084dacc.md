### Title
Missing Class Hash Validation in `execute_replace_class` Allows Permanent Freezing of Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS accepts any arbitrary felt value as a new class hash without verifying that the hash corresponds to a previously declared contract class. A contract can call `replace_class` with an undeclared class hash, permanently bricking itself. Any funds stored in that contract's storage become permanently inaccessible, because all subsequent calls to the contract fail at the proof level when the OS cannot locate the compiled class.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function reads `request.class_hash` and immediately writes it into `contract_state_changes` without any validation:

```cairo
func execute_replace_class{...}(contract_address: felt) {
    ...
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
``` [1](#0-0) 

The TODO comment at line 898 explicitly acknowledges the missing check. The OS accepts the state transition unconditionally.

When a subsequent transaction calls the bricked contract, `execute_entry_point` attempts to resolve the class hash:

```cairo
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=execution_context.class_hash
);
...
let (compiled_class_fact: CompiledClassFact*) = find_element(
    array_ptr=compiled_class_facts_bundle.compiled_class_facts,
    elm_size=CompiledClassFact.SIZE,
    n_elms=compiled_class_facts_bundle.n_compiled_class_facts,
    key=compiled_class_hash,
);
``` [2](#0-1) 

If `class_hash` was never declared, `dict_read` returns 0 (the default uninitialized value), and `find_element` raises a Cairo assertion failure because no compiled class with hash 0 exists in `compiled_class_facts_bundle`. The proof cannot be generated for any block containing a call to the bricked contract. The sequencer is permanently unable to include such transactions, and all funds in the contract's storage are frozen.

This is structurally identical to the ERC-721 locking issue: the bridge (OS) accepts a state-changing operation (`replace_class`) without validating that the new type/interface (class hash) is actually usable, and the second phase (calling the contract) fails irrecoverably.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Any contract that holds user assets (token balances, ETH, vault deposits) and calls `replace_class` with an undeclared class hash becomes permanently bricked. The contract's storage — including all user balances — is frozen forever. There is no protocol-level recovery path: the contract cannot be called to withdraw funds, and no standard transaction can update its class hash back to a valid one (because calling the contract itself fails at proof generation).

---

### Likelihood Explanation

**Medium-High.** The `replace_class` syscall is available to any contract execution — no privileged role is required. A malicious contract deployer can:

1. Deploy a contract that appears legitimate (e.g., a vault or token contract).
2. Attract user deposits.
3. Call `replace_class` with an arbitrary undeclared felt value.
4. The OS commits the state transition without validation.
5. All user funds are permanently locked.

This is a direct, one-step rug-pull vector available to any unprivileged contract deployer. It requires no leaked keys, no operator collusion, and no external dependency.

---

### Recommendation

In `execute_replace_class`, before updating `contract_state_changes`, verify that the requested `class_hash` has a corresponding entry in `contract_class_changes` (i.e., it was previously declared via a `declare` transaction). The check should assert that `dict_read{dict_ptr=contract_class_changes}(key=class_hash)` returns a non-zero compiled class hash. This mirrors the validation already performed implicitly in `execute_entry_point` — but that validation occurs too late (after the state has already been committed).

The existing TODO comment at line 898 of `syscall_impls.cairo` already identifies this gap; it must be resolved before the syscall is considered safe.

---

### Proof of Concept

1. Declare a contract class `C` with a `replace_class` call in its body targeting class hash `0xdeadbeef` (never declared).
2. Deploy an instance of `C` at address `A`. Users deposit tokens into `A`.
3. Invoke the entry point that calls `replace_class(0xdeadbeef)`.
4. The OS executes `execute_replace_class`: no validation occurs; `contract_state_changes[A].class_hash` is set to `0xdeadbeef`. The transaction is committed.
5. In the next block, a user submits a withdrawal transaction calling `A`.
6. `execute_entry_point` reads `class_hash = 0xdeadbeef`, calls `dict_read` on `contract_class_changes` → returns 0 (undeclared), calls `find_element` with key 0 → Cairo assertion failure.
7. The sequencer cannot prove any block containing a call to `A`. All funds at `A` are permanently frozen. [3](#0-2) [4](#0-3)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L153-167)
```text
    alloc_locals;
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
