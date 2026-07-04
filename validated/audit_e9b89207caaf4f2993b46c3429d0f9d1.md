### Title
Missing Declared Class Validation in `execute_replace_class` Allows Permanent Contract Fund Freezing — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS does not validate that the caller-supplied `class_hash` corresponds to a previously declared class. Any contract can invoke `replace_class` with `class_hash = 0` (`UNINITIALIZED_CLASS_HASH`) or any arbitrary undeclared hash, permanently making itself unexecutable and freezing all funds held within it. The codebase itself acknowledges this gap with an explicit TODO comment.

---

### Finding Description

In `syscall_impls.cairo`, `execute_replace_class` reads the requested `class_hash` directly from the syscall request and writes it into `contract_state_changes` with no check that the hash exists in `contract_class_changes` (the declared-class registry):

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

The same missing validation exists in the deprecated syscall path: [2](#0-1) 

The sentinel value `UNINITIALIZED_CLASS_HASH = 0` is defined in `commitment.cairo` and is the exact value used by `deploy_contract` to assert a slot is empty before deployment: [3](#0-2) [4](#0-3) 

Because `execute_replace_class` performs no analogous inverse check, a contract can write `class_hash = 0` (or any undeclared hash) into its own state entry. Once committed, every future call to that contract will attempt to dispatch to a non-existent class, causing permanent execution failure.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

After `replace_class(0)` is committed to the state tree, the contract's `class_hash` field is `UNINITIALIZED_CLASS_HASH`. All subsequent transactions targeting that contract will fail at class lookup time; no transaction can ever successfully withdraw or transfer assets held in the contract's storage. The state commitment is cryptographically finalized; there is no recovery path.

---

### Likelihood Explanation

The `replace_class` syscall is callable by any deployed contract without any privileged role. A malicious contract (deployed by any unprivileged user) can invoke it in a single transaction. Users who have deposited funds into such a contract (e.g., a vault, escrow, or bridge) become victims without any action on their part. The attacker-controlled entry path is: deploy contract → call `replace_class(0)` → funds frozen.

---

### Recommendation

Before writing the new `class_hash` into `contract_state_changes`, assert that the hash is present in `contract_class_changes` (i.e., it was declared in the current or a prior block). Specifically:

1. Perform a `dict_read` on `contract_class_changes` for `class_hash`.
2. Assert the returned compiled class hash is non-zero (i.e., the class is declared).
3. Additionally assert `class_hash != UNINITIALIZED_CLASS_HASH` (zero-address analog).

Apply the same fix to both `execute_replace_class` in `syscall_impls.cairo` and the deprecated variant in `deprecated_execute_syscalls.cairo`.

---

### Proof of Concept

1. An unprivileged user deploys a contract whose `__execute__` function calls `replace_class(class_hash=0)`.
2. The OS processes the transaction. `execute_replace_class` reads `class_hash = 0` from the request, skips any declared-class check (the TODO is unimplemented), and writes `StateEntry(class_hash=0, ...)` into `contract_state_changes`.
3. The transaction succeeds; the state root is updated with the contract's class hash set to `UNINITIALIZED_CLASS_HASH`.
4. In any subsequent block, a call to this contract causes the OS to look up class hash `0`. No compiled class exists for hash `0`; execution cannot proceed.
5. All ERC-20 balances, ETH, or other assets stored in the contract's storage slots are permanently inaccessible — matching the **Critical. Permanent freezing of funds** impact category.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/commitment.cairo (L16-16)
```text
const UNINITIALIZED_CLASS_HASH = 0;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deploy_contract.cairo (L51-54)
```text
    local state_entry: StateEntry*;
    %{ GetContractAddressStateEntry %}
    assert state_entry.class_hash = UNINITIALIZED_CLASS_HASH;
    assert state_entry.nonce = 0;
```
