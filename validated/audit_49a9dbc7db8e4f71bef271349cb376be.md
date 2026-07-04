### Title
Missing Validation of New Class Hash in `execute_replace_class` Allows Permanent Freezing of Contract Funds - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS does not validate that the new class hash supplied by the caller corresponds to a previously declared contract class. An unprivileged contract can call `replace_class` with an arbitrary, undeclared class hash, permanently rendering the contract un-callable and freezing any funds held within it.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function reads the requested new class hash directly from the syscall request and writes it into `contract_state_changes` without any check that the hash is present in `contract_class_changes` (the declared-class registry):

```cairo
func execute_replace_class{...}(contract_address: felt) {
    ...
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
    ...
}
``` [1](#0-0) 

The developer-acknowledged TODO at line 898 explicitly confirms the missing check. The syscall succeeds and the state is committed with the arbitrary class hash.

When any subsequent transaction attempts to call the affected contract, `execute_entry_point` performs:

```cairo
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=execution_context.class_hash
);
...
let (compiled_class_fact: CompiledClassFact*) = find_element(
    array_ptr=compiled_class_facts_bundle.compiled_class_facts,
    ...
    key=compiled_class_hash,
);
``` [2](#0-1) 

`dict_read` on an undeclared class hash returns the default value `0`. `find_element` then searches for a compiled class with hash `0`. If none exists, `find_element` panics, making the proof unprovable for any transaction touching that contract. The sequencer is forced to permanently exclude all calls to the contract, freezing its funds.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once `replace_class` is called with an undeclared hash and the transaction is finalized, the contract's `class_hash` field in the committed state is irrecoverably set to an invalid value. No future transaction can successfully call the contract (the OS proof would be invalid), and no upgrade or recovery path exists. Any ERC-20 tokens, ETH, or other assets held in the contract's storage are permanently inaccessible.

---

### Likelihood Explanation

The `replace_class` syscall is callable by any contract from within its own execution context — no privileged role is required. A malicious actor can:

1. Deploy a contract that internally calls `replace_class` with a crafted undeclared hash.
2. Advertise the contract as a legitimate vault or escrow.
3. After collecting user funds, trigger the `replace_class` call.
4. All deposited funds are permanently frozen.

The attack requires only a standard `invoke` transaction from an unprivileged account. The missing check is confirmed by the in-code TODO, indicating the developers are aware the guard is absent.

---

### Recommendation

Before committing the new class hash in `execute_replace_class`, verify that it is present in `contract_class_changes` (i.e., it has been declared in the current or a prior block). Concretely, perform a `dict_read` on `contract_class_changes` for `class_hash` and assert the returned compiled class hash is non-zero:

```cairo
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
with_attr error_message("Class hash is not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

This mirrors the validation already performed implicitly in `execute_entry_point` but makes it an explicit, enforced pre-condition of the syscall itself.

---

### Proof of Concept

1. Deploy contract `MaliciousVault` whose `__execute__` entry point calls `replace_class(0xdeadbeef)` where `0xdeadbeef` is not a declared class hash.
2. Users deposit funds into `MaliciousVault`.
3. Attacker sends an `invoke` transaction calling `MaliciousVault.__execute__`.
4. The OS processes `execute_replace_class`:
   - `request.class_hash = 0xdeadbeef`
   - No declared-class check is performed (line 898 TODO).
   - `contract_state_changes` is updated: `MaliciousVault.class_hash = 0xdeadbeef`.
5. Transaction is finalized and committed to state.
6. Any subsequent call to `MaliciousVault` reaches `execute_entry_point`:
   - `dict_read(contract_class_changes, 0xdeadbeef)` → returns `0` (undeclared).
   - `find_element(..., key=0)` → panics; proof is unprovable.
7. The sequencer cannot include any transaction touching `MaliciousVault`. All funds are permanently frozen.

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
