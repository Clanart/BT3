### Title
Missing Class Existence Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` function in the StarkNet OS accepts any arbitrary felt value as a new class hash without verifying that the hash corresponds to a declared contract class. An unprivileged contract can call this syscall with a non-existent class hash, causing the OS to commit an invalid class hash to the contract's state entry. Any funds held in that contract become permanently inaccessible.

---

### Finding Description

In `syscall_impls.cairo`, `execute_replace_class` handles the `REPLACE_CLASS_SELECTOR` syscall. After deducting gas, it reads `request.class_hash` and writes it directly into the contract's `StateEntry` with no lookup or assertion against `contract_class_changes`:

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

The TODO comment is the developer's own acknowledgment that this check is absent. There is no `dict_read` on `contract_class_changes`, no `assert_not_zero`, and no range check that would constrain `class_hash` to a value that was previously declared via `execute_declare_transaction`. Any felt value — including `0xdeadbeef` or any random value — is accepted and committed to the global state.

Compare this to `execute_declare_transaction`, which enforces `prev_value=0` and `assert_not_zero(compiled_class_hash)` before writing to `contract_class_changes`: [2](#0-1) 

No equivalent guard exists in `execute_replace_class`.

The `REPLACE_CLASS_SELECTOR` is dispatched unconditionally from `execute_syscalls` for any calling contract: [3](#0-2) 

---

### Impact Explanation

Once the OS commits a non-existent class hash to a contract's `StateEntry`, every subsequent call to that contract will fail at the OS level: the class lookup will find no matching entry in the class tree, making the contract permanently non-executable. Any ERC-20 balances, NFTs, or protocol-controlled funds stored in that contract's storage become permanently frozen with no recovery path. This matches **Critical — Permanent freezing of funds**.

---

### Likelihood Explanation

The `replace_class` syscall is available to every deployed contract with no privilege requirement beyond gas. A realistic attack path:

1. A legitimate upgradeable contract (e.g., a DeFi vault) exposes a `replace_class`-based upgrade function with insufficient access control — a common pattern.
2. An attacker calls that upgrade function supplying an arbitrary felt (e.g., `0x1`) that was never declared.
3. The OS, lacking the class-existence check, commits the invalid class hash to the state.
4. All user funds in the vault are permanently frozen.

Even without a vulnerable upgrade function, an attacker who deploys a contract holding third-party funds (e.g., a shared escrow) can self-destruct it by calling `replace_class` with an invalid hash, freezing depositors' funds.

---

### Recommendation

Before writing the new class hash to `contract_state_changes`, add a lookup into `contract_class_changes` to assert the class was previously declared:

```cairo
// Verify the class hash is declared.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
assert_not_zero(compiled_class_hash);
```

This mirrors the invariant already enforced in `execute_declare_transaction` and closes the gap identified by the TODO comment.

---

### Proof of Concept

1. Attacker deploys `VaultContract` (holds user STRK deposits) with an `upgrade(new_class: felt)` entry point that calls `replace_class(new_class)` with no access control.
2. Attacker calls `VaultContract.upgrade(0xdeadbeef)` where `0xdeadbeef` is not a declared class.
3. The OS dispatches `REPLACE_CLASS_SELECTOR` → `execute_replace_class`.
4. `execute_replace_class` reads `class_hash = 0xdeadbeef`, skips any existence check (the TODO), and calls `dict_update` on `contract_state_changes` setting `VaultContract.class_hash = 0xdeadbeef`.
5. The block is proven and the state root is updated with `VaultContract.class_hash = 0xdeadbeef`.
6. Any subsequent `call_contract` or `invoke` targeting `VaultContract` fails: the OS cannot find a class entry for `0xdeadbeef` in the class tree.
7. All depositor funds in `VaultContract`'s storage are permanently inaccessible — no withdrawal, no migration, no recovery.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L896-915)
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

    return ();
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L815-819)
```text
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
