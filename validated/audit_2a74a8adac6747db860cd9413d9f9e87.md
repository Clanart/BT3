### Title
Missing Class Hash Existence Validation in `execute_replace_class` Enables Permanent Freezing of Contract Funds — (File: `execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS does not validate that the caller-supplied class hash corresponds to a declared contract class. Any contract can therefore replace its own class hash with an arbitrary, non-existent value. Once set, all future calls to that contract fail because the OS cannot resolve the class, permanently freezing any funds held in the contract's storage.

---

### Finding Description

In `execute_replace_class`, the function reads `class_hash` directly from the `ReplaceClassRequest` and writes it into `contract_state_changes` with no check that the hash has ever been declared. The code itself contains an explicit TODO acknowledging the missing guard: [1](#0-0) 

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
```

The `contract_class_changes` dict — which tracks declared classes — is never consulted. The OS therefore accepts a state transition that sets a contract's class hash to a value that has no corresponding class body, violating the invariant that every live contract must point to a declared class.

This is the direct analog of the BathToken CEI pattern violation: in that report, the `initialized` flag was set *after* an external call, allowing state to be overwritten with attacker-controlled values before the guard was in place. Here, the guard (class-existence check) is simply absent, allowing the state transition to proceed with an attacker-controlled, invalid class hash.

The `execute_replace_class` function is dispatched from `execute_syscalls` whenever a contract issues a `REPLACE_CLASS_SELECTOR` syscall: [2](#0-1) 

---

### Impact Explanation

Once a contract's class hash is set to a non-existent value, every subsequent call to that contract fails at class resolution. Funds stored in the contract's storage (token balances, ETH equivalents, NFT ownership records) become permanently inaccessible — they cannot be transferred, withdrawn, or recovered by any party. This is **Critical: Permanent freezing of funds**.

---

### Likelihood Explanation

The attack requires only the ability to deploy a contract — an unprivileged operation available to any user. No privileged role, leaked key, or external dependency is needed. The missing check is explicitly acknowledged in the source via the TODO comment, confirming the gap is known and unresolved. The attack path is short and deterministic.

---

### Recommendation

Before writing the new `StateEntry` in `execute_replace_class`, perform a lookup in `contract_class_changes` to confirm that `class_hash` maps to a non-zero `compiled_class_hash`. If the class hash is not found, write a failure response and return without modifying `contract_state_changes`. This mirrors the pattern used in `execute_declare_transaction`, where `prev_value=0` enforces that a class may only be declared once: [3](#0-2) 

---

### Proof of Concept

1. **Attacker deploys `MaliciousVault`** using a legitimately declared class hash `C_valid`. The contract exposes:
   - `deposit()` — accepts user token transfers into its storage.
   - `freeze()` — calls `replace_class(0xDEAD_BEEF_…)` where `0xDEAD_BEEF_…` is a felt that has never been declared as a class.

2. **Users deposit funds** by calling `deposit()`. Balances accumulate in `MaliciousVault`'s storage.

3. **Attacker calls `freeze()`**. The contract issues a `REPLACE_CLASS` syscall with `class_hash = 0xDEAD_BEEF_…`.

4. **`execute_replace_class` runs** (syscall_impls.cairo line 878). It skips the missing existence check and executes:
   ```
   dict_update(contract_state_changes,
       key=MaliciousVault_address,
       prev=StateEntry{class_hash=C_valid, …},
       new=StateEntry{class_hash=0xDEAD_BEEF_…, …})
   ``` [4](#0-3) 

5. **All subsequent calls to `MaliciousVault` fail.** The OS cannot resolve class `0xDEAD_BEEF_…` to any executable code.

6. **User funds are permanently frozen** in `MaliciousVault`'s storage with no recovery path.

### Citations

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L815-819)
```text
    // Note that prev_value=0 enforces that a class may be declared only once.
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```
