### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS does not verify that the new class hash supplied by a calling contract is actually declared in the system. Any contract can call `replace_class` with an arbitrary, undeclared class hash. Once committed to state, no transaction can ever successfully execute against that contract again, permanently freezing any funds it holds.

---

### Finding Description

In `execute_replace_class`, the OS reads the requested class hash directly from the syscall request and writes it into `contract_state_changes` without any validation that the hash corresponds to a declared class:

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

The TODO comment at line 898 explicitly acknowledges the missing guard. The OS accepts any felt value as the new class hash.

In subsequent blocks, when any transaction attempts to call this contract, `execute_entry_point` performs:

```cairo
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=execution_context.class_hash
);
``` [2](#0-1) 

Because the class hash is undeclared, `dict_read` returns `0` (the uninitialized default). The OS then calls:

```cairo
let (compiled_class_fact: CompiledClassFact*) = find_element(
    array_ptr=compiled_class_facts_bundle.compiled_class_facts,
    elm_size=CompiledClassFact.SIZE,
    n_elms=compiled_class_facts_bundle.n_compiled_class_facts,
    key=compiled_class_hash,   // key = 0
);
``` [3](#0-2) 

`find_element` panics when key `0` is absent from the compiled class facts bundle, causing the entire OS execution to fail. An honest sequencer therefore cannot include any transaction — direct or indirect — that reaches this contract. The contract is permanently bricked.

This is structurally identical to the `TokenStaking.recoverStake` bug: a guard condition (`find_element` requiring a valid compiled class) is bypassed because the prerequisite value (a declared class hash) is never enforced to be non-trivial/non-zero before it is committed to state.

---

### Impact Explanation

Any ERC-20 balance, ETH, or other asset held in a contract whose class hash has been replaced with an undeclared value is permanently inaccessible. There is no recovery path: the contract cannot call `replace_class` again (execution is impossible), and no external party can restore it. This satisfies **Critical — Permanent freezing of funds**.

---

### Likelihood Explanation

The attack requires only a malicious contract deployer — an explicitly listed unprivileged role. The deployer:

1. Publishes a contract that appears legitimate (vault, token, bridge adapter).
2. Attracts user deposits.
3. Calls an internal function that emits `replace_class(undeclared_hash)`.
4. The honest sequencer includes the transaction (it passes all gas and nonce checks at inclusion time).
5. State is committed; the contract is permanently frozen.

No privileged access, leaked key, or network-level attack is required. The `REPLACE_CLASS_SELECTOR` branch in `execute_syscalls` is reachable by any deployed contract. [4](#0-3) 

---

### Recommendation

Inside `execute_replace_class`, before writing the new class hash to state, verify that it is present in `contract_class_changes` (i.e., it was declared in this or a prior block):

```cairo
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=class_hash
);
with_attr error_message("Class hash is not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

This mirrors the existing pattern in `execute_entry_point` and closes the gap identified in the TODO comment.

---

### Proof of Concept

1. Attacker deploys `MaliciousVault` — a contract with a `freeze()` entry point that calls `replace_class(0xdeadbeef)` where `0xdeadbeef` is never declared.
2. Users call `deposit()`, locking funds inside `MaliciousVault`.
3. Attacker submits an invoke transaction calling `freeze()`.
4. The OS executes `execute_replace_class`: no declared-class check exists; `contract_state_changes[MaliciousVault.address].class_hash` is set to `0xdeadbeef`. Transaction succeeds and is committed.
5. In the next block, any transaction targeting `MaliciousVault` reaches `execute_entry_point`. `dict_read(contract_class_changes, 0xdeadbeef)` returns `0`. `find_element(..., key=0)` panics — OS proof fails.
6. The honest sequencer permanently excludes all calls to `MaliciousVault`. All deposited funds are frozen with no recovery mechanism.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L896-913)
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

    assert [revert_log] = RevertLogEntry(selector=CHANGE_CLASS_ENTRY, value=state_entry.class_hash);
    let revert_log = &revert_log[1];
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L154-156)
```text
    let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
        key=execution_context.class_hash
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L161-166)
```text
    let (compiled_class_fact: CompiledClassFact*) = find_element(
        array_ptr=compiled_class_facts_bundle.compiled_class_facts,
        elm_size=CompiledClassFact.SIZE,
        n_elms=compiled_class_facts_bundle.n_compiled_class_facts,
        key=compiled_class_hash,
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_syscalls.cairo (L195-203)
```text
    if (selector == REPLACE_CLASS_SELECTOR) {
        execute_replace_class(contract_address=execution_context.execution_info.contract_address);
        %{ OsLoggerExitSyscall %}
        return execute_syscalls(
            block_context=block_context,
            execution_context=execution_context,
            syscall_ptr_end=syscall_ptr_end,
        );
    }
```
