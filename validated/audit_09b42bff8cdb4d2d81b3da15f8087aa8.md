### Title
Missing Declared Class Validation in `execute_replace_class` Enables OS Halt via Undeclared Class Hash Substitution — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS Cairo program does not verify that the caller-supplied `class_hash` corresponds to a previously declared contract class. An unprivileged contract can invoke `replace_class` with an arbitrary, undeclared class hash. When the OS subsequently attempts to execute that contract (in the same block or any future block), it will call `find_element` to look up the compiled class for the undeclared hash, which will fail with a Cairo assertion error, halting the OS and preventing block proof generation — a total network shutdown.

This is the direct StarkNet analog of URI scheme hijacking: just as a malicious iOS app registers the same URI scheme as a legitimate app to intercept its messages, a malicious contract replaces its class identifier with an arbitrary value, causing the OS dispatch mechanism to fail when it tries to route execution to that class.

---

### Finding Description

In `syscall_impls.cairo`, `execute_replace_class` accepts a caller-supplied `class_hash` and writes it directly into `contract_state_changes` without any check that the hash corresponds to a declared class:

```cairo
let class_hash = request.class_hash;
// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.
local state_entry: StateEntry*;
%{ GetContractAddressStateEntry %}
tempvar new_state_entry = new StateEntry(
    class_hash=class_hash, storage_ptr=state_entry.storage_ptr, nonce=state_entry.nonce
);
dict_update{dict_ptr=contract_state_changes}(...);
``` [1](#0-0) 

The TODO comment at line 898 explicitly acknowledges this missing check. The OS accepts any felt value as the new class hash with no on-chain enforcement.

When the contract with the replaced class is subsequently executed, `execute_entry_point` performs a two-step lookup:

**Step 1** — Map Sierra class hash → compiled class hash via `contract_class_changes`:
```cairo
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=execution_context.class_hash
);
``` [2](#0-1) 

For an undeclared class hash, `dict_read` returns the default value `0` (no mapping was ever written by a `declare` transaction).

**Step 2** — Look up the compiled class by compiled class hash via `find_element`:
```cairo
let (compiled_class_fact: CompiledClassFact*) = find_element(
    array_ptr=compiled_class_facts_bundle.compiled_class_facts,
    elm_size=CompiledClassFact.SIZE,
    n_elms=compiled_class_facts_bundle.n_compiled_class_facts,
    key=compiled_class_hash,
);
``` [3](#0-2) 

`find_element` uses a Cairo `assert` internally to guarantee the key is found. With `compiled_class_hash = 0` (or any undeclared value), no matching entry exists in `compiled_class_facts`, causing an irrecoverable Cairo assertion failure that halts the OS.

The `contract_class_changes` dict is populated exclusively by `declare` transactions via `dict_update` in `execute_declare_transaction`:
```cairo
dict_update{dict_ptr=contract_class_changes}(
    key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
);
``` [4](#0-3) 

An undeclared hash will never appear in this dict, so the default `0` return from `dict_read` is guaranteed.

---

### Impact Explanation

**Impact: High — Network not being able to confirm new transactions (total network shutdown).**

When the OS fails to prove a block (due to the `find_element` assertion failure), that block cannot be finalized on L1. The sequencer cannot advance the chain. All subsequent blocks are blocked until the issue is resolved, constituting a total network shutdown.

Additionally, if the targeted contract holds user funds, those funds are permanently frozen because the contract can never be executed again after the class replacement — satisfying the **Critical: Permanent freezing of funds** impact as well.

---

### Likelihood Explanation

**Likelihood: Medium.**

The attack requires:
1. Deploying a contract (permissionless on StarkNet)
2. Calling a function on that contract that invokes `replace_class` with an arbitrary felt value

No privileged access, leaked keys, or trusted roles are required. The only uncertainty is whether the sequencer's blockifier (a separate Rust component, out of scope) independently enforces the declared-class check before submitting the block to the OS. If the blockifier also lacks this check (or if a malicious sequencer deliberately omits it), the attack is directly executable. The OS TODO comment confirms the OS itself provides zero enforcement, making the OS the necessary vulnerable step regardless of blockifier behavior.

---

### Recommendation

In `execute_replace_class`, before writing the new class hash to `contract_state_changes`, verify that the provided `class_hash` has a corresponding entry in `contract_class_changes` (i.e., it was previously declared). Concretely, perform a `dict_read` on `contract_class_changes` with the provided `class_hash` and assert the result is non-zero:

```cairo
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
with_attr error_message("Class hash not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

This mirrors the enforcement already present in `execute_entry_point` and closes the gap acknowledged by the TODO comment.

---

### Proof of Concept

1. **Deploy** a Cairo 1 contract `MaliciousContract` that exposes a public function `trigger_hijack()` which calls the `replace_class` syscall with an arbitrary undeclared felt value, e.g., `class_hash = 0xdeadbeef`.

2. **Submit** an `invoke` transaction calling `MaliciousContract::trigger_hijack()`. The OS processes `execute_replace_class`:
   - `request.class_hash = 0xdeadbeef` (never declared)
   - No validation occurs (TODO line 898)
   - `contract_state_changes[MaliciousContract_address].class_hash = 0xdeadbeef` is written

3. **Submit** a second `invoke` transaction calling any entry point on `MaliciousContract` in the same or next block. The OS processes `execute_entry_point`:
   - `execution_context.class_hash = 0xdeadbeef`
   - `dict_read(contract_class_changes, key=0xdeadbeef)` → returns `0` (undeclared)
   - `find_element(..., key=0)` → Cairo assertion failure: key not found in `compiled_class_facts`
   - **OS halts. Block cannot be proven. Network shutdown.** [5](#0-4) [6](#0-5)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L817-819)
```text
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```
