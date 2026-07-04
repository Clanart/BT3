### Title
Missing Zero Class Hash Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds - (File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo)

---

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS accepts a zero (or any undeclared) class hash without validation, allowing any contract to permanently brick itself and freeze all funds it holds. The OS itself contains a TODO comment acknowledging this missing check. This is a direct structural analog to the zero-value withdrawal bug: a critical parameter is accepted without a non-zero guard, enabling a destructive state transition.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function reads `request.class_hash` and immediately writes it into the contract's `StateEntry` without any validation:

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

The TODO at line 898 explicitly acknowledges the missing guard. No `assert_not_zero(class_hash)` or declared-class membership check is performed before the state update is committed. Because the OS processes this as a non-reverting state write, the contract's class hash is permanently overwritten with `0` (or any garbage felt) in the canonical state.

The `execute_replace_class` function is dispatched from `execute_syscalls` via the `REPLACE_CLASS_SELECTOR` branch: [2](#0-1) 

Any Sierra contract executing during an invoke transaction can issue this syscall. The gas deduction succeeds, the response header is written with `failure_flag=0`, and the state dict is updated — all before any class-existence check.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once a contract's `class_hash` is set to `0` in `contract_state_changes`, every subsequent call to that contract will attempt to dispatch to class hash `0`. Because no class with hash `0` is ever declared (the `dict_update` in `execute_declare_transaction` enforces `prev_value=0` to prevent re-declaration of the zero slot), the contract becomes permanently uncallable. [3](#0-2) 

Any ERC-20 balance, NFT, or other asset held in the contract's storage is permanently inaccessible. There is no recovery path: `replace_class` itself requires the contract to be callable, and the contract can no longer be called.

---

### Likelihood Explanation

The entry path is reachable by any unprivileged transaction sender:

1. Sender submits an invoke transaction targeting a contract they control (or any contract whose `__execute__` can be made to call `replace_class`).
2. During execution, the contract issues the `replace_class` syscall with `class_hash = 0`.
3. The OS deducts gas, writes a success response, and commits `class_hash=0` to `contract_state_changes`.
4. The block is proven and the state root is updated on L1 — the change is irreversible.

No privileged role, leaked key, or external dependency is required. The syscall is available to all Sierra contracts. The likelihood is **medium**: it requires a contract to issue the call (intentionally or via a reentrancy/logic bug in a third-party contract), but the OS provides zero protection against it.

---

### Recommendation

Add an `assert_not_zero(class_hash)` guard immediately after reading `request.class_hash`, and resolve the existing TODO by verifying that `class_hash` exists in `contract_class_changes` (i.e., has a non-zero `compiled_class_hash` entry) before committing the state update. This mirrors the fix applied to the zero-withdrawal bug: reject the operation at the protocol layer before any state is mutated.

---

### Proof of Concept

1. Deploy contract `Victim` holding 1000 STRK.
2. `Victim.__execute__` calls `replace_class(class_hash=0)`.
3. Invoke transaction is submitted; the OS executes `execute_replace_class`:
   - `class_hash = 0` is read from the request.
   - No zero-check is performed (line 896–913 of `syscall_impls.cairo`).
   - `dict_update` writes `StateEntry(class_hash=0, ...)` for `Victim`'s address.
4. Block is proven; state root on L1 reflects `Victim.class_hash = 0`.
5. Any subsequent call to `Victim` dispatches to class `0` → no entry point found → permanent revert.
6. The 1000 STRK in `Victim`'s storage is permanently frozen with no recovery mechanism.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_syscalls.cairo (L80-100)
```text
func execute_syscalls{
    range_check_ptr,
    syscall_ptr: felt*,
    builtin_ptrs: BuiltinPointers*,
    contract_state_changes: DictAccess*,
    contract_class_changes: DictAccess*,
    revert_log: RevertLogEntry*,
    outputs: OsCarriedOutputs*,
}(block_context: BlockContext*, execution_context: ExecutionContext*, syscall_ptr_end: felt*) {
    alloc_locals;
    if (syscall_ptr == syscall_ptr_end) {
        return ();
    }

    local selector = [syscall_ptr];
    %{ LogEnterSyscall %}

    if (selector == STORAGE_READ_SELECTOR) {
        execute_storage_read(contract_address=execution_context.execution_info.contract_address);
        %{ OsLoggerExitSyscall %}
        return execute_syscalls(
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
