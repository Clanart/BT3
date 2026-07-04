### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

`execute_replace_class` in `syscall_impls.cairo` accepts any user-supplied class hash without verifying that it corresponds to a class that has been declared on the network. An acknowledged TODO comment at line 898 confirms the check is intentionally absent. Any contract can call `replace_class` with an arbitrary undeclared felt value, permanently replacing its own class hash with one that has no corresponding compiled class. Any subsequent call to that contract will fail irrecoverably inside `execute_entry_point`, making the contract permanently uncallable and freezing all funds it holds.

---

### Finding Description

The `execute_replace_class` syscall handler reads the requested class hash directly from the syscall request and writes it into `contract_state_changes` with no validation:

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

The `class_hash` value comes entirely from `request.class_hash`, which is attacker-controlled calldata. No allowlist, no `dict_read` against `contract_class_changes`, and no `find_element` lookup is performed to confirm the hash is declared.

When the contract is subsequently called, `execute_entry_point` performs:

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

If the class hash was never declared, `dict_read` returns the default value `0`, and `find_element` cannot locate a compiled class with hash `0`. The OS cannot produce a valid proof for any block that includes a call to this contract, so the sequencer will permanently reject all such calls. Any funds held by the contract are irrecoverably frozen.

The analog to the external report is direct: just as `deployPool()` calls `deployer()` on a user-supplied oracle wrapper and trusts the return value without an allowlist, `execute_replace_class` accepts a user-supplied class hash and writes it to state without verifying it against the set of declared classes.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once a contract's class hash is replaced with an undeclared value, every future call to that contract fails at the `find_element` step inside `execute_entry_point`. The contract becomes permanently uncallable. Any ERC-20 tokens, ETH, or other assets held in that contract's storage are frozen with no recovery path, because the only way to move them would be to call the contract.

---

### Likelihood Explanation

**Medium.**

The `replace_class` syscall is callable by any contract on itself. An attacker who:
- deploys a contract that holds user deposits and then calls `replace_class` with an invalid hash (rug-pull variant), or
- tricks a victim contract into calling `replace_class` via a crafted cross-contract call,

can trigger the freeze. No privileged role, leaked key, or network-level attack is required. The only prerequisite is that the targeted contract holds funds worth freezing.

---

### Recommendation

Before writing the new class hash to `contract_state_changes`, verify that it corresponds to a declared class by reading `contract_class_changes`:

```cairo
// Verify the new class hash is declared.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
with_attr error_message("Class hash is not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

This mirrors the check already performed implicitly in `execute_entry_point` and makes it an explicit gate in `execute_replace_class`, closing the window before the invalid state is committed.

---

### Proof of Concept

1. Attacker deploys `VaultContract` which accepts user deposits and exposes a `drain_and_freeze()` entry point.
2. `drain_and_freeze()` first transfers all tokens to the attacker, then calls `replace_class(class_hash=0xdeadbeef)` where `0xdeadbeef` is an arbitrary undeclared felt.
3. `execute_replace_class` in `syscall_impls.cairo` (lines 896–913) writes `class_hash=0xdeadbeef` into `contract_state_changes` with no validation.
4. In the next block, any user attempting to call `VaultContract` causes `execute_entry_point` (lines 154–166) to call `dict_read(key=0xdeadbeef)` → returns `0`, then `find_element(..., key=0)` → element not found → OS cannot prove the block containing this call.
5. The sequencer permanently rejects all calls to `VaultContract`. All remaining user deposits are frozen.

The root cause — the missing declared-class check acknowledged by the TODO at line 898 — is a necessary and sufficient step in this attack path.

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
