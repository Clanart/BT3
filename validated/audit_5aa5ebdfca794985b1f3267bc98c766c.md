### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Permanent Fund Freezing — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS omits a critical validation step that was explicitly acknowledged as required via a TODO comment: it does not verify that the new class hash supplied by a contract actually corresponds to a previously declared class. An unprivileged actor can exploit this to permanently freeze funds held by any contract that exposes `replace_class` with caller-influenced input.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function processes the `replace_class` syscall, which allows a contract to swap its own class hash for a new one. The implementation reads the new class hash directly from the syscall request and writes it into `contract_state_changes` without any check that the hash exists in `contract_class_changes` (i.e., that it was previously declared):

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

The TODO comment at line 898 explicitly documents that this check was intended but was never implemented — a direct analog to the external report's "missing functionality" class. The OS accepts and commits any arbitrary felt value as the new class hash.

Once the state is committed, any subsequent call to the affected contract will attempt to look up the class by its (now invalid) hash. Because no class with that hash was ever declared, the OS cannot find the corresponding compiled class, and all future executions of the contract will fail. The contract's storage and any funds it holds become permanently inaccessible.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Any contract that holds funds (e.g., a multisig wallet, a DeFi vault, or any account contract) and whose `replace_class` path can be reached with attacker-influenced input will have its funds permanently frozen. The state update is committed to the proven state root; there is no recovery mechanism once an undeclared class hash is written.

---

### Likelihood Explanation

**Medium.**

The attack requires a contract that:
1. Calls `replace_class` with a value that is partially or fully attacker-controlled (e.g., passed as calldata, read from storage written by the attacker, or derived from attacker-supplied parameters).
2. Holds funds.

This is a realistic scenario for upgradeable contracts, proxy patterns, or any contract that delegates class-upgrade authority to a role that an attacker can impersonate or influence. The missing check is reachable by any unprivileged transaction sender who can trigger such a contract path.

---

### Recommendation

Inside `execute_replace_class`, after reading `class_hash` from the request, add a lookup into `contract_class_changes` (or the equivalent declared-class registry) to assert that `class_hash` maps to a non-zero compiled class hash. This mirrors the enforcement already present for `declare` transactions, where `prev_value=0` is asserted to guarantee a class is declared at most once. The check should be:

```cairo
// Assert that the new class hash has been declared.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
assert_not_zero(compiled_class_hash);
```

---

### Proof of Concept

1. Declare class `A` (valid, compiled class hash `H_A`).
2. Deploy contract `C` using class `A`; fund it with tokens.
3. From within `C` (or by triggering `C`'s upgrade path), call `replace_class` with `class_hash = 0xDEAD` (never declared).
4. The OS executes `execute_replace_class`:
   - Reads `class_hash = 0xDEAD` from the syscall request.
   - Skips the missing declared-class check (TODO at line 898).
   - Writes `StateEntry(class_hash=0xDEAD, ...)` into `contract_state_changes`.
5. The block is proven and the new state root is committed on L1.
6. Any subsequent invoke targeting `C` causes the OS to look up class `0xDEAD` in the compiled class facts — it does not exist — and execution fails permanently.
7. All funds in `C` are frozen with no recovery path. [1](#0-0)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L896-916)
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
}
```
