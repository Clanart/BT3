### Title
Missing Class Validity Check in `execute_replace_class` Allows Permanent Freezing of Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall in the StarkNet OS program does not verify that the new class hash supplied by the caller is an actually-declared contract class. The `execute_declare_transaction` path enforces this invariant (valid Sierra hash, non-zero compiled class hash, declared exactly once), but `execute_replace_class` — the complementary operation — skips the check entirely. An unprivileged contract can therefore set its own class hash to any arbitrary felt value, rendering the contract permanently non-executable and freezing any funds it holds.

---

### Finding Description

**Declare path — invariant enforced:**

In `execute_declare_transaction` (`transaction_impls.cairo`), the OS enforces three properties before accepting a class:

1. The class hash must be the output of `finalize_class_hash`, i.e., a valid Sierra class hash pre-image.
2. `compiled_class_hash` must be non-zero.
3. `dict_update` is called with `prev_value=0`, ensuring a class can only be declared once. [1](#0-0) [2](#0-1) 

**Replace-class path — invariant absent:**

`execute_replace_class` (`syscall_impls.cairo`) reads `class_hash` directly from the syscall request and writes it into `contract_state_changes` with no validation whatsoever. The only acknowledgment of the missing check is a TODO comment:

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
``` [3](#0-2) 

The OS therefore accepts a state transition that sets a contract's class hash to an arbitrary felt — including one that has never been declared and has no corresponding compiled class.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once a contract's class hash is set to an undeclared value, every subsequent call to that contract will fail at class-lookup time inside the OS (the `contract_class_changes` dict will return 0 / the default for the unknown key, and no valid entry point can be dispatched). The contract becomes permanently non-executable. Any ERC-20 tokens, ETH, or other assets held in the contract's storage are irrecoverably locked with no upgrade or recovery path, because the very mechanism that would allow recovery (`replace_class` again, or any entry point) requires a functioning class.

---

### Likelihood Explanation

**Medium.**

The `replace_class` syscall is available to any contract without privilege restrictions. A realistic attack path:

1. An attacker deploys a contract that accepts deposits (e.g., a fake yield vault or escrow).
2. Users deposit funds.
3. The attacker's contract calls `replace_class` with an arbitrary undeclared felt (e.g., `0xdead`).
4. The OS accepts the state transition — the TODO-acknowledged missing check means no revert occurs.
5. The contract is permanently bricked; all deposited funds are frozen.

Additionally, any legitimate contract with a logic bug that allows an external caller to trigger `replace_class` with attacker-controlled calldata is equally vulnerable. The syscall is dispatched through the standard `execute_syscalls` loop with no additional gate. [4](#0-3) 

---

### Recommendation

Before writing the new class hash into `contract_state_changes`, verify that the class hash exists in `contract_class_changes` (i.e., its compiled class hash is non-zero):

```cairo
// After gas reduction succeeds:
let class_hash = request.class_hash;

// Enforce that the target class has been declared.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
with_attr error_message("Class hash is not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

This mirrors the invariant already enforced by `execute_declare_transaction` and closes the asymmetry between the two operations.

---

### Proof of Concept

1. Deploy contract `Vault` that accepts ETH deposits and exposes a `drain_class` entry point.
2. Users deposit funds into `Vault`.
3. Attacker calls `drain_class`, which internally issues the `replace_class` syscall with `class_hash = 0xdeadbeef` (not declared).
4. The OS executes `execute_replace_class`:
   - Gas is deducted.
   - `class_hash = 0xdeadbeef` is written into `contract_state_changes[Vault_address]`.
   - No check against `contract_class_changes` is performed (the TODO-acknowledged gap).
   - The state transition is accepted and included in the proven block.
5. Any subsequent invoke to `Vault` causes the OS to look up `contract_class_changes[0xdeadbeef]`, which returns 0 (undeclared). Entry point dispatch fails; the transaction reverts.
6. All funds in `Vault.storage` are permanently inaccessible. [5](#0-4) [4](#0-3)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L738-743)
```text
        let expected_class_hash = finalize_class_hash(
            contract_class_component_hashes=contract_class_component_hashes
        );
        with_attr error_message("Invalid class hash pre-image.") {
            assert [class_hash_ptr] = expected_class_hash;
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
