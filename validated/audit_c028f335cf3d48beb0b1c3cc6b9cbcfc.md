### Title
Missing Class Hash Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds — (File: `execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS does not validate that the new class hash corresponds to a previously declared contract class. Any contract can replace its own class hash with an arbitrary, undeclared felt value. Once replaced, the contract becomes permanently unexecutable, freezing any funds it holds. The OS proof accepts this invalid state transition without complaint.

---

### Finding Description

In `execute_replace_class`, the OS reads the requested class hash directly from the syscall request and writes it into `contract_state_changes` with no check that the hash exists in the declared class registry (`contract_class_changes`):

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
    ...
}
```

The inline `TODO` comment at line 898 explicitly acknowledges the missing invariant. The OS proof will accept any felt as a valid class hash for a contract, including values that were never declared via a `declare` transaction and therefore have no corresponding compiled class in the class trie. [1](#0-0) 

The `execute_replace_class` handler is reachable by any executing contract through the standard syscall dispatch in `execute_syscalls`: [2](#0-1) 

There is no caller restriction — any contract, deployed by any unprivileged user, may invoke this syscall.

By contrast, the `execute_declare_transaction` path enforces `prev_value=0` when writing to `contract_class_changes`, ensuring a class hash can only be declared once and must have a valid compiled class hash: [3](#0-2) 

No equivalent guard exists in `execute_replace_class`.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

When a contract's class hash is set to an undeclared value, the OS state commitment records the contract with an invalid class hash. Any subsequent call to that contract will fail at class resolution time (the compiled class does not exist), making the contract permanently unexecutable. All assets held in the contract's storage — including ERC-20 balances, NFT ownership records, or any other value-bearing state — are irrecoverably frozen.

This matches the allowed impact: **Critical. Permanent freezing of funds.**

---

### Likelihood Explanation

**Medium.**

The direct entry path is open to any unprivileged user:

1. An attacker deploys a contract whose code calls `replace_class` with an arbitrary felt (e.g., `1` or any random value not in the class trie).
2. Other users deposit funds into the contract (e.g., believing it is a legitimate vault or pool).
3. The attacker invokes the function that triggers `replace_class`.
4. The OS proof accepts the state transition without validation.
5. The contract is permanently broken; all deposited funds are frozen.

This is a realistic rug-pull vector enabled by a missing OS-level invariant. The OS is the correct enforcement layer for this check — individual contracts cannot self-enforce that their replacement class hash is valid, because the OS is the only component with access to the full declared class registry.

---

### Recommendation

Before writing the new class hash into `contract_state_changes`, verify that the hash exists in the declared class registry. Concretely, perform a `dict_read` on `contract_class_changes` (or the underlying class trie) and assert the result is non-zero:

```cairo
// Verify the new class hash is declared.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
with_attr error_message("replace_class: class hash is not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

This mirrors the invariant already enforced in `execute_declare_transaction` where `assert_not_zero(compiled_class_hash)` is called before writing to `contract_class_changes`. [4](#0-3) 

---

### Proof of Concept

1. **Deploy attacker contract** with class hash `C_valid` (a legitimately declared class). The contract exposes a `rug()` entry point that calls `replace_class(class_hash=0xdeadbeef)` where `0xdeadbeef` is never declared.
2. **Victim deposits funds** (e.g., calls `transfer` on an ERC-20 to the attacker contract's address, or calls a `deposit()` function).
3. **Attacker calls `rug()`**. The contract issues the `replace_class` syscall with `class_hash=0xdeadbeef`.
4. **OS processes the syscall** via `execute_replace_class`. No validation of `0xdeadbeef` against `contract_class_changes` is performed. The state entry for the attacker contract is updated: `class_hash = 0xdeadbeef`.
5. **State commitment** is computed and the proof is generated. The proof is valid — the OS accepted the transition.
6. **Any subsequent call** to the attacker contract fails: the OS cannot find a compiled class for `0xdeadbeef`. The contract is permanently unexecutable.
7. **Victim's funds are permanently frozen** with no recovery path. [5](#0-4)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L815-819)
```text
    // Note that prev_value=0 enforces that a class may be declared only once.
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```
