### Title
Unvalidated Class Hash in `execute_replace_class` Enables Permanent Freezing of Contract Funds — (File: `execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS does not verify that the replacement class hash corresponds to a declared contract class. An unprivileged contract deployer can exploit this to permanently render any contract non-callable, freezing all funds held within it. The missing check is explicitly acknowledged in the code as a TODO.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function processes the `replace_class` syscall and directly writes the caller-supplied class hash into `contract_state_changes` without any validation: [1](#0-0) 

The critical missing check is explicitly acknowledged by the development team:

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

When a subsequent transaction calls this contract, `execute_entry_point` reads the (now invalid) class hash from `contract_state_changes`, then performs a `dict_read` on `contract_class_changes` for that undeclared hash — returning 0 (the default). It then calls `find_element` to locate the compiled class with hash 0: [2](#0-1) 

Since the undeclared hash has no corresponding compiled class, `find_element` fails. The sequencer must therefore exclude all future calls to this contract from any block. The contract is permanently non-callable at the protocol level.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Any ERC-20 balance, ETH equivalent, or other asset held in a contract whose class hash has been replaced with an undeclared value is permanently inaccessible. There is no recovery path: the state is committed with the invalid class hash, no future transaction can call the contract, and no OS-level mechanism exists to restore the original class hash after the fact.

---

### Likelihood Explanation

**Medium.** The attack requires the adversary to control a contract (trivially achieved by deploying one) and to attract user deposits before triggering `replace_class`. A realistic scenario is a malicious DeFi contract that accepts user deposits and then calls `replace_class(undeclared_hash)` to permanently lock all deposited funds. The attacker need not be able to withdraw the funds — the goal is irreversible denial of access. Because `replace_class` is a standard, gas-metered syscall available to any contract, no privileged role or leaked key is required.

---

### Recommendation

Before committing the new class hash to `contract_state_changes`, verify that it is a declared class by checking its presence in `contract_class_changes`. Concretely, perform a `dict_read` on `contract_class_changes` with `class_hash` as the key and assert the result is non-zero (i.e., a valid compiled class hash exists). This is already identified as a required fix in the codebase: [3](#0-2) 

---

### Proof of Concept

1. Attacker declares a legitimate-looking class `C_legit` and deploys contract `A` using it.
2. Users deposit funds into contract `A` (e.g., via a `deposit` entry point).
3. Attacker calls a function in `A` that issues `replace_class(0xdeadbeef)`, where `0xdeadbeef` is never declared.
4. The OS processes `execute_replace_class`:
   - `request.class_hash = 0xdeadbeef` is written directly into `contract_state_changes[A]`.
   - No validation against `contract_class_changes` is performed.
5. The block is proven and the state is committed with `class_hash(A) = 0xdeadbeef`.
6. Any subsequent transaction targeting contract `A` reaches `execute_entry_point`, which calls `dict_read(contract_class_changes, 0xdeadbeef)` → returns `0`, then `find_element(..., key=0)` → fails.
7. The sequencer cannot include any call to `A` in any future block.
8. All user funds in `A` are permanently frozen with no recovery mechanism.

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
