### Title
`replace_class` Syscall Accepts Undeclared Class Hash, Enabling Permanent Fund Freezing — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` function in the StarkNet OS accepts any arbitrary class hash from the caller without verifying that the hash corresponds to a previously declared class in `contract_class_changes`. This is the direct analog of the NFT flashloan bypass: just as the original report's sale check reads NFT ownership state that can be transiently manipulated, the OS's class-validity invariant is bypassed because `replace_class` never cross-checks the in-flight `contract_class_changes` dictionary. A contract can permanently replace its own class hash with an undeclared value, rendering itself permanently unexecutable and freezing any funds it holds.

---

### Finding Description

In `execute_declare_transaction`, the OS enforces that a class can only be registered once by requiring `prev_value=0` in the `contract_class_changes` dict update: [1](#0-0) 

This establishes the invariant: every class hash stored in a contract's `StateEntry` must have a corresponding entry in `contract_class_changes`.

However, `execute_replace_class` in `syscall_impls.cairo` updates the contract's `class_hash` field in `contract_state_changes` with the caller-supplied value, with an explicit TODO acknowledging the missing check: [2](#0-1) 

The function signature does not even receive `contract_class_changes` as an implicit argument, making it structurally impossible to perform the validation: [3](#0-2) 

The `execute_replace_class` call site in `execute_syscalls.cairo` confirms no additional validation is performed around the call: [4](#0-3) 

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

After `replace_class(undeclared_hash)` is accepted by the OS and committed to state, the contract's `StateEntry.class_hash` points to a class that has no compiled class entry. Every subsequent call to that contract will fail at the class-lookup stage. Any ERC-20 tokens, ETH, or other assets held in the contract's storage become permanently inaccessible. Because the OS proof is valid (the OS itself accepted the syscall), the state root will commit this broken class hash, and no future transaction can recover the funds.

---

### Likelihood Explanation

The attack path requires only a standard invoke transaction from an unprivileged sender. No privileged role, leaked key, or external dependency is needed. A malicious contract author can:

1. Deploy a contract with a legitimate class and attract user deposits.
2. Issue an invoke transaction whose `__execute__` calls `replace_class` with an arbitrary felt value that was never declared.
3. The OS accepts the syscall, the state is committed, and all funds are frozen.

The explicit `TODO` comment in the source confirms the developers are aware the check is absent, meaning this is a known gap in the current production code path.

---

### Recommendation

Add `contract_class_changes: DictAccess*` as an implicit argument to `execute_replace_class` and perform a `dict_read` on `contract_class_changes` keyed by `request.class_hash` before updating `contract_state_changes`. Assert that the returned value is non-zero (i.e., the class has been declared). This mirrors the pattern already used in `execute_declare_transaction` where `prev_value=0` enforces the declared-once invariant.

---

### Proof of Concept

1. Declare class `A` (legitimate) and deploy contract `C` with class `A`. Fund `C` with tokens.
2. Submit an invoke transaction to `C`. Inside `__execute__`, emit the syscall `replace_class(0xdeadbeef)` where `0xdeadbeef` has never been declared.
3. `execute_replace_class` reads `request.class_hash = 0xdeadbeef`, skips any class-existence check (the `contract_class_changes` dict is not consulted), and calls `dict_update` on `contract_state_changes` setting `C`'s `class_hash` to `0xdeadbeef`.
4. The OS proof is generated and accepted. The committed state root encodes `C.class_hash = 0xdeadbeef`.
5. Any subsequent call to `C` fails: the OS cannot find a compiled class for `0xdeadbeef`. All funds in `C` are permanently frozen.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L815-819)
```text
    // Note that prev_value=0 enforces that a class may be declared only once.
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L878-895)
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

```

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
