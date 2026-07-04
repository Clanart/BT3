### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Permanent Contract Freezing — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS does not verify that the new class hash supplied by a contract corresponds to a previously declared class. Any contract can invoke `replace_class` with an arbitrary, undeclared class hash. The OS accepts and commits this state change, permanently rendering the contract uncallable and freezing all funds it holds.

---

### Finding Description

In `syscall_impls.cairo`, `execute_replace_class` (lines 877–916) reads the requested class hash directly from the syscall request and writes it into `contract_state_changes` without consulting `contract_class_changes` to confirm the hash was ever declared:

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

The in-code TODO explicitly acknowledges the missing check. The same omission exists in the deprecated path at `deprecated_execute_syscalls.cairo` lines 307–329, which also writes an arbitrary `class_hash` from `syscall_ptr.class_hash` directly into `contract_state_changes` with no existence check.

By contrast, `execute_declare_transaction` in `transaction_impls.cairo` (lines 814–819) enforces the invariant that a class may be declared only once by requiring `prev_value=0` in `contract_class_changes`:

```cairo
// Note that prev_value=0 enforces that a class may be declared only once.
assert_not_zero(compiled_class_hash);
dict_update{dict_ptr=contract_class_changes}(
    key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
);
```

`execute_replace_class` never reads from `contract_class_changes` to verify the target class hash exists there. The OS therefore accepts and proves a state transition where a contract's class hash is set to a value with no corresponding declared class.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once the OS commits a state root containing a contract whose class hash does not correspond to any declared class, every subsequent call to that contract will fail at class-lookup time. No entry point can be executed, no withdrawal or transfer function can be called, and no upgrade path exists. All tokens or native assets held in the contract's storage are permanently inaccessible. Because the state root is proven and finalized on L1, the freeze is irreversible without a protocol-level upgrade.

---

### Likelihood Explanation

The `replace_class` syscall is callable by any contract from within its own execution context — no privileged role is required. A malicious contract (e.g., one masquerading as a vault or escrow) can call `replace_class` with an arbitrary felt value as the class hash immediately after users deposit funds. The attacker needs only to deploy a contract and submit a valid invoke transaction; no leaked keys, operator access, or external dependency is involved. The path is fully reachable by an unprivileged transaction sender.

---

### Recommendation

Before writing the new class hash into `contract_state_changes`, `execute_replace_class` must verify that the requested class hash exists in `contract_class_changes` (i.e., its compiled class hash entry is non-zero). Concretely, perform a `dict_read` on `contract_class_changes` keyed by `class_hash` and assert the returned value is non-zero, mirroring the invariant already enforced during declaration. The same fix must be applied to the deprecated path in `deprecated_execute_syscalls.cairo`.

---

### Proof of Concept

1. Attacker deploys contract `V` (a fake vault) that accepts user deposits.
2. Users deposit tokens into `V`; `V`'s storage now holds balances.
3. Attacker submits an invoke transaction calling `V`'s internal function, which issues the `replace_class` syscall with `class_hash = 0xdeadbeef` (never declared).
4. The OS executes `execute_replace_class`:
   - Reads `state_entry` for `V`. [1](#0-0) 
   - Writes `new StateEntry(class_hash=0xdeadbeef, ...)` into `contract_state_changes`. [2](#0-1) 
   - No check against `contract_class_changes` is performed. [3](#0-2) 
5. The block is proven; the state root now records `V.class_hash = 0xdeadbeef`.
6. All future calls to `V` fail — class `0xdeadbeef` does not exist in the class tree. [4](#0-3) 
7. User funds in `V` are permanently frozen.

The same attack path applies through the deprecated syscall handler. [5](#0-4)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L898-898)
```text
    // TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L899-900)
```text
    local state_entry: StateEntry*;
    %{ GetContractAddressStateEntry %}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L902-910)
```text
    tempvar new_state_entry = new StateEntry(
        class_hash=class_hash, storage_ptr=state_entry.storage_ptr, nonce=state_entry.nonce
    );

    dict_update{dict_ptr=contract_state_changes}(
        key=contract_address,
        prev_value=cast(state_entry, felt),
        new_value=cast(new_state_entry, felt),
    );
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
