### Title
Missing Class-Existence Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS program accepts any arbitrary class hash without verifying that the hash corresponds to a previously declared class. A contract can therefore call `replace_class` with an undeclared class hash, the OS silently accepts the state update, and the contract becomes permanently inaccessible — permanently freezing any funds it holds. This is a direct analog to the reported Turnstile false-positive: an operation appears to succeed at one layer while the intended effect is silently absent, with no recovery path.

---

### Finding Description

`execute_replace_class` in `syscall_impls.cairo` processes the `REPLACE_CLASS_SELECTOR` syscall. The function reads the new class hash from the syscall request and unconditionally writes it into `contract_state_changes`:

```cairo
func execute_replace_class{
    range_check_ptr,
    syscall_ptr: felt*,
    contract_state_changes: DictAccess*,
    revert_log: RevertLogEntry*,
}(contract_address: felt) {
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

The in-code TODO explicitly acknowledges the missing check. The function's implicit argument list does **not** include `contract_class_changes`, so there is no mechanism to verify that `class_hash` has a corresponding entry in the declared-class tree. [1](#0-0) 

The `execute_replace_class` handler is dispatched from `execute_syscalls` when the selector matches `REPLACE_CLASS_SELECTOR`: [2](#0-1) 

The class tree (`contract_class_changes`) is a separate dict that is only updated when a class is declared via `execute_declare_transaction`: [3](#0-2) 

Because `execute_replace_class` has no access to `contract_class_changes`, it cannot cross-check whether the supplied class hash was ever declared. The OS writes the new (invalid) class hash into the contract state tree and returns a success response (`failure_flag=0`) to the calling contract.

---

### Impact Explanation

After `replace_class` is called with an undeclared class hash:

1. The contract's class hash in the state tree is permanently set to a hash that has no corresponding class in the class tree.
2. Any subsequent call to the contract requires the prover to supply the class for that hash. Because the class was never declared, the prover cannot supply it, and proof generation for any block containing a call to this contract fails.
3. The sequencer must exclude all calls to this contract from future blocks.
4. Any funds (ERC-20 balances, ETH, or other assets) held in the contract's storage are permanently inaccessible — **critical permanent freezing of funds**.
5. The contract cannot call `replace_class` again to recover, because it cannot be called at all.

This matches the allowed impact: **Critical — Permanent freezing of funds**.

---

### Likelihood Explanation

The `replace_class` syscall is a standard upgrade mechanism. Realistic triggering paths include:

- A contract developer performs an upgrade and accidentally supplies a class hash that was never declared (e.g., a hash computed locally but whose `declare` transaction was never submitted or was rejected).
- A contract with a public upgrade function accepts a caller-supplied class hash and passes it directly to `replace_class` without validating it on-chain first.
- A malicious contract intentionally calls `replace_class` with a garbage hash to permanently freeze its own funds (e.g., as part of a rug-pull or griefing attack against users who deposited into it).

The missing check is acknowledged by the development team via the TODO comment, confirming awareness of the gap. Any of the above paths is reachable by an unprivileged transaction sender.

---

### Recommendation

Add `contract_class_changes` as an implicit argument to `execute_replace_class` and verify that the requested class hash has a non-zero compiled class hash entry before updating the contract state. If the class does not exist, write a failure response (set `failure_flag=1`) so the calling contract can observe the error and the transaction can revert, rather than silently committing an invalid state transition.

---

### Proof of Concept

1. **Deploy** a contract `Vault` that holds user funds and exposes an `upgrade(class_hash)` function that calls `replace_class(class_hash)`.
2. **Attacker** (or a developer making a mistake) calls `Vault.upgrade(0xdeadbeef)` where `0xdeadbeef` is a felt that was never declared as a class.
3. The OS executes `execute_replace_class`:
   - Reads `class_hash = 0xdeadbeef` from the syscall request.
   - No check against `contract_class_changes` is performed (the TODO is unimplemented).
   - Writes `StateEntry(class_hash=0xdeadbeef, ...)` into `contract_state_changes`.
   - Returns `ResponseHeader(gas=remaining_gas, failure_flag=0)` — **success**.
4. The transaction is included in a block. The state commitment now records `Vault` with class hash `0xdeadbeef`.
5. Any future transaction that calls `Vault` requires the prover to supply the class for `0xdeadbeef`. The class does not exist; proof generation fails for any block containing such a call.
6. The sequencer permanently excludes `Vault` from future blocks. All funds stored in `Vault`'s storage are permanently frozen with no recovery path. [4](#0-3)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L814-819)
```text
    // Declare the class hash.
    // Note that prev_value=0 enforces that a class may be declared only once.
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```
