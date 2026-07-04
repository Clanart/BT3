### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS does not verify that the new class hash supplied by a contract corresponds to a previously declared class. This is an acknowledged gap (marked with a `TODO` comment). As a result, any contract can replace its own class hash with an arbitrary, undeclared value. Once committed to state, any future call to that contract causes the OS proof to fail irrecoverably, permanently freezing all funds held by the contract.

---

### Finding Description

The `execute_replace_class` function in `syscall_impls.cairo` accepts the caller-supplied `request.class_hash` and writes it directly into `contract_state_changes` without checking whether that hash exists in `contract_class_changes` (i.e., whether it was ever declared via a `declare` transaction):

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
``` [1](#0-0) 

The `TODO` comment at line 898 explicitly acknowledges the missing check.

When `execute_entry_point` is later called for a contract whose class hash is undeclared, the OS performs:

```cairo
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=execution_context.class_hash
);
// compiled_class_hash == 0 for an undeclared class

let (compiled_class_fact: CompiledClassFact*) = find_element(
    array_ptr=compiled_class_facts_bundle.compiled_class_facts,
    ...
    key=compiled_class_hash,   // key = 0, not present
);
``` [2](#0-1) 

`find_element` asserts the element exists. If the compiled class hash is 0 (or any value not in the bundle), the assertion fails and no valid proof can be produced for any block that attempts to call the contract. The sequencer is forced to permanently exclude all calls to that contract, freezing its funds.

The analog to the `onlyInitOr` bypass is direct: just as `DEFAULT_ADMIN_ROLE` could bypass the time-window restriction by self-granting the target role (circumventing the intended access control), here a contract can bypass the "class must be declared" protocol invariant by calling `replace_class` with an arbitrary hash — because the OS enforces no such constraint.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once a contract's class hash is set to an undeclared value and the block is proven and finalized on L1, the state root commits to this invalid class hash. No future block can include a successful call to that contract (the OS proof would be unsound). Any ERC-20 tokens, ETH, STRK, or other assets held in the contract's storage become permanently inaccessible. There is no recovery path because the state root is final on L1.

---

### Likelihood Explanation

The attack surface is reachable by any unprivileged contract deployer or caller:

1. An attacker deploys a contract whose code calls `replace_class(undeclared_hash)` — this requires no special privilege, only the ability to deploy a contract and pay gas.
2. The attacker (or any user) sends funds to the contract before or after deployment.
3. The attacker triggers the `replace_class` call (e.g., via a public entry point or a constructor).
4. The OS processes the syscall, writes the undeclared hash to state, and produces a valid proof for that block (the check is missing).
5. After finalization, the contract is permanently bricked.

This can also be weaponized against third-party contracts that expose a `replace_class` call path reachable by an attacker (e.g., via reentrancy, a misconfigured proxy, or a social-engineering upgrade flow).

---

### Recommendation

Before writing the new class hash to `contract_state_changes`, verify that it exists in `contract_class_changes` (i.e., it was declared in the current or a prior block). Concretely, perform a `dict_read` on `contract_class_changes` with the new class hash and assert the returned compiled class hash is non-zero:

```cairo
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
with_attr error_message("Class hash is not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

This mirrors the invariant already enforced at class-declaration time and closes the gap acknowledged by the existing `TODO` comment.

---

### Proof of Concept

1. **Deploy a contract** whose constructor (or any external entry point) executes the `replace_class` syscall with `class_hash = 0xdeadbeef` (an arbitrary undeclared felt).
2. **Send funds** (e.g., STRK tokens) to the contract address.
3. **Trigger the entry point** that calls `replace_class(0xdeadbeef)`.
4. The OS executes `execute_replace_class`:
   - Gas is deducted.
   - `request.class_hash = 0xdeadbeef` is written to `contract_state_changes` with no validation.
   - The revert log records the old class hash.
   - The function returns successfully.
5. The block is proven and finalized on L1 with the new state root containing `class_hash = 0xdeadbeef` for the contract.
6. In any subsequent block, a call to the contract causes `execute_entry_point` to call `dict_read(contract_class_changes, 0xdeadbeef)` → returns 0 → `find_element(..., key=0)` → assertion failure → no valid proof can be produced.
7. The sequencer permanently excludes the contract. All funds are frozen. [3](#0-2) [4](#0-3)

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
