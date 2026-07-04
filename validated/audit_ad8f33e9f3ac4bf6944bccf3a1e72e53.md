### Title
Missing Validation of Declared Class Hash in `execute_replace_class` Allows Permanent Freezing of Contract Funds - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS program accepts an arbitrary class hash from the caller without verifying that the hash corresponds to a previously declared contract class. This is directly analogous to the reported CRL vulnerability: just as the PCCS contract returns CRL data without validating its relevance to the current issuer certificate, the OS accepts a replacement class hash without validating its existence in the declared class registry. An attacker who controls any contract can replace its class hash with an undeclared or invalid value, permanently rendering the contract uncallable and freezing any funds held within it.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function processes the `ReplaceClassRequest` and updates the contract's `StateEntry` with the caller-supplied `class_hash` field directly, with no check that the hash corresponds to a declared class:

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
```

The developer-inserted TODO comment at line 898 explicitly acknowledges this missing check. [1](#0-0) 

When any subsequent transaction calls into the contract whose class hash has been replaced with an undeclared value, `execute_entry_point` performs a `dict_read` on `contract_class_changes` for the (now-invalid) class hash. Because the class was never declared, the dict returns the default value of `0`. The OS then calls `find_element` on the `compiled_class_facts_bundle` with key `0`:

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

Since `0` (or any arbitrary undeclared hash) will not be present in the compiled class facts bundle, `find_element` fails, making any block containing a call to the poisoned contract unprovable. The contract becomes permanently uncallable, and all funds held within it are permanently frozen.

The analogy to the CRL bug is exact: in the CRL case, a CRL is fetched and used without checking that it belongs to the current issuer certificate (stale/irrelevant data is accepted). Here, a class hash is accepted and written to state without checking that it belongs to the set of declared classes (irrelevant/nonexistent data is accepted).

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once `replace_class` is called with an undeclared class hash, the contract's `StateEntry.class_hash` is permanently set to an invalid value in the Merkle state. No future transaction can successfully call into the contract, because the OS cannot resolve the class hash to a compiled class. Any ERC-20 tokens, ETH, or other assets held in the contract's storage become permanently inaccessible. The state update is committed to the Merkle tree and cannot be reversed without a protocol-level intervention.

---

### Likelihood Explanation

**High.** The `replace_class` syscall is a standard, publicly documented StarkNet syscall callable by any contract. Any user who deploys a contract (or who controls a contract via an upgrade mechanism) can invoke it. No privileged access, leaked key, or operator cooperation is required. The missing validation is confirmed by the TODO comment in the source code, indicating the check was intentionally deferred and is not present in the current production code.

---

### Recommendation

In `execute_replace_class`, before writing the new `class_hash` to `contract_state_changes`, verify that the hash exists in `contract_class_changes` (i.e., it was declared in the current or a prior block). Concretely, perform a `dict_read` on `contract_class_changes` with `key=class_hash` and assert the returned compiled class hash is non-zero, mirroring the check already performed in `execute_entry_point` before execution. This ensures only valid, declared classes can be set as a contract's implementation, analogous to validating that a CRL belongs to the current issuer certificate before using it.

---

### Proof of Concept

1. Attacker deploys `VictimContract` holding user funds (e.g., an ERC-20 vault).
2. Attacker (as the contract owner or via a self-call) submits an invoke transaction that triggers `replace_class(class_hash=0xdeadbeef)` where `0xdeadbeef` is never declared.
3. The OS processes `execute_replace_class`:
   - `request.class_hash = 0xdeadbeef` is written directly into `new_state_entry.class_hash`.
   - No check against `contract_class_changes` is performed (the TODO at line 898 confirms this).
   - `dict_update` commits the poisoned `StateEntry` to `contract_state_changes`.
4. The block is proven and finalized. `VictimContract`'s on-chain class hash is now `0xdeadbeef`.
5. Any subsequent transaction calling `VictimContract` reaches `execute_entry_point`:
   - `dict_read(contract_class_changes, key=0xdeadbeef)` returns `0` (undeclared).
   - `find_element(..., key=0)` fails — no compiled class with hash `0` exists.
   - The OS execution aborts; the block cannot be proven.
6. The sequencer must exclude all calls to `VictimContract` from future blocks. All funds in `VictimContract` are permanently frozen.

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
