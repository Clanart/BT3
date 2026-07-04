### Title
Missing Class Declaration Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` function in the StarkNet OS does not verify that the new class hash supplied via the `replace_class` syscall corresponds to a previously declared class. This is the direct analog of the ERC1967Factory bug: just as the factory allowed anyone to set an arbitrary implementation address without an admin check, the OS allows any contract to set its class hash to an arbitrary, undeclared value without a declaration check. The result is that a contract can be permanently bricked, freezing any funds it holds.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function processes the `replace_class` syscall. After deducting gas, it reads the requested `class_hash` from the syscall request and directly writes it into `contract_state_changes` with no validation that the hash exists in `contract_class_changes` (the dict tracking declared classes). [1](#0-0) 

The critical gap is at line 898, where a developer TODO explicitly acknowledges the missing check:

```cairo
// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.
``` [2](#0-1) 

The function signature does not even receive `contract_class_changes` as an implicit argument, making it structurally impossible to perform the check: [3](#0-2) 

By contrast, the calling context `execute_syscalls` does carry `contract_class_changes` as an implicit argument but does not forward it: [4](#0-3) 

The same missing check exists in the deprecated syscall path: [5](#0-4) 

For comparison, `execute_declare_transaction` correctly enforces that a class can only be declared once by using `prev_value=0` in the `contract_class_changes` dict update: [6](#0-5) 

`execute_replace_class` performs no equivalent lookup into `contract_class_changes`.

---

### Impact Explanation

When a contract calls `replace_class(new_class_hash)` where `new_class_hash` is not a declared class:

1. The OS updates `contract_state_changes` for that contract address, setting `class_hash = new_class_hash`.
2. The proof is valid — the OS accepted the syscall without error.
3. All future calls to the contract will attempt to execute the class identified by `new_class_hash`.
4. Because no class with that hash was ever declared, execution cannot proceed.
5. The contract is permanently non-functional. Any ERC-20 tokens, ETH, or other assets held by the contract are permanently frozen with no recovery path.

**Impact category**: Critical — Permanent freezing of funds.

---

### Likelihood Explanation

The `replace_class` syscall is available to any Cairo 1 contract. The attack requires no special privilege beyond the ability to deploy or interact with a contract:

- **Malicious deployer scenario**: An attacker deploys a contract that accepts user deposits, then calls `replace_class` with an arbitrary undeclared hash (e.g., `1`). The contract is permanently bricked. All deposited funds are frozen. The OS proof is valid and the state transition is accepted by the network.
- **Vulnerable contract scenario**: Any contract that exposes an unguarded `replace_class` call path (e.g., a proxy upgrade function with insufficient access control at the contract level) can be exploited by an unprivileged caller to supply an undeclared hash.

The OS is the trust anchor. Because it does not enforce the class-declaration invariant, no contract-level protection can compensate — the OS will accept any hash.

---

### Recommendation

Pass `contract_class_changes` as an implicit argument to `execute_replace_class` and add an assertion that the requested `class_hash` has a non-zero entry in that dict (i.e., it was previously declared):

```cairo
func execute_replace_class{
    range_check_ptr,
    syscall_ptr: felt*,
    contract_state_changes: DictAccess*,
    contract_class_changes: DictAccess*,  // ADD THIS
    revert_log: RevertLogEntry*,
}(contract_address: felt) {
    ...
    let class_hash = request.class_hash;

    // Verify the class has been declared.
    let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
    assert_not_zero(compiled_class_hash);
    ...
}
```

Apply the same fix to `execute_replace_class` in `deprecated_execute_syscalls.cairo`.

---

### Proof of Concept

1. Attacker deploys contract `VaultAttack` with the following logic:
   - `deposit()`: accepts funds from users.
   - `brick()`: calls `replace_class(1)` — class hash `1` is never declared.

2. Users call `deposit()`, sending funds to `VaultAttack`.

3. Attacker calls `brick()`. The OS executes `execute_replace_class`:
   - Gas is deducted.
   - `class_hash = 1` is read from the request.
   - No check against `contract_class_changes` is performed (the TODO confirms this).
   - `contract_state_changes` is updated: `VaultAttack.class_hash = 1`.
   - Revert log entry is written. Function returns successfully.

4. The block is proven. The state transition is accepted. `VaultAttack` now has `class_hash = 1`.

5. Any subsequent call to `VaultAttack` (e.g., to withdraw funds) causes the OS to look up class `1`, which does not exist in `contract_class_changes`. Execution fails permanently.

6. All user funds in `VaultAttack` are permanently frozen. No recovery is possible. [7](#0-6)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_syscalls.cairo (L80-88)
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
