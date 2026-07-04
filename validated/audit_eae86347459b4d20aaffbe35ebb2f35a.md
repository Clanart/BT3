### Title
Missing Declared Class Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds — (`File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS updates a contract's class hash in state without verifying that the new class hash corresponds to a declared contract class. This is a direct analog of the reported `kickWithDeposit` bug: a state-modifying operation bypasses a critical invariant check that the rest of the system depends on. Any contract can call `replace_class` with an arbitrary, undeclared class hash, permanently bricking itself and freezing any funds it holds.

---

### Finding Description

`execute_replace_class` in `syscall_impls.cairo` (lines 878–916) accepts any `class_hash` value from the syscall request and writes it directly into `contract_state_changes` with no validation:

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

The TODO comment at line 898 explicitly acknowledges the missing invariant check. The same omission exists in the deprecated path in `deprecated_execute_syscalls.cairo` (lines 307–329).

**The invariant that is bypassed:** Every class hash stored in `contract_state_changes` must have a corresponding entry in `contract_class_changes` (i.e., must be declared). This invariant is enforced by `execute_entry_point`, which performs two mandatory lookups on any class hash before executing it:

1. `dict_read{dict_ptr=contract_class_changes}(key=execution_context.class_hash)` — retrieves the compiled class hash.
2. `find_element(... key=compiled_class_hash)` — locates the compiled class in the block's compiled class facts bundle.

If either lookup fails (because the class hash was never declared), `find_element` panics and the OS cannot generate a valid proof for any block containing a call to that contract.

**Contrast with other functions:** `execute_deploy` and `execute_declare_transaction` both operate through the class declaration pipeline, ensuring that any class hash written to state has a corresponding compiled class. `execute_replace_class` is the sole exception.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Attack sequence:
1. A contract (holding user funds, e.g., an ERC-20 vault or escrow) calls `replace_class(undeclared_hash)` where `undeclared_hash` is any felt value not corresponding to a declared class.
2. The OS accepts this without complaint (no check exists). The block is proven and committed on L1 with the contract's class hash set to `undeclared_hash`.
3. In any subsequent block, any call to that contract causes `execute_entry_point` to call `dict_read` on `undeclared_hash` in `contract_class_changes`, returning 0 (default), then `find_element` with key=0 panics.
4. The OS cannot generate a valid proof for any block containing a call to that contract.
5. The sequencer is forced to permanently exclude all interactions with the contract.
6. All funds held by the contract are permanently frozen with no recovery path.

---

### Likelihood Explanation

**High.** The entry path requires no privileged access:
- Any contract deployer can deploy a contract that calls `replace_class` with an arbitrary felt value.
- The syscall is available to all Sierra/Cairo 1 contracts and all deprecated Cairo 0 contracts.
- No special permissions, leaked keys, or operator cooperation are required.
- The TODO comment confirms the developers are aware the check is absent, meaning the current code is intentionally (temporarily) unguarded.

---

### Recommendation

Before writing the new class hash to `contract_state_changes`, verify that it exists in `contract_class_changes`. Specifically, perform a `dict_read` on `contract_class_changes` for the requested `class_hash` and assert the result is non-zero (i.e., a compiled class hash has been registered for it):

```cairo
// Verify the new class hash is declared.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
with_attr error_message("Class hash is not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

This mirrors the check implicitly enforced by `execute_entry_point` and closes the invariant gap.

---

### Proof of Concept

1. Declare and deploy a contract `VaultWithTrap` that:
   - Accepts ETH/ERC-20 deposits from users.
   - Exposes a function `trap()` that calls `replace_class(0xdeadbeef)` — an undeclared class hash.

2. Users deposit funds into `VaultWithTrap`.

3. Attacker (or the contract itself, triggered by any condition) calls `trap()`.

4. The OS processes the `replace_class` syscall in `execute_replace_class`:
   - `class_hash = 0xdeadbeef` is written to `contract_state_changes` for `VaultWithTrap`.
   - No validation occurs (line 898 TODO is unimplemented).
   - The revert log records the old class hash, but the transaction succeeds — revert is not triggered.

5. The block containing `trap()` is proven and committed on L1.

6. In the next block, any call to `VaultWithTrap` reaches `execute_entry_point`:
   - `dict_read{dict_ptr=contract_class_changes}(key=0xdeadbeef)` → returns `0`.
   - `find_element(... key=0)` → panics (no compiled class with hash 0 exists).
   - The OS cannot produce a proof for this block.

7. The sequencer must permanently exclude all calls to `VaultWithTrap`. All deposited funds are frozen with no withdrawal path. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L878-916)
```text
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
