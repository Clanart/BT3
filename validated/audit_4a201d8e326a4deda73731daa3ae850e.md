### Title
Missing Declared Class Validation in `execute_replace_class` Allows Permanent Contract Bricking - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary
The `execute_replace_class` syscall in the StarkNet OS accepts an arbitrary user-supplied class hash and writes it directly into the contract state without verifying that the class hash is actually declared. This is the direct analog of the BunniZone bug: just as BunniZone reads `amAmm.getTopBid(id)` without first checking `getAmAmmEnabled(id)`, the OS uses `request.class_hash` without first checking that the class exists in `contract_class_changes`. A malicious actor can exploit this to permanently brick any contract they control, freezing all funds held within it.

---

### Finding Description

In `execute_replace_class`, the new class hash is taken directly from the syscall request and written into `contract_state_changes` with no validation: [1](#0-0) 

The code at line 898 contains an explicit acknowledgment of the missing check:

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

The `replace_class` syscall itself succeeds and the block is proven. The failure manifests when the bricked contract is called in any subsequent block. In `execute_entry_point`, the OS reads the (now-invalid) class hash from state and performs a lookup in `contract_class_changes`: [2](#0-1) 

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
```

For an undeclared class hash, `dict_read` returns `0` (`UNINITIALIZED_CLASS_HASH`). `find_element` then asserts the element exists — it does not return a sentinel — so the OS Cairo program traps. The sequencer cannot include any transaction that calls the bricked contract, making it permanently uncallable and all funds within it permanently frozen.

The `UNINITIALIZED_CLASS_HASH = 0` constant confirms the default return value: [3](#0-2) 

The `execute_replace_class` function is reachable from `execute_syscalls` via the `REPLACE_CLASS_SELECTOR` dispatch branch, callable by any contract: [4](#0-3) 

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

A malicious actor deploys a contract (e.g., a fake vault or DEX), collects user deposits, then calls `replace_class` with an arbitrary undeclared felt (e.g., `0xdeadbeef`). The OS writes this into the state tree without complaint. The block is proven. From that point forward, every call to the contract causes the OS to trap on `find_element`, so the sequencer can never include such a call. All funds inside the contract are permanently frozen with no recovery path, since the committed state root now encodes the invalid class hash.

---

### Likelihood Explanation

**Medium.**

- Any unprivileged user can deploy a contract and invoke `replace_class` — no special role or key is required.
- The TODO comment at line 898 is a developer-acknowledged gap, meaning the check was intentionally deferred, not overlooked in review.
- The attack requires social engineering (convincing users to deposit into the malicious contract) but no cryptographic capability or network-level access.

---

### Recommendation

Inside `execute_replace_class`, before writing the new `StateEntry`, perform a `dict_read` on `contract_class_changes` with the requested `class_hash` and assert the result is non-zero (i.e., the class has been declared). This mirrors the validation already implicitly required by `execute_entry_point` when it later looks up the compiled class.

```cairo
// Add before dict_update:
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
with_attr error_message("Class hash is not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

---

### Proof of Concept

1. Attacker deploys `MaliciousVault` — a contract with a public `deposit()` and a hidden `brick()` function that calls `replace_class(0xdeadbeef)`.
2. Users deposit funds into `MaliciousVault`.
3. Attacker calls `brick()`:
   - `execute_replace_class` is invoked with `class_hash = 0xdeadbeef`.
   - No declared-class check is performed (the TODO is unimplemented).
   - `contract_state_changes[MaliciousVault].class_hash` is set to `0xdeadbeef`.
   - The block is proven successfully; the invalid state is committed.
4. Any subsequent call to `MaliciousVault` (e.g., `withdraw()`):
   - `execute_entry_point` reads `class_hash = 0xdeadbeef`.
   - `dict_read(contract_class_changes, 0xdeadbeef)` → returns `0`.
   - `find_element(compiled_class_facts, 0)` → OS traps; block cannot be proven with this call.
   - Sequencer permanently excludes all calls to `MaliciousVault`.
5. All user funds in `MaliciousVault` are permanently frozen with no recovery mechanism.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/commitment.cairo (L16-16)
```text
const UNINITIALIZED_CLASS_HASH = 0;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_syscalls.cairo (L195-203)
```text
    if (selector == REPLACE_CLASS_SELECTOR) {
        execute_replace_class(contract_address=execution_context.execution_info.contract_address);
        %{ OsLoggerExitSyscall %}
        return execute_syscalls(
            block_context=block_context,
            execution_context=execution_context,
            syscall_ptr_end=syscall_ptr_end,
        );
    }
```
