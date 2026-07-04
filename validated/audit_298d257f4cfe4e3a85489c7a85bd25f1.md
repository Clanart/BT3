### Title
Missing Declared-Class Existence Check in `execute_replace_class` Allows Permanent Contract Freezing — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS does not verify that the new class hash supplied by the caller is a previously declared class. The OS unconditionally writes the caller-supplied hash into `contract_state_changes`, mirroring the M-03 pattern of overwriting state without an existence guard. Any contract can invoke `replace_class` with an arbitrary, undeclared class hash, permanently rendering itself (and all funds it holds) inaccessible.

---

### Finding Description

`execute_replace_class` in `syscall_impls.cairo` reads the caller-supplied `class_hash` from the syscall request and immediately writes it into the contract state dictionary with no check against `contract_class_changes` (the authoritative record of declared classes):

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

The developer-acknowledged TODO on line 898 confirms the check is intentionally absent and deferred. The identical omission exists in the deprecated path: [2](#0-1) 

By contrast, every other state-writing operation in the OS enforces an existence invariant before committing. `deploy_contract` asserts `state_entry.class_hash = UNINITIALIZED_CLASS_HASH` before writing: [3](#0-2) 

And `execute_declare_transaction` uses `prev_value=0` in `dict_update` to enforce single-declaration: [4](#0-3) 

`execute_replace_class` has no analogous guard.

---

### Impact Explanation

Once a contract's `class_hash` field in `contract_state_changes` is set to an undeclared hash, the OS has no class bytecode to execute for that contract address. Every subsequent call to the contract will fail at the class-lookup stage. Any ERC-20 tokens, ETH, or other assets held in the contract's storage become permanently inaccessible — a **Critical: Permanent Freezing of Funds** impact. The corrupted state is committed to the global state root and propagated to L1, making it irreversible without a protocol-level upgrade.

---

### Likelihood Explanation

The `replace_class` syscall is callable by any executing contract without privileged access. An attacker who controls a contract (e.g., a deployed wallet, escrow, or token contract) can call `replace_class` with `class_hash = 0xdeadbeef` (or any non-declared felt) in a single transaction. No special role, leaked key, or operator cooperation is required. The entry path is fully unprivileged and reachable from any standard transaction type (invoke v1/v3).

---

### Recommendation

Before writing the new class hash into `contract_state_changes`, verify that it exists in `contract_class_changes` (or the pre-existing class trie). Concretely, perform a `dict_read` on `contract_class_changes` for the supplied `class_hash` and assert the returned compiled class hash is non-zero (i.e., the class has been declared). This mirrors the `prev_value=0` guard already used in `execute_declare_transaction` and the `UNINITIALIZED_CLASS_HASH` guard in `deploy_contract`.

---

### Proof of Concept

1. Attacker deploys contract `C` holding 1000 STRK.
2. `C` exposes a public function `freeze()` that calls the `replace_class` syscall with `class_hash = 1` (not declared).
3. Attacker sends an invoke transaction calling `C.freeze()`.
4. The OS executes `execute_replace_class`: reads `request.class_hash = 1`, skips any existence check (line 898 TODO), and calls `dict_update` setting `C`'s `class_hash` to `1`.
5. The block is proven and the state root is updated on L1 with `C.class_hash = 1`.
6. Any subsequent call to `C` fails: the OS cannot find a compiled class for hash `1`.
7. The 1000 STRK in `C`'s storage are permanently frozen. [5](#0-4)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deploy_contract.cairo (L51-54)
```text
    local state_entry: StateEntry*;
    %{ GetContractAddressStateEntry %}
    assert state_entry.class_hash = UNINITIALIZED_CLASS_HASH;
    assert state_entry.nonce = 0;
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
