### Title
Missing Declared Class Hash Validation in `execute_replace_class` Enables Permanent Contract Freezing — (File: `execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation in `syscall_impls.cairo` does not verify that the new class hash supplied by a calling contract is actually declared on-chain. An unprivileged contract owner can call `replace_class` with an arbitrary undeclared class hash, permanently freezing the contract and all funds it holds, because the OS can never again prove a block that executes any entry point of that contract.

---

### Finding Description

In `execute_replace_class`, the function updates `contract_state_changes` with the caller-supplied class hash without any validation that the hash corresponds to a declared class. The code itself contains an explicit acknowledgment of this gap:

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

After this update, the contract's `class_hash` field in the state is set to the undeclared value. In any subsequent block, when `execute_entry_point` is called for this contract, it performs:

```cairo
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=execution_context.class_hash
);
// ...
let (compiled_class_fact: CompiledClassFact*) = find_element(
    array_ptr=compiled_class_facts_bundle.compiled_class_facts,
    elm_size=CompiledClassFact.SIZE,
    n_elms=compiled_class_facts_bundle.n_compiled_class_facts,
    key=compiled_class_hash,
);
``` [2](#0-1) 

Because the class hash was never declared, `dict_read` returns the default value `0` (the dict is initialized via `dict_new()` with default 0). `find_element` then searches for a compiled class with hash `0`, which does not exist in `compiled_class_facts_bundle`. `find_element` is a Cairo assertion — it panics if the key is absent. The OS therefore cannot produce a valid proof for any block that calls the frozen contract. The sequencer is forced to exclude all such calls indefinitely.

The `contract_class_changes` dict is initialized in `initialize_state_changes` with `dict_new()`, confirming the default return value of 0 for any undeclared key: [3](#0-2) 

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

A malicious contract owner deploys a contract that appears legitimate (e.g., a vault, a multisig, or a token contract), attracts user deposits, and then calls `replace_class` with an arbitrary undeclared class hash. From that point forward:

- The OS cannot prove any block that executes an entry point of the contract.
- The sequencer cannot include any such call.
- All funds held by the contract are permanently inaccessible to every user.

The attacker does not need to extract the funds; the goal is to destroy the contract's usability, which constitutes permanent freezing of funds for all depositors.

---

### Likelihood Explanation

The attack path is fully reachable by any unprivileged transaction sender who owns or controls a contract. No privileged role, leaked key, or operator cooperation is required. The only prerequisite is deploying a contract and issuing a `replace_class` syscall with an arbitrary felt value as the new class hash. The syscall is available to all Sierra contracts without restriction.

---

### Recommendation

Inside `execute_replace_class`, before writing the new class hash to `contract_state_changes`, add a Cairo-level assertion that the new class hash exists in `contract_class_changes` (i.e., its compiled class hash is non-zero). This is already identified in the TODO comment at line 898. The check should mirror the pattern used in `execute_entry_point` — perform a `dict_read` on `contract_class_changes` for the new class hash and assert the result is non-zero before allowing the update.

---

### Proof of Concept

1. Attacker deploys contract `V` (a fake vault) and advertises it as a legitimate yield vault.
2. Victims deposit tokens into `V`; `V` holds significant funds.
3. Attacker calls `replace_class(0xdeadbeef)` from within `V`, where `0xdeadbeef` is an arbitrary felt that was never declared.
4. The OS executes `execute_replace_class`: no declared-class check exists, so `contract_state_changes[V].class_hash` is set to `0xdeadbeef`. The block is proven successfully.
5. In any future block, a victim attempts to call `V.__execute__` to withdraw funds.
6. `execute_entry_point` runs: `dict_read(contract_class_changes, 0xdeadbeef)` → returns `0`; `find_element(..., key=0)` → Cairo panic (element not found).
7. The OS cannot prove the block. The sequencer drops the call.
8. No call to `V` can ever be included in a proven block. All deposited funds are permanently frozen.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L154-166)
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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/os.cairo (L262-276)
```text
func initialize_state_changes() -> (
    contract_state_changes: DictAccess*, contract_class_changes: DictAccess*
) {
    %{ InitializeStateChanges %}
    // A dictionary from contract address to a dict of storage changes of type StateEntry.
    let (contract_state_changes: DictAccess*) = dict_new();

    %{ InitializeClassHashes %}
    // A dictionary from class hash to compiled class hash (Casm).
    let (contract_class_changes: DictAccess*) = dict_new();

    return (
        contract_state_changes=contract_state_changes, contract_class_changes=contract_class_changes
    );
}
```
