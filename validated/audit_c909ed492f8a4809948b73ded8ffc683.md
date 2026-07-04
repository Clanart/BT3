Looking at the key files in scope, I need to examine the `execute_replace_class` function and the class validation logic more carefully.

### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The StarkNet OS `execute_replace_class` syscall handler (both the Cairo 1 and deprecated Cairo 0 variants) accepts any arbitrary felt value as the new `class_hash` without verifying that the hash corresponds to a declared contract class. An unprivileged contract can call `replace_class` with an undeclared class hash, permanently corrupting its own class pointer. Any subsequent call to that contract will fail at the proof level because the OS cannot resolve the undeclared hash to a compiled class. Funds held in the contract's storage are permanently frozen.

---

### Finding Description

In `execute_replace_class` (Cairo 1 variant), the OS explicitly acknowledges the missing check with a TODO comment:

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

The `class_hash` from the request is written directly into the contract's `StateEntry` with no validation. The identical pattern exists in the deprecated syscall handler.

When any subsequent transaction calls this contract, `execute_entry_point` performs:

```cairo
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=execution_context.class_hash
);
...
let (compiled_class_fact: CompiledClassFact*) = find_element(
    array_ptr=compiled_class_facts_bundle.compiled_class_facts,
    elm_size=CompiledClassFact.SIZE,
    n_elms=compiled_class_facts_bundle.n_compiled_class_facts,
    key=compiled_class_hash,
);
```

`dict_read` on `contract_class_changes` for an undeclared class hash returns 0 (the default uninitialized value). `find_element` then searches for a compiled class with hash 0. Since no valid compiled class with hash 0 exists in the bundle (and `validate_compiled_class_facts_post_execution` prevents the sequencer from injecting one), the proof fails. No valid proof can ever be generated for a transaction calling this contract again.

The vulnerability is present in both syscall paths:
- **Cairo 1**: `syscall_impls.cairo` lines 878–916
- **Deprecated (Cairo 0)**: `deprecated_execute_syscalls.cairo` lines 307–329

---

### Impact Explanation

**Permanent freezing of funds.** Any contract that holds token balances (ERC-20 balances stored in its storage slots, or any asset tracked in contract storage) becomes permanently inaccessible after `replace_class` is called with an undeclared hash. No transaction can successfully call the contract — the OS proof fails at `find_element` — so no withdrawal, transfer, or recovery function can ever execute. The funds are irrecoverably locked.

---

### Likelihood Explanation

The attack is straightforward and requires no privileged access:

1. An attacker deploys a contract that appears legitimate (e.g., a token vault, staking contract, or bridge escrow).
2. Users deposit funds into the contract.
3. The attacker calls a function that internally invokes `replace_class(undeclared_hash)`.
4. The OS accepts the syscall without validation and commits the corrupted class hash to state.
5. All future calls to the contract fail at proof generation.

The `replace_class` syscall is available to any deployed contract on itself. The only social-engineering requirement is convincing users to deposit funds before the attacker triggers the class replacement — a realistic scenario for any contract that accumulates user funds over time.

---

### Recommendation

Add a validation step in `execute_replace_class` (both Cairo 1 and deprecated variants) that asserts the requested `class_hash` has a non-zero entry in `contract_class_changes` (i.e., it has been declared in the current or a prior block). Concretely, after reading `class_hash` from the request, perform a `dict_read` on `contract_class_changes` and assert the result is non-zero before writing the new `StateEntry`. This is exactly what the existing TODO comment describes.

---

### Proof of Concept

**Step 1 — Attacker deploys a malicious vault contract** that exposes a `drain()` function containing:
```
replace_class(0xdeadbeef)  // 0xdeadbeef is never declared
```

**Step 2 — Users deposit tokens** into the vault (e.g., by calling `deposit(amount)`).

**Step 3 — Attacker calls `drain()`**. The OS executes `execute_replace_class`:

- `class_hash = 0xdeadbeef` is written to the contract's `StateEntry` with no validation.
- The state commitment records the new class hash.
- The transaction succeeds and is included in a proven block.

**Step 4 — Any user attempts to call `withdraw()`**. The OS executes `execute_entry_point`:

- `dict_read(contract_class_changes, key=0xdeadbeef)` → returns `0` (undeclared).
- `find_element(..., key=0)` → no compiled class with hash 0 exists → proof fails.
- The sequencer cannot include any transaction targeting this contract.

**Result**: All user funds stored in the vault's storage are permanently frozen. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L896-910)
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
