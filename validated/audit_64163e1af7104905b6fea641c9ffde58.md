### Title
Missing Declared Class Validation in `execute_replace_class` Enables Permanent Freezing of Contract Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS does not verify that the new class hash supplied by a user corresponds to a declared contract class. An unprivileged user can supply an arbitrary, undeclared class hash via the `replace_class` syscall, permanently rendering any targeted contract non-executable and freezing all funds held within it.

---

### Finding Description

**Vulnerability class:** Invalid transaction acceptance / missing input validity check (direct analog to the Chainlink oracle's missing `updateAt` staleness/bounds check — here, the OS consumes an external value without validating it against the set of known-valid values).

In `execute_replace_class`, the `class_hash` from the user's syscall request is written directly to the contract state with no check that it exists in `contract_class_changes` (the dictionary of declared classes): [1](#0-0) 

The critical lines are:

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
```

The TODO comment explicitly acknowledges this missing check. The OS accepts any felt value as `class_hash` and commits it to state.

When a subsequent transaction attempts to call the now-broken contract, `execute_entry_point` reads the class hash and looks up the compiled class: [2](#0-1) 

```cairo
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=execution_context.class_hash   // undeclared hash → returns 0
);
// ...
let (compiled_class_fact: CompiledClassFact*) = find_element(
    array_ptr=compiled_class_facts_bundle.compiled_class_facts,
    elm_size=CompiledClassFact.SIZE,
    n_elms=compiled_class_facts_bundle.n_compiled_class_facts,
    key=compiled_class_hash,           // key = 0, not found → execution fails
);
```

If the class hash is undeclared, `dict_read` returns 0 (the dict default), and `find_element` cannot locate a compiled class, causing all future executions of the contract to fail. Because no entry point can execute, `replace_class` cannot be called again to recover. The contract is permanently broken.

---

### Impact Explanation

Any contract holding funds — a multisig wallet, vault, DeFi protocol, or any contract with a balance — can have its class replaced with an undeclared hash. Once replaced, no entry points can execute and the funds are permanently frozen. This matches the **Critical: Permanent freezing of funds** impact in the allowed scope.

---

### Likelihood Explanation

The attack path is reachable by any unprivileged user who can trigger a `replace_class` syscall within a contract. The most common realistic scenario is an upgradeable contract whose `upgrade(new_class_hash)` entry point accepts a caller-supplied hash and forwards it to `replace_class`. No special privileges, leaked keys, or operator collusion are required. The missing check is explicitly acknowledged in the codebase with a TODO comment, confirming it is a known, unmitigated gap in the current OS.

---

### Recommendation

Before updating the contract state in `execute_replace_class`, verify that `class_hash` exists in `contract_class_changes`:

```cairo
// Verify the new class hash is declared.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
with_attr error_message("Class hash not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

This mirrors the check already performed in `execute_entry_point` when resolving a class for execution, and closes the gap acknowledged by the TODO comment.

---

### Proof of Concept

1. Deploy an upgradeable vault contract that holds user funds and exposes:
   ```
   fn upgrade(new_class_hash: felt252) {
       replace_class_syscall(new_class_hash);
   }
   ```
2. Call `upgrade(0xDEADBEEF)` where `0xDEADBEEF` is an undeclared class hash.
3. The OS's `execute_replace_class` accepts this without checking if the class is declared. [3](#0-2) 
4. The `upgrade` call returns successfully; the sequencer includes the transaction.
5. The contract's class is now committed to state as `0xDEADBEEF`.
6. Any future call to the contract reaches `execute_entry_point`, which performs `dict_read` on `0xDEADBEEF` (not in `contract_class_changes`), gets 0, then `find_element` fails to locate a compiled class. [2](#0-1) 
7. All calls to the contract revert. No entry point can call `replace_class` to recover. All funds held by the contract are permanently frozen.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L896-914)
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
