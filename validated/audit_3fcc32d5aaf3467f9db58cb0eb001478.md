### Title
Missing Declared Class Hash Validation in `execute_replace_class` Enables Permanent Contract Freezing - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS does not verify that the replacement class hash has been declared before updating a contract's class. An unprivileged transaction sender can invoke `replace_class` with an arbitrary, undeclared class hash, permanently freezing the contract and any funds it holds. The missing check is explicitly acknowledged in the code with a TODO comment.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function processes the `replace_class` syscall. After deducting gas, it reads the requested `class_hash` from the syscall request and directly updates the contract's `StateEntry` in `contract_state_changes` — without verifying that the class hash corresponds to any entry in `contract_class_changes` (i.e., a previously declared class).

The relevant code:

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
``` [1](#0-0) 

By contrast, the `execute_declare_transaction` function enforces `prev_value=0` to ensure a class is declared only once, and `finalize_class_hash` verifies the Sierra class hash pre-image. No equivalent guard exists in `execute_replace_class`. [2](#0-1) 

The `deploy_contract` function similarly enforces `UNINITIALIZED_CLASS_HASH` as a precondition, showing the pattern of class-hash integrity checks that `execute_replace_class` omits. [3](#0-2) 

---

### Impact Explanation

If a contract's `class_hash` is set to an undeclared felt value:

1. The OS state update permanently records the invalid class hash in the Patricia commitment tree.
2. Every subsequent call to the contract attempts to look up and execute the class with that hash.
3. No such class exists in the compiled class facts bundle; execution fails unconditionally.
4. The contract is permanently bricked — all tokens (STRK, ERC-20, NFTs) held in its storage are irrecoverably frozen.

This matches **Critical — Permanent freezing of funds**.

---

### Likelihood Explanation

- **Direct path**: Any contract whose upgrade/migration logic does not gate `replace_class` behind strict owner checks can be triggered by an unprivileged caller to supply an arbitrary hash. This is a common pattern in upgradeable proxy contracts.
- **Self-inflicted path**: A contract developer who mistakenly passes an undeclared hash (e.g., a hash computed off-chain before the class is declared on-chain) permanently freezes their own contract and its funds.
- The TODO comment at line 898 confirms the developers are aware the check is absent, meaning the window of exposure is open until the fix is shipped.

---

### Recommendation

Inside `execute_replace_class`, after reading `class_hash` from the request, add a lookup into `contract_class_changes` to assert the hash maps to a non-zero compiled class hash (i.e., it has been declared). Concretely:

```cairo
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
assert_not_zero(compiled_class_hash);
```

This mirrors the invariant enforced by `execute_declare_transaction` (`prev_value=0` / `assert_not_zero(compiled_class_hash)`) and closes the gap.

---

### Proof of Concept

1. Attacker identifies a target contract `C` holding funds, whose upgrade function calls `replace_class(new_class_hash)` with caller-supplied input and insufficient access control.
2. Attacker submits an `invoke` transaction calling `C.upgrade(0xdeadbeef)` where `0xdeadbeef` is never declared.
3. The OS executes `execute_replace_class`:
   - Gas is deducted successfully.
   - `class_hash = 0xdeadbeef` is written into `C`'s `StateEntry` with no validation.
   - `dict_update` commits the change to `contract_state_changes`.
4. The state update is committed to the Patricia tree; `C.class_hash = 0xdeadbeef` is now canonical.
5. Any subsequent `call_contract` or `invoke` targeting `C` fails at class lookup — no CASM exists for `0xdeadbeef`.
6. All funds in `C`'s storage are permanently inaccessible.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L815-819)
```text
    // Note that prev_value=0 enforces that a class may be declared only once.
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deploy_contract.cairo (L51-53)
```text
    local state_entry: StateEntry*;
    %{ GetContractAddressStateEntry %}
    assert state_entry.class_hash = UNINITIALIZED_CLASS_HASH;
```
