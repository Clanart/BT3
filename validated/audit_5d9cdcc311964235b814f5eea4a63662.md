### Title
Missing Validation of Declared Class Hash in `replace_class` Syscall Allows Permanent Freezing of Contract Funds - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The StarkNet OS `execute_replace_class` function does not verify that the new class hash supplied by a contract is actually a declared class. This missing enforcement — explicitly acknowledged by a TODO comment in the production code — allows any contract to replace its own class hash with an arbitrary, undeclared value. Once set, the contract becomes permanently non-callable, freezing all funds held within it.

---

### Finding Description

In `execute_replace_class`, the OS accepts the caller-supplied `class_hash` and writes it directly into `contract_state_changes` without checking whether that hash corresponds to a previously declared class: [1](#0-0) 

The comment at line 898 explicitly acknowledges the missing check:

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

No assertion is made that `class_hash` exists in `contract_class_changes`. Compare this to the path taken when the class hash is later consumed in `execute_entry_point`: [2](#0-1) 

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
```

If `class_hash` is undeclared, `dict_read` returns `0` (the Cairo dict default), and `find_element` with key `0` will fail to locate a compiled class fact, causing the proof to be unprovable for any transaction that touches the contract. The sequencer must therefore permanently exclude all calls to that contract, freezing any funds it holds.

---

### Impact Explanation

**Impact: Critical — Permanent freezing of funds.**

Once a contract's class hash is set to an undeclared value via `replace_class`, every subsequent call to that contract causes `find_element` to fail at the OS level. The sequencer cannot include such calls in any provable block. Any ERC-20 tokens, ETH, or other assets held in the contract's storage become permanently inaccessible. There is no recovery path: the OS state transition that wrote the invalid class hash was accepted and committed, and no future transaction can undo it without a protocol-level upgrade.

---

### Likelihood Explanation

**Likelihood: Medium.**

The attack requires a malicious contract deployer to:
1. Deploy a contract that exposes a function calling `replace_class` with an arbitrary felt value.
2. Attract user funds into the contract (e.g., by presenting it as a legitimate DeFi vault or bridge).
3. Invoke the malicious function.

All three steps are within the capability of an unprivileged actor. The `replace_class` syscall is available to any contract with no access control at the OS level. The attack is irreversible once executed.

---

### Recommendation

In `execute_replace_class`, before writing the new class hash into `contract_state_changes`, assert that the supplied `class_hash` has a non-zero entry in `contract_class_changes` (i.e., it has been declared in the current or a prior block):

```cairo
// Verify the new class hash is a declared class.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
with_attr error_message("replace_class: class hash is not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

This mirrors the validation already performed implicitly in `execute_entry_point` and closes the gap the TODO comment identifies.

---

### Proof of Concept

1. **Deploy** a malicious contract whose Sierra source contains:
   ```rust
   fn drain(ref self: ContractState) {
       // 0xdeadbeef is not a declared class hash
       starknet::replace_class_syscall(0xdeadbeef_u256.try_into().unwrap()).unwrap();
   }
   ```
2. **Attract** user funds: users call `deposit()` and the contract holds their tokens.
3. **Attacker calls** `drain()`. The OS executes `execute_replace_class` with `class_hash = 0xdeadbeef`. Because no validation is performed, `dict_update` writes `class_hash=0xdeadbeef` into `contract_state_changes`.
4. **State is committed.** The block is proven and finalized with the contract's class hash set to `0xdeadbeef`.
5. **Any future call** to the contract reaches `execute_entry_point`, which calls `dict_read` on `contract_class_changes` for key `0xdeadbeef`, receives `0` (undeclared), then calls `find_element` with key `0`. No compiled class fact with hash `0` exists; the proof fails. The sequencer cannot include any transaction touching this contract.
6. **All user funds are permanently frozen.**

The root cause is the absent check at: [3](#0-2)

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
