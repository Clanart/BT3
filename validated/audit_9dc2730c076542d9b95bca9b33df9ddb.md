### Title
Missing Class Hash Validation in `execute_replace_class` Allows Permanent Contract Bricking - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

### Summary

The StarkNet OS `execute_replace_class` syscall handler accepts any arbitrary felt value as a replacement class hash without verifying that the hash corresponds to a declared, compiled class. This is structurally analogous to the LootBox unprotected `selfdestruct`: just as anyone could destroy the LootBox implementation making all proxies non-functional, any contract can permanently replace its own class hash with a non-existent one, making itself permanently non-callable and freezing all funds it holds.

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function processes the `replace_class` syscall. It reads `request.class_hash` directly from the syscall request buffer — which is fully attacker-controlled — and writes it into `contract_state_changes` without any validation that the hash corresponds to a declared class: [1](#0-0) 

The code itself contains an explicit acknowledgment of the missing check: [2](#0-1) 

```
// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.
```

After the invalid class hash is committed to state, any subsequent call to that contract reaches `execute_entry_point`, which performs: [3](#0-2) 

1. `dict_read` on `contract_class_changes` for the (now-invalid) class hash — returns `0` if undeclared.
2. `find_element` searching the compiled class facts bundle for a compiled class with hash `0` — which does not exist.

This causes the OS to be unable to produce a valid proof for any block containing a call to the bricked contract, or the call simply fails with an unrecoverable error, permanently locking any funds held by the contract.

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once a contract's class hash is replaced with an undeclared value and the state is committed on-chain, the contract becomes permanently non-callable. There is no recovery path: `replace_class` cannot be called again because the contract's entry point dispatch itself fails. Any ERC-20 tokens, ETH bridged funds, or other assets held in the contract's storage are permanently inaccessible.

### Likelihood Explanation

**Medium.** The attack surface is real and reachable:

1. **Direct self-bricking**: Any contract that exposes a public or permissionless function wrapping `replace_class_syscall` (e.g., an upgradeable proxy with missing access control — the exact pattern from the LootBox report) can be called by an unprivileged user with `class_hash = 0` or any non-existent hash.
2. **Malicious contract**: An attacker deploys a contract that collects user deposits, then calls `replace_class(0)` to permanently freeze the collected funds.
3. **Accidental bug**: A contract with a bug in its upgrade logic passes an unvalidated caller-supplied class hash to `replace_class_syscall`; the OS provides no safety net.

The OS is the last line of defense. The missing validation means the OS commits an invalid state transition that is irreversible.

### Recommendation

In `execute_replace_class`, before writing the new class hash to `contract_state_changes`, verify that `request.class_hash` exists in `contract_class_changes` (i.e., it has been declared in the current or a prior block). The check should mirror the lookup already performed in `execute_entry_point`:

```cairo
// Verify the new class hash is a declared class.
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=request.class_hash
);
assert_not_zero(compiled_class_hash);  // 0 means undeclared.
```

This is exactly what the existing TODO comment calls for and what the `execute_entry_point` path already assumes is guaranteed.

### Proof of Concept

1. Attacker deploys contract `VaultProxy` with a public `upgrade(new_class: felt)` function that calls `replace_class_syscall(new_class)` with no access control.
2. Users deposit funds into `VaultProxy`.
3. Attacker calls `VaultProxy.upgrade(0)`.
4. The OS `execute_replace_class` handler accepts `class_hash = 0` without validation and commits it to `contract_state_changes`.
5. The state diff is proven and finalized on L1.
6. Any subsequent `call_contract` to `VaultProxy` reaches `execute_entry_point`, which calls `dict_read(contract_class_changes, key=0)` → returns `0`, then `find_element(..., key=0)` → fails to find a compiled class.
7. All calls to `VaultProxy` are permanently unexecutable; all deposited funds are frozen forever. [4](#0-3) [5](#0-4)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L154-167)
```text
    let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
        key=execution_context.class_hash
    );

    // The key must be at offset 0.
    static_assert CompiledClassFact.hash == 0;
    let compiled_class_facts_bundle = block_context.os_global_context.compiled_class_facts_bundle;
    let (compiled_class_fact: CompiledClassFact*) = find_element(
        array_ptr=compiled_class_facts_bundle.compiled_class_facts,
        elm_size=CompiledClassFact.SIZE,
        n_elms=compiled_class_facts_bundle.n_compiled_class_facts,
        key=compiled_class_hash,
    );
    local compiled_class: CompiledClass* = compiled_class_fact.compiled_class;
```
