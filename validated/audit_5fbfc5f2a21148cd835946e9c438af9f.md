### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS accepts any arbitrary class hash from a contract without verifying that the hash corresponds to a previously declared class. An unprivileged contract deployer can exploit this to permanently freeze all funds held in any contract they control by replacing its class with an undeclared hash, rendering the contract permanently uncallable.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function processes the `replace_class` syscall. After deducting gas, it reads the requested new `class_hash` directly from the syscall request and immediately writes it into `contract_state_changes` with no validation that the class hash was ever declared on-chain:

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

The TODO comment at line 898 explicitly acknowledges the missing check. The syscall succeeds and the state is committed with the arbitrary class hash.

When any subsequent transaction attempts to call this contract, `execute_entry_point` resolves the class hash through two steps:

1. `dict_read{dict_ptr=contract_class_changes}(key=execution_context.class_hash)` — for an undeclared class hash, this returns `0` (the default dict value, since no `declare` transaction ever wrote an entry for it).
2. `find_element(... key=compiled_class_hash)` — called with `compiled_class_hash=0`, which is not present in the OS's `compiled_class_facts_bundle`, causing a hard Cairo proof failure. [2](#0-1) 

The contract becomes permanently uncallable. No recovery path exists: the contract cannot call `replace_class` again because it cannot execute at all after the state is committed.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Any funds (ERC-20 balances, ETH, or other assets) held in the storage of the affected contract are permanently inaccessible. The contract address is live in state but its class hash points to a non-existent compiled class, making every future call to it unprovable. There is no admin override or recovery mechanism in the OS for this condition.

---

### Likelihood Explanation

**High.** The attack requires only:
1. Deploying a contract (permissionless on StarkNet).
2. Calling a function on that contract that internally invokes the `replace_class` syscall with an arbitrary felt value as the class hash.

No privileged role, leaked key, or operator cooperation is needed. The `replace_class` syscall is a standard Sierra libfunc available to any Cairo 1 contract. The attacker can target their own contract (self-griefing to lock deposited funds) or, if a shared contract exposes a callable path to `replace_class`, target third-party contracts. The missing check is explicitly flagged in the source with a TODO, confirming it is a known gap in the current implementation.

---

### Recommendation

Before writing the new class hash into `contract_state_changes`, assert that the class hash exists in `contract_class_changes` (i.e., it was previously declared via a `declare` transaction). Concretely, perform a `dict_read` on `contract_class_changes` with the new `class_hash` as the key and assert the returned `compiled_class_hash` is non-zero:

```cairo
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
with_attr error_message("Class hash is not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

This mirrors the validation already performed implicitly in `execute_entry_point` and makes it an explicit, enforced constraint at the point of class replacement.

---

### Proof of Concept

1. **Deploy** a Cairo 1 contract `VictimVault` that holds user funds and exposes a function `lock()` which calls the `replace_class` Sierra libfunc with a hardcoded arbitrary felt (e.g., `0xdeadbeef`) as the new class hash.

2. **Deposit** funds into `VictimVault` (e.g., via ERC-20 `transfer`).

3. **Invoke** `VictimVault::lock()`. The OS processes `execute_replace_class`:
   - Gas is deducted.
   - `class_hash = 0xdeadbeef` is written into `contract_state_changes` for `VictimVault`'s address.
   - No validation occurs. The syscall returns success.
   - The transaction is included in a proven block.

4. **Attempt** any subsequent call to `VictimVault` (e.g., `withdraw()`):
   - `execute_entry_point` reads `class_hash = 0xdeadbeef` from state.
   - `dict_read{dict_ptr=contract_class_changes}(key=0xdeadbeef)` returns `0`.
   - `find_element(... key=0)` fails — no compiled class with hash `0` exists.
   - The block containing this call cannot be proven.
   - The sequencer excludes all calls to `VictimVault`.

5. **Result**: All funds in `VictimVault` are permanently frozen. No transaction can ever successfully call the contract again. [3](#0-2) [4](#0-3)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L153-167)
```text
    alloc_locals;
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
