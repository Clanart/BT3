### Title
Missing Declared Class Validation in `execute_replace_class` Allows Arbitrary Class Hash Substitution - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS does not verify that the target `class_hash` supplied by the caller corresponds to a previously declared contract class. The OS itself acknowledges this gap with a `TODO` comment. This is the direct analog of the external report's "whitelist incompatible with proxies" vulnerability class: just as a whitelisted proxy can silently swap its implementation to a malicious one, a deployed contract can call `replace_class` with an arbitrary, undeclared class hash and the OS will accept and commit the state transition without any on-chain enforcement.

---

### Finding Description

In `syscall_impls.cairo`, `execute_replace_class` (lines 877–916) reads the caller-supplied `class_hash` from the syscall request and immediately writes it into `contract_state_changes` without checking that the hash exists in `contract_class_changes` (the declared-class registry):

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

The same omission exists in the deprecated path `deprecated_execute_syscalls.cairo` lines 307–329, where `execute_replace_class` also performs no existence check against `contract_class_changes`.

By contrast, `execute_declare_transaction` (transaction_impls.cairo lines 816–819) enforces `prev_value=0` to guarantee a class is declared exactly once before it enters the registry. There is no analogous enforcement at the `replace_class` syscall level.

---

### Impact Explanation

**Critical — Direct loss of funds / permanent freezing of funds.**

A contract (e.g., a token vault, an account contract, or a multi-sig) that holds or controls user funds can call `replace_class` with a class hash that:

1. Was never declared on-chain (hash maps to no bytecode in the class trie), causing all subsequent calls to the contract to fail with an unresolvable class lookup — permanently freezing all funds locked in that contract.
2. Points to a previously declared but malicious class (one that, for example, drains the contract's storage or redirects transfers), enabling direct theft of funds.

Because the OS commits the new `class_hash` into the state root without verifying it is declared, the resulting state is provably valid from the OS's perspective, and the damage is permanent and irreversible on-chain.

---

### Likelihood Explanation

Any deployed contract that implements the `replace_class` syscall can trigger this. The attacker-controlled entry path is:

1. Deploy a contract whose code calls `replace_class(arbitrary_hash)`.
2. Submit an invoke transaction targeting that contract.
3. The OS executes `execute_replace_class`, skips the missing existence check, and writes `arbitrary_hash` into `contract_state_changes`.
4. The state root is updated to reflect the new (invalid) class hash.

No privileged role, leaked key, or external dependency is required. Any unprivileged transaction sender who controls a deployed contract can execute this path.

---

### Recommendation

Before writing the new `class_hash` into `contract_state_changes`, verify that the hash exists in `contract_class_changes` (i.e., it was previously declared). Concretely, add a `dict_read` on `contract_class_changes` keyed by `class_hash` and assert the returned compiled class hash is non-zero:

```cairo
// Verify the target class has been declared.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
with_attr error_message("replace_class: class hash not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

This mirrors the invariant already enforced in `execute_declare_transaction` where `prev_value=0` ensures a class is registered before use.

---

### Proof of Concept

1. Attacker deploys contract `A` with a function `__execute__` that calls `replace_class(0xdeadbeef)` where `0xdeadbeef` is never declared.
2. Attacker submits an invoke transaction to contract `A`.
3. The OS dispatches to `execute_syscalls` → `execute_replace_class` in `syscall_impls.cairo`.
4. At line 896–910, `class_hash = 0xdeadbeef` is read from the request and written directly into `contract_state_changes` with no lookup into `contract_class_changes`.
5. The state root is updated. Contract `A` now has `class_hash = 0xdeadbeef` in the committed state.
6. Any subsequent call to contract `A` attempts to resolve class `0xdeadbeef` from the class trie, finds nothing, and fails — all funds in contract `A` are permanently frozen.

The `TODO` comment at line 898 is the developers' own acknowledgment that this check is missing:

```cairo
// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L814-819)
```text
    // Declare the class hash.
    // Note that prev_value=0 enforces that a class may be declared only once.
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
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
