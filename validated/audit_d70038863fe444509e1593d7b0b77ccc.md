### Title
Missing Class Hash Validation in `execute_replace_class` Enables Permanent Freezing of Contract Funds - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS does not validate that the new class hash supplied by a contract corresponds to a previously declared class. Any contract can call `replace_class` with an arbitrary, undeclared felt value as the class hash. Once committed to state, all subsequent calls to that contract will fail at the OS level because the compiled class lookup will find no matching entry, permanently rendering the contract uncallable and freezing any funds it holds.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function reads the requested class hash directly from the syscall request and writes it into the contract state without any validation:

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

The TODO comment at line 898 explicitly acknowledges that the check is missing. The `class_hash` value originates from `request.class_hash`, which is fully controlled by the calling contract (i.e., by user-deployed code). No assertion is made that this value exists in `contract_class_changes` (the declared-class dictionary) or in the `compiled_class_facts_bundle`.

When a subsequent transaction attempts to call the affected contract, `execute_entry_point` performs:

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
``` [2](#0-1) 

`dict_read` on an undeclared class hash returns 0 (the default). `find_element` with key `0` will fail to locate any compiled class fact (since no class with hash 0 is declared), causing an OS-level failure. The contract becomes permanently uncallable.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once a contract's class hash is replaced with an undeclared value and the block is finalized, the state change is irreversible. Any ERC-20 tokens, ETH, or other assets held by the contract are permanently inaccessible. No future transaction can successfully invoke the contract because the OS will always fail to resolve the compiled class, and there is no recovery path within the protocol.

---

### Likelihood Explanation

**Medium.** The attack requires deploying a contract whose code calls `replace_class` with an invalid hash. This is directly achievable by:

1. A malicious contract deployer who creates a honeypot contract that accepts deposits and then self-destructs its class reference.
2. A buggy contract that accidentally passes an unvalidated storage value or user-supplied argument to `replace_class`.

The syscall is available to any deployed contract, and the OS imposes no restriction on the hash value supplied. The missing check is explicitly flagged in the source with a TODO, confirming it is a known gap.

---

### Recommendation

Before writing the new `StateEntry`, validate that `class_hash` is present in `contract_class_changes` (i.e., it was previously declared via a `declare` transaction). Concretely, perform a `dict_read` on `contract_class_changes` with `class_hash` as the key and assert the result is non-zero, or use `search_sorted` against the compiled class facts bundle to confirm the hash is known. This mirrors the validation already performed implicitly in `execute_entry_point` but must be enforced eagerly at the point of replacement.

---

### Proof of Concept

1. Deploy a Sierra contract containing an external function that executes the `replace_class` syscall with a hardcoded arbitrary felt (e.g., `0xdeadbeef`) that has never been declared.
2. Submit an invoke transaction calling that function. The OS processes `execute_replace_class`:
   - `class_hash = 0xdeadbeef` is read from the request. [3](#0-2) 
   - No validation is performed (the TODO block is absent).
   - `dict_update` writes `class_hash=0xdeadbeef` into `contract_state_changes`. [4](#0-3) 
3. The transaction succeeds and is included in a finalized block. The contract's on-chain class hash is now `0xdeadbeef`.
4. Submit any subsequent invoke transaction targeting the contract. In `execute_entry_point`:
   - `dict_read` on `contract_class_changes` with key `0xdeadbeef` returns `0` (undeclared). [5](#0-4) 
   - `find_element` searches for compiled class hash `0` and finds nothing, causing an OS-level failure. [6](#0-5) 
5. The contract is permanently uncallable. All funds held within it are frozen with no recovery mechanism.

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
