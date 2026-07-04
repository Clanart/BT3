### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Permanent Freezing of Funds — (File: `execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS does not verify that the supplied class hash corresponds to a previously declared contract class. This is explicitly acknowledged by a TODO comment in the code. A contract deployer can exploit this gap to permanently render a contract non-functional after users have deposited funds, causing irreversible fund freezing — analogous to how the `sweep()` function in the external report lacked a check on the token type, allowing the Treasury to bypass intended restrictions.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function accepts any arbitrary felt value as the new class hash and writes it directly into `contract_state_changes` without checking whether that hash corresponds to a class that has been declared on-chain:

```cairo
func execute_replace_class{...}(contract_address: felt) {
    ...
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
    ...
}
``` [1](#0-0) 

The `contract_class_changes` dictionary (which maps class hashes to compiled class hashes) is never consulted during `execute_replace_class`. The OS enforces that a class hash must be declared before it can be used in `dict_update` for `contract_class_changes` (via `prev_value=0` in `execute_declare_transaction`), but no equivalent guard exists for the class hash written into a contract's `StateEntry` via `replace_class`. [2](#0-1) 

By contrast, the `execute_declare_transaction` path enforces `prev_value=0` to prevent double-declaration, but `execute_replace_class` has no symmetric check that the target class hash exists in `contract_class_changes`.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once a contract's `StateEntry.class_hash` is set to an undeclared hash, every subsequent call to that contract will fail at class resolution time inside the OS execution engine. The contract becomes permanently non-functional. Any ERC-20 balances, vault deposits, or other assets held by the contract are irreversibly frozen — there is no recovery path because the class hash is committed to the global state root. [3](#0-2) 

The state commitment is computed from the squashed `contract_state_changes`, which will include the invalid class hash, making the freeze permanent and provable on L1.

---

### Likelihood Explanation

A contract deployer — explicitly listed as a valid attacker role — can:

1. Deploy a contract that accepts user deposits (e.g., a fake vault or token).
2. Include a backdoor entry point (or trigger it directly) that calls the `replace_class` syscall with an arbitrary, undeclared felt value.
3. After users have deposited funds, invoke the backdoor.
4. The OS accepts the `replace_class` call without validation, commits the invalid class hash to state, and the contract becomes permanently non-functional.

No privileged sequencer access, leaked keys, or network-level attack is required. The attacker only needs to deploy a contract and submit a standard invoke transaction — both are public protocol entry points.

---

### Recommendation

Inside `execute_replace_class`, before writing the new class hash into `contract_state_changes`, add a lookup into `contract_class_changes` to verify that the supplied `class_hash` has a non-zero compiled class hash entry (i.e., it has been declared). This mirrors the invariant already enforced by `execute_declare_transaction` via `prev_value=0` and `assert_not_zero(compiled_class_hash)`. [4](#0-3) 

Concretely: perform a `dict_read` on `contract_class_changes` keyed by `request.class_hash` and assert the result is non-zero before proceeding with the state update.

---

### Proof of Concept

1. **Attacker deploys** `MaliciousVault` (class hash `CLASS_A`) — a contract with a `deposit()` entry point and a hidden `freeze()` entry point that calls `replace_class(0xdeadbeef)`.
2. **Users call** `deposit()`, transferring STRK/ETH into `MaliciousVault`.
3. **Attacker submits** an invoke transaction calling `freeze()` on `MaliciousVault`.
4. **OS executes** `execute_replace_class` with `class_hash = 0xdeadbeef`. No validation is performed. [5](#0-4) 
5. **State is committed**: `MaliciousVault`'s `StateEntry.class_hash = 0xdeadbeef` is written into the Patricia trie and proven on L1.
6. **All subsequent calls** to `MaliciousVault` fail — the OS cannot resolve class `0xdeadbeef` to any compiled class.
7. **User funds are permanently frozen** with no recovery mechanism.

### Citations

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L815-819)
```text
    // Note that prev_value=0 enforces that a class may be declared only once.
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/state.cairo (L70-75)
```text
    let contract_state_tree_update_output = compute_contract_state_commitment(
        contract_state_changes_start=squashed_contract_state_changes_start,
        n_contract_state_changes=n_contract_state_changes,
        patricia_update_constants=patricia_update_constants,
    );

```
