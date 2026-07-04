### Title
Missing Class Hash Validation in `execute_replace_class` Allows Permanent Freezal of Contract Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS Cairo program accepts an arbitrary user-supplied class hash and writes it directly to the contract state without verifying that the hash corresponds to a declared contract class. An explicit TODO comment in the code acknowledges this missing check. Because the OS is the ZK-proof program whose output is committed on L1, a valid proof can be generated for a block that contains such a state transition. Once committed, the contract's class is set to an undeclared/invalid hash, making the contract permanently non-callable and freezing any funds it holds.

---

### Finding Description

In `execute_replace_class` (lines 878–916 of `syscall_impls.cairo`), the new class hash is taken directly from the user-supplied syscall request and written to `contract_state_changes` with no validation:

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

The TODO comment explicitly acknowledges the missing check. The `class_hash` field comes from `request.class_hash`, which is fully attacker-controlled calldata.

When a contract whose class has been replaced with an undeclared hash is subsequently called, `execute_entry_point` performs:

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

If `class_hash` was never declared, `dict_read` returns the default value (0), and `find_element` with key `0` panics (Cairo `find_element` aborts if the key is absent), making the contract permanently unprovable and non-callable.

The state-transition analogy to the Crowdsale bug is direct: just as the Crowdsale allowed a transition back to `Preparing` state by setting `finalizeAgent` to a non-sane instance (an unexpected/invalid state with no recovery path), the StarkNet OS allows a contract to transition to an "invalid class" state by calling `replace_class` with an undeclared hash — an unexpected state with no recovery path.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once the `replace_class` transaction is included in a block and the proof is verified on L1, the state is committed with the invalid class hash. Any funds held by the affected contract (ERC-20 balances, ETH, NFTs, or protocol-level locked assets) become permanently inaccessible because:

1. Every subsequent call to the contract causes `find_element` to panic during proof generation.
2. The sequencer cannot include any call to the contract in a provable block.
3. There is no recovery mechanism — `replace_class` can only be called by the contract itself, which is now non-callable.

---

### Likelihood Explanation

**High.** The `replace_class` syscall is available to any deployed contract with no privilege requirement. Any unprivileged user who controls a contract (or can trick a contract into calling `replace_class`) can trigger this. The OS does not enforce the missing check, so a valid ZK proof can be generated for the invalid state transition. The sequencer's off-chain simulation may or may not catch this depending on implementation quality, but the OS — the authoritative security boundary — does not.

---

### Recommendation

In `execute_replace_class`, before writing the new class hash to `contract_state_changes`, verify that the class hash exists in `contract_class_changes` (i.e., it has been declared in the current or a prior block). Concretely, perform a `dict_read` on `contract_class_changes` with `class_hash` as the key and assert the result is non-zero:

```cairo
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
with_attr error_message("Class hash not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

This resolves the explicitly noted TODO and closes the state-transition bypass.

---

### Proof of Concept

1. Attacker deploys contract `C` holding funds (e.g., an ERC-20 vault).
2. Attacker sends an invoke transaction that calls `replace_class(class_hash=0xDEAD)` from within `C`, where `0xDEAD` is an arbitrary felt never declared on-chain.
3. The OS processes `execute_replace_class`: `request.class_hash = 0xDEAD` is written to `contract_state_changes[C].class_hash` with no validation. [3](#0-2) 
4. The block is proven and committed on L1. The state now records `C.class_hash = 0xDEAD`.
5. Any future call to `C` causes `execute_entry_point` to do `dict_read(contract_class_changes, 0xDEAD)` → returns `0`, then `find_element(..., key=0)` → panic. [2](#0-1) 
6. No proof can ever be generated for a block containing a call to `C`. All funds in `C` are permanently frozen.

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
