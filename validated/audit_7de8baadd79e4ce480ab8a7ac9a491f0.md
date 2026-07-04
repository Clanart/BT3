### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS writes an arbitrary caller-supplied class hash directly into `contract_state_changes` without verifying that the hash corresponds to a previously declared contract class. A developer comment in the code explicitly acknowledges this missing check. If a contract replaces its class with an undeclared hash, the contract becomes permanently unexecutable and all funds held in its storage are frozen forever.

---

### Finding Description

`execute_replace_class` is the OS-level handler for the `replace_class` syscall. Its implementation reads the requested class hash from the syscall request and immediately writes it into the contract's state entry:

```cairo
// Replaces the class.
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
``` [1](#0-0) 

The `// TODO` comment at line 898 is the OS authors' own acknowledgment that the check is absent. No Cairo-level assertion verifies that `class_hash` exists in `contract_class_changes` (the dictionary of declared classes). The OS simply trusts the caller-supplied felt.

Compare this with the normal declare flow, where `execute_declare_transaction` enforces `prev_value=0` and `assert_not_zero(compiled_class_hash)` before writing to `contract_class_changes`: [2](#0-1) 

`replace_class` has no equivalent guard.

---

### Impact Explanation

Once `contract_state_changes` records an undeclared class hash for a contract address, every subsequent call to that contract will attempt to look up and execute a class that does not exist in the declared-class set. Execution will fail unconditionally. Because the StarkNet state is append-only and the OS provides no recovery path, any ERC-20 tokens, ETH, or other assets held in the contract's storage are permanently inaccessible — a **critical permanent freezing of funds**.

---

### Likelihood Explanation

The `replace_class` syscall is callable by any contract during normal execution. An unprivileged user can:

1. Deploy a contract (or interact with an existing upgradeable contract whose upgrade path is insufficiently guarded).
2. Trigger a `replace_class` call supplying an arbitrary felt (e.g., `0x1`, a random hash, or a hash of a class that was never declared).
3. The OS handler accepts the value without validation and commits it to state.

No privileged role, leaked key, or operator cooperation is required. The entry path is a standard user-submitted invoke transaction.

---

### Recommendation

Inside `execute_replace_class`, before writing the new class hash, assert that it is present in `contract_class_changes` (i.e., that it was previously declared). Concretely, perform a `dict_read` on `contract_class_changes` keyed by `class_hash` and assert the returned compiled-class hash is non-zero:

```cairo
// Verify the class has been declared.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
assert_not_zero(compiled_class_hash);
```

This mirrors the protection already present in the declare transaction path and closes the gap the TODO comment identifies.

---

### Proof of Concept

1. **Deploy** a contract `C` that holds user funds and exposes an `upgrade(new_class_hash: felt)` entry point that calls `replace_class(new_class_hash)`.
2. **Submit** an invoke transaction calling `C.upgrade(0xdeadbeef)` where `0xdeadbeef` is never declared.
3. The OS executes `execute_replace_class`:
   - Reads `class_hash = 0xdeadbeef` from the syscall request.
   - Skips any declared-class check (the TODO line).
   - Writes `StateEntry(class_hash=0xdeadbeef, ...)` into `contract_state_changes`.
4. State is committed. `C`'s class hash is now `0xdeadbeef`.
5. Any subsequent call to `C` fails at class-lookup time — no entry points are reachable.
6. All funds in `C`'s storage are permanently frozen. [3](#0-2)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L815-819)
```text
    // Note that prev_value=0 enforces that a class may be declared only once.
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```
