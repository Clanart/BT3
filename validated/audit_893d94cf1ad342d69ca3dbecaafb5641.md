### Title
Missing Validation of New Class Hash in `execute_replace_class` Allows Permanent Freezing of Contract Funds - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS does not validate that the new class hash supplied by a contract corresponds to a previously declared class. Any contract can call `replace_class` with an arbitrary, undeclared class hash. Once committed, the contract's class hash is permanently set to an invalid value, making the contract permanently inaccessible and freezing any funds it holds.

---

### Finding Description

The `execute_replace_class` function processes the `replace_class` syscall. It reads the requested new class hash directly from the syscall request and writes it into `contract_state_changes` with no check that the hash is a declared class:

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

The in-code TODO comment explicitly acknowledges the missing check. The `replace_class` syscall is reachable by any contract via the `execute_syscalls` dispatcher: [2](#0-1) 

When a subsequent block attempts to execute a call to the affected contract, `execute_entry_point` reads the now-invalid class hash from `contract_state_changes`, looks it up in `contract_class_changes` (returning 0, the dict default), and then calls `find_element` searching for a compiled class with hash 0: [3](#0-2) 

`find_element` panics if the key is absent, causing the OS to fail for any block that includes a call to the affected contract. The sequencer will therefore permanently exclude all transactions targeting that contract, making it and its funds permanently inaccessible.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once a contract's class hash is set to an undeclared value:
- Every subsequent call to the contract causes the OS prover to fail at `find_element`.
- The sequencer's simulation detects this and permanently excludes all such transactions.
- The state change is committed on-chain; reversal requires a hard fork.
- All funds (tokens, ETH, STRK) held by the contract are permanently frozen with no recovery path.

---

### Likelihood Explanation

**Moderate.** The attack path is straightforward and requires no privileged access:

1. An attacker deploys a contract that exposes a callable function invoking `replace_class(class_hash=<undeclared_value>)`.
2. Users deposit funds into the contract (e.g., believing it is a legitimate vault or account).
3. The attacker calls the trigger function; the `replace_class` syscall succeeds and commits the invalid class hash.
4. The contract is permanently bricked; all deposited funds are frozen.

The `replace_class` syscall is available to every contract without restriction. The attack requires no leaked keys, no operator access, and no network-level capabilities — only the ability to deploy and call a contract, which any unprivileged user can do.

---

### Recommendation

Inside `execute_replace_class`, before writing the new class hash to `contract_state_changes`, validate that the requested class hash exists as a declared class. Concretely, perform a lookup in `contract_class_changes` (or the equivalent declared-class registry) and assert the result is non-zero:

```cairo
// Validate that the new class hash is declared.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
with_attr error_message("Class hash is not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

This mirrors the validation already performed implicitly in `execute_entry_point` when dispatching calls, and closes the gap identified by the existing TODO comment.

---

### Proof of Concept

**Step 1 — Deploy malicious contract.**
Deploy `MaliciousVault` whose `freeze()` entry point calls `replace_class(class_hash=0xdeadbeef)`, where `0xdeadbeef` is not a declared class hash.

**Step 2 — Attract deposits.**
Users send funds to `MaliciousVault`, believing it is a legitimate vault.

**Step 3 — Trigger the attack.**
Attacker calls `freeze()`. The OS executes `execute_replace_class`:
- `class_hash = 0xdeadbeef` is written to `contract_state_changes` with no validation.
- The revert log records `CHANGE_CLASS_ENTRY` with the old class hash.
- The transaction succeeds and the state is committed.

**Step 4 — Contract is permanently bricked.**
In any subsequent block, a transaction calling `MaliciousVault`:
- `execute_entry_point` reads `class_hash = 0xdeadbeef`.
- `dict_read` on `contract_class_changes` returns 0 (undeclared).
- `find_element(..., key=0)` panics — OS proof fails.
- Sequencer permanently excludes all calls to `MaliciousVault`.

**Result:** All user funds inside `MaliciousVault` are permanently frozen with no on-chain recovery mechanism.

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
