### Title
Missing Declared-Class Validation in `execute_replace_class()` Allows Permanent Contract Bricking — (`File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS does not validate that the new `class_hash` supplied by a contract is an already-declared class. An unprivileged transaction sender can trigger a contract to replace its class with an arbitrary, undeclared hash, permanently bricking the contract and freezing any funds it holds. The missing check is explicitly acknowledged by a TODO comment in the production code.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function (lines 877–916) processes the `replace_class` syscall. After deducting gas, it reads the current `StateEntry` for the calling contract and unconditionally writes the caller-supplied `class_hash` into the state:

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

There is **no assertion** that `class_hash` exists in `contract_class_changes` (the dictionary of declared classes). The OS enforces class existence in the `execute_declare_transaction` path — specifically, `assert_not_zero(compiled_class_hash)` and `dict_update{dict_ptr=contract_class_changes}(key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash)` — but this constraint is entirely absent in the `replace_class` path.

This is the direct analog of the external report: a constraint enforced in one code path (`curatePool` / `execute_declare_transaction`) is not enforced in an alternative path (`replacePool` / `execute_replace_class`).

The vulnerability class is: **state-transition bypass via missing input validation**.

---

### Impact Explanation

If a contract's class hash is replaced with an undeclared hash:

1. The `contract_state_changes` dict records the invalid class hash for that contract address.
2. The state is committed to the Patricia Merkle tree with this invalid class hash.
3. All subsequent calls to the contract attempt to look up the class by hash; since no such class exists, every call fails.
4. Any ERC-20 tokens, ETH, or other assets held in the contract's storage are permanently inaccessible.

**Impact: Critical — Permanent freezing of funds.**

---

### Likelihood Explanation

The attack is reachable by any unprivileged transaction sender who can invoke a contract function that calls `replace_class` with user-controlled input. This is a realistic scenario for:

- Upgradeable contracts whose upgrade function lacks access control (a common pattern).
- Contracts that delegate the new class hash selection to a caller-supplied argument.

The OS is the last line of defense; it should reject an invalid class hash regardless of the calling contract's logic. The TODO comment at line 898 confirms the StarkNet developers themselves identified this as a missing check.

---

### Recommendation

In `execute_replace_class`, before writing the new `StateEntry`, assert that `class_hash` is present in `contract_class_changes` (i.e., it has been declared). Concretely, perform a `dict_read` on `contract_class_changes` keyed by `class_hash` and assert the returned compiled class hash is non-zero:

```cairo
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
with_attr error_message("Class hash is not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

This mirrors the validation already present in `execute_declare_transaction` and closes the bypass.

---

### Proof of Concept

1. **Deploy** a contract `VaultContract` that holds user funds and exposes:
   ```
   func upgrade(new_class_hash: felt) {
       replace_class(new_class_hash);  // no access control
   }
   ```
2. **Submit** an invoke transaction calling `VaultContract.upgrade(0xdeadbeef)` where `0xdeadbeef` is never declared.
3. The OS executes `execute_replace_class`:
   - Gas is deducted successfully.
   - The TODO-guarded check is absent; no validation of `0xdeadbeef` against `contract_class_changes` occurs.
   - `dict_update` writes `class_hash=0xdeadbeef` into `contract_state_changes` for `VaultContract`.
4. `state_update` commits this to the Patricia tree.
5. All subsequent calls to `VaultContract` (including fund withdrawals) fail because `0xdeadbeef` resolves to no compiled class.
6. All funds in `VaultContract` are permanently frozen.

---

**Root cause location:** [1](#0-0) 

**TODO comment confirming the missing check:** [2](#0-1) 

**Contrast: class existence enforced in declare path but not replace path:** [3](#0-2)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L816-819)
```text
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```
