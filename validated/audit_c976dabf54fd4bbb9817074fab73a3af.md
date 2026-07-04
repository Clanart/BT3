### Title
Missing Class Hash Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS Cairo program accepts an arbitrary class hash from a contract's syscall request and writes it directly to the state — without verifying that the supplied hash corresponds to a previously declared class. This is the direct analog of the "raw request chain" pattern: a raw, unvalidated value from an unprivileged caller is passed through the OS execution layer and committed to protocol state without any filtering or existence check.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function reads the requested class hash from the syscall pointer and immediately writes it to `contract_state_changes`:

```cairo
// syscall_impls.cairo lines 896-913
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

The developer-inserted TODO comment explicitly acknowledges the missing check: `// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.`

There is no assertion, no lookup in `contract_class_changes`, and no cross-reference against the compiled class facts bundle to confirm that `class_hash` was ever declared via a `DECLARE` transaction. The raw value from the syscall request is forwarded directly to the state update — a textbook unvalidated passthrough.

Compare this to the analogous pattern in the external report:
- **External report**: `api(req: Request)` → `skipService.api(req)` → `axios.request(...)` — raw request forwarded without filtering.
- **This codebase**: `request.class_hash` → `new StateEntry(class_hash=class_hash, ...)` → `dict_update(...)` — raw class hash forwarded without existence check.

The entry point for this syscall is fully open to any executing contract:

```cairo
// execute_syscalls.cairo lines 195-202
if (selector == REPLACE_CLASS_SELECTOR) {
    execute_replace_class(contract_address=execution_context.execution_info.contract_address);
    ...
}
```

Any contract, deployed by any unprivileged user, can invoke this syscall.

---

### Impact Explanation

**Impact: Critical — Permanent freezing of funds.**

When a contract's class hash is replaced with an undeclared hash `H`, the following chain of events occurs in any subsequent block that attempts to call that contract:

1. `execute_entry_point` reads the contract's class hash from state → gets `H`.
2. It calls `dict_read{dict_ptr=contract_class_changes}(key=H)` → returns `0` (no declared compiled class for `H`).
3. It calls `find_element(..., key=0)` on the compiled class facts bundle.
4. If `0` is not present in the compiled class facts (which it will not be for a legitimately undeclared hash), `find_element` raises an exception and the OS proof fails.

The contract is permanently uncallable. Any ERC-20 tokens, ETH, or other assets held by the contract are permanently frozen with no recovery path, because no valid proof can ever be generated for a block that executes a call to that contract.

The attack scenario reachable by an unprivileged actor:
1. Attacker deploys a contract (e.g., a fake vault or wallet).
2. Users deposit funds into the contract.
3. Attacker calls `replace_class` with an arbitrary undeclared felt value as the class hash.
4. The OS accepts the syscall and commits the invalid class hash to state.
5. The contract is permanently bricked; all deposited funds are frozen forever.

---

### Likelihood Explanation

**Likelihood: Medium.**

- The `replace_class` syscall is available to every deployed contract with no access control.
- The missing validation is explicitly acknowledged in the source code via a TODO comment, confirming the developers are aware the check does not exist.
- A malicious actor only needs to deploy a contract and invoke one syscall. No privileged access, leaked keys, or external dependencies are required.
- The attack is irreversible once committed to state.

---

### Recommendation

Before writing the new class hash to `contract_state_changes`, verify that the hash exists as a declared class by checking `contract_class_changes`:

```cairo
// Verify the new class hash is declared.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
with_attr error_message("Class hash is not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

This mirrors the validation already performed implicitly in `execute_entry_point` when looking up a class for execution, but must be enforced proactively at the point of replacement to prevent invalid state from being committed.

---

### Proof of Concept

**Step 1 — Attacker deploys a contract with a `replace_class` call:**
```
deploy_account → contract address = 0xDEAD
```

**Step 2 — Users deposit funds:**
```
invoke transfer(recipient=0xDEAD, amount=1_000_000)
```

**Step 3 — Attacker calls `replace_class` with an arbitrary undeclared hash:**
```
invoke 0xDEAD.__execute__() → internally calls replace_class(class_hash=0xBADBADBAD)
```

**Step 4 — OS execution in `execute_replace_class`:**
```cairo
let class_hash = request.class_hash;  // = 0xBADBADBAD
// No check performed. Proceeds directly to:
dict_update(key=0xDEAD, new_value=StateEntry(class_hash=0xBADBADBAD, ...))
``` [1](#0-0) 

**Step 5 — Any future call to `0xDEAD` in a subsequent block:**
```cairo
// execute_entry_point.cairo
let (compiled_class_hash) = dict_read(key=0xBADBADBAD);  // returns 0
let (compiled_class_fact) = find_element(..., key=0);     // FAILS — 0 not declared
// OS proof cannot be generated. Contract is permanently frozen.
``` [2](#0-1) 

The `replace_class` syscall is dispatched without restriction from `execute_syscalls`: [3](#0-2) 

The missing validation is confirmed by the developer TODO in the handler: [4](#0-3)

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
