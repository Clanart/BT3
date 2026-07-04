### Title
Missing Declared Class Hash Validation in `execute_replace_class` Enables Permanent Fund Freezing — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall in the StarkNet OS does not verify that the replacement class hash is actually declared in the contract class tree before committing the state change. This is an acknowledged missing check (marked with a TODO). Any contract can call `replace_class` with an arbitrary, undeclared class hash, permanently rendering itself unexecutable and freezing all funds it holds.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function unconditionally writes the caller-supplied `class_hash` into the contract's `StateEntry` without any validation that the hash corresponds to a declared class:

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
```

The TODO comment at line 898 explicitly acknowledges the missing check. The OS accepts any felt value as the new class hash and commits it to state.

The analog to the external report's vulnerability class is direct: just as the old Ethos protocol could still burn LUSD tokens after an upgrade (creating a dual-state where old and new protocol versions coexist with inconsistent rules), here the OS allows a contract to transition into a state where its class hash references a non-existent class — a permanently broken state that the OS has no mechanism to recover from.

When the OS later attempts to execute a call against this contract, it looks up the class hash in the compiled class facts bundle (`CompiledClassFactsBundle` in `block_context.cairo`). If the hash is not present, execution cannot proceed. Unlike a normal revert, this is not recoverable: the contract's state entry permanently holds the invalid class hash, and no future transaction can execute the contract's logic to fix it.

This is structurally identical to the external report's "old system cannot create new Troves" scenario — the contract is stuck in a broken state with no path forward.

---

### Impact Explanation

**Permanent freezing of funds.** Any contract that calls `replace_class` with an undeclared class hash becomes permanently unexecutable. All ERC-20 tokens, ETH, or other assets held in that contract's storage are irrecoverably frozen. There is no OS-level mechanism to recover from this state, since the only way to change the class hash back would be to call `replace_class` again — which requires executing the contract, which is impossible.

---

### Likelihood Explanation

The attack is reachable by any unprivileged contract deployer or user who can trigger a `replace_class` call within a contract they influence. Concrete paths:

1. **Accidental**: A contract developer implements an upgrade mechanism with a bug in the class hash lookup (e.g., off-by-one in an array, or a hash computed incorrectly), causing `replace_class` to be called with an invalid hash. The OS provides no safety net.
2. **Malicious contract owner**: A contract owner (e.g., of a vault or multisig) calls `replace_class` with `0x1` or any random felt, permanently freezing depositors' funds.
3. **Governance exploit**: A contract with a DAO-controlled upgrade mechanism is attacked via a governance exploit, causing `replace_class` to be called with an invalid hash.

The syscall is a standard, documented StarkNet syscall accessible to any Sierra contract. No privileged role is required to call it.

---

### Recommendation

Before committing the state change in `execute_replace_class`, verify that the requested `class_hash` exists in the `contract_class_changes` dictionary (i.e., it has a non-zero compiled class hash entry). This is exactly what the TODO comment at line 898 of `syscall_impls.cairo` calls for. The check should assert that `class_hash` maps to a non-zero compiled class hash in the current block's class changes, or alternatively that it was previously declared in a prior block (verifiable via the class commitment tree).

---

### Proof of Concept

1. Deploy a contract `Vault` that holds user funds and exposes a `replace_class(new_hash: felt)` function callable by the owner.
2. Users deposit funds (e.g., ERC-20 tokens) into `Vault`.
3. The owner (attacker) calls `Vault.replace_class(0x1)` — an arbitrary undeclared felt.
4. The OS executes `execute_replace_class` in `syscall_impls.cairo` (lines 878–916), writes `class_hash=0x1` into `Vault`'s `StateEntry`, and commits the state change without any validation.
5. Any subsequent transaction targeting `Vault` causes the OS to look up class hash `0x1` in the `CompiledClassFactsBundle`. It is not found. The transaction fails at the OS level.
6. The funds in `Vault`'s storage are permanently inaccessible. No recovery path exists within the protocol. [1](#0-0) [2](#0-1) [3](#0-2)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/block_context.cairo (L16-22)
```text
struct CompiledClassFactsBundle {
    n_compiled_class_facts: felt,
    compiled_class_facts: CompiledClassFact*,
    builtin_costs: felt*,
    n_deprecated_compiled_class_facts: felt,
    deprecated_compiled_class_facts: DeprecatedCompiledClassFact*,
}
```
