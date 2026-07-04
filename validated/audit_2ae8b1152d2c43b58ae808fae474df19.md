### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS accepts any arbitrary class hash as the replacement without verifying that the hash corresponds to a previously declared contract class. An unprivileged contract caller can invoke `replace_class` with an undeclared class hash, permanently corrupting the contract's on-chain class pointer and irreversibly freezing any funds held by that contract.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function processes the `replace_class` syscall. After deducting gas, it reads the requested new class hash directly from the syscall request and writes it into `contract_state_changes` with no check that the hash exists in the declared-class registry:

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

The developer-authored TODO comment at line 898 explicitly acknowledges this missing check. The `contract_class_changes` dictionary (which maps declared class hashes to their compiled class hashes) is never consulted. Any felt value — including one that has never been declared — is accepted as the new class hash and committed to state.

By contrast, the `deploy_contract` path enforces that the class hash used for deployment is one that was loaded into the compiled-class facts bundle and validated post-execution: [2](#0-1) 

No equivalent guard exists for `replace_class`.

---

### Impact Explanation

Once a contract's class hash is overwritten with an undeclared value and the block is committed:

1. Every subsequent call to that contract causes the OS to look up the compiled class for the invalid hash. The lookup fails, and execution reverts.
2. Because the class hash is now invalid, even a follow-up `replace_class` call to restore the original class is impossible — the contract cannot execute at all.
3. Any ERC-20 tokens, ETH, or other assets held in the contract's storage are permanently inaccessible.

This matches the **Critical — Permanent freezing of funds** impact category.

---

### Likelihood Explanation

The attack path requires only that an unprivileged user:

1. Deploy (or control) any contract on StarkNet.
2. Issue a `replace_class` syscall from within that contract, supplying an arbitrary felt as the new class hash.

No privileged role, leaked key, or external dependency is required. The syscall is a standard, publicly accessible operation available to every Cairo contract. The missing validation is unconditional — there is no code path in `execute_replace_class` that performs the check.

A realistic scenario: a malicious actor deploys a contract that appears to be a legitimate vault or DeFi protocol, attracts user deposits, then calls `replace_class` with a random undeclared hash. All deposited funds are permanently frozen.

---

### Recommendation

Before writing the new `StateEntry`, verify that `request.class_hash` is present in `contract_class_changes` (for classes declared within the current block) or in the global compiled-class state (for previously declared classes). The check should mirror the validation already performed during `deploy_contract` and `validate_compiled_class_facts_post_execution`. Specifically, remove the TODO and add a Cairo assertion or hint-backed dict lookup confirming the class hash maps to a non-zero compiled class hash before the `dict_update` is executed.

---

### Proof of Concept

1. Attacker deploys `MaliciousVault` — a contract that accepts deposits and exposes a `drain()` entry point.
2. Users deposit funds into `MaliciousVault`.
3. Attacker calls `drain()`, which internally issues:
   ```
   replace_class(class_hash=0xdeadbeef)   // arbitrary undeclared hash
   ```
4. The OS executes `execute_replace_class`:
   - Gas is deducted.
   - `request.class_hash = 0xdeadbeef` is read.
   - **No check** against `contract_class_changes` or global state is performed.
   - `dict_update` writes `class_hash=0xdeadbeef` into `contract_state_changes` for `MaliciousVault`'s address.
5. The block is proven and committed. `MaliciousVault`'s on-chain class hash is now `0xdeadbeef`.
6. Any subsequent call to `MaliciousVault` (including withdrawal attempts) fails — the OS cannot find a compiled class for `0xdeadbeef`.
7. All user funds are permanently frozen with no recovery path. [3](#0-2)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deploy_contract.cairo (L51-66)
```text
    local state_entry: StateEntry*;
    %{ GetContractAddressStateEntry %}
    assert state_entry.class_hash = UNINITIALIZED_CLASS_HASH;
    assert state_entry.nonce = 0;

    tempvar new_state_entry = new StateEntry(
        class_hash=constructor_execution_context.class_hash,
        storage_ptr=state_entry.storage_ptr,
        nonce=0,
    );

    dict_update{dict_ptr=contract_state_changes}(
        key=contract_address,
        prev_value=cast(state_entry, felt),
        new_value=cast(new_state_entry, felt),
    );
```
