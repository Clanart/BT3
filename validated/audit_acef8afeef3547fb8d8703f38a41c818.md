### Title
Unvalidated Class Hash in `execute_replace_class` Allows Permanent Freezal of Contract Funds - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The StarkNet OS `execute_replace_class` syscall implementation does not verify that the new class hash supplied by a contract is a declared (known) Sierra class. This is directly analogous to the LP.sol bug: just as LP.sol consumed *current*, attacker-manipulable pool balances without time-weighting or validation, the OS here consumes the *current* class hash written by a contract mid-transaction without checking it against the set of declared classes. The result is that a contract's class pointer can be permanently set to an undeclared hash, making the contract permanently uncallable and freezing any funds it holds.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function accepts any `class_hash` value from the contract's syscall request and writes it directly into `contract_state_changes` without verifying that the hash corresponds to a declared Sierra class:

```cairo
// execute_replace_class, syscall_impls.cairo ~L896-L914
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

The TODO comment at line 898 explicitly acknowledges the missing check. The OS accepts and commits any `class_hash` value, including one that has never been declared.

When a subsequent call is made to the contract (in the same or a later block), `execute_entry_point` reads the class hash from state and looks up its compiled class hash:

```cairo
// execute_entry_point.cairo ~L154-L166
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

If the Sierra class hash was never declared, `dict_read` returns `0` (the default for an uninitialized dict entry). `find_element` with `key=0` then fails as a Cairo assertion if no compiled class with hash `0` exists, making the proof for any block that calls the contract invalid.

The vulnerability class is identical to the LP.sol report: **a current, attacker-manipulable value (the class hash, set intra-transaction via `replace_class`) is consumed by a downstream computation (entry point dispatch) without any validation against a committed, trusted set (declared classes)**. The LP.sol analog is using current pool balances (manipulable via flash loan) in a price computation without TWAP.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once a contract's class hash is set to an undeclared value and the transaction is committed to state, the contract becomes permanently uncallable at the OS level. Any funds (ERC-20 balances, ETH, or other assets) held by or attributed to that contract address are irrecoverably frozen. No future transaction can successfully execute an entry point on the contract, because `find_element` will fail for the undeclared compiled class hash, producing an invalid proof.

---

### Likelihood Explanation

**Medium.**

The attacker requires a target contract that:
1. Holds or controls funds, and
2. Exposes a code path that calls `replace_class` with attacker-influenced input (e.g., an upgradeable proxy where the upgrade selector is not access-controlled, or a contract with a logic bug).

Such patterns are common in upgradeable contract architectures. Because the OS provides no safety net, even a contract that *accidentally* passes an undeclared hash (e.g., due to an off-by-one in a hash computation) will have its funds permanently frozen. The attacker's transaction is a standard `INVOKE_FUNCTION` — no privileged role is required.

---

### Recommendation

In `execute_replace_class`, before writing the new class hash to `contract_state_changes`, assert that the hash exists in `contract_class_changes` (i.e., it has been declared in the current or a prior block):

```cairo
// After reading class_hash from request:
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
assert_not_zero(compiled_class_hash);  // Enforce: class must be declared.
```

This mirrors the fix recommended in the LP.sol report: validate the value against a committed, trusted source before using it in a critical state transition, rather than accepting the current (attacker-manipulable) value blindly.

---

### Proof of Concept

1. **Attacker deploys** contract `C` holding user funds. `C` exposes a public `upgrade(new_class_hash: felt)` function that calls `replace_class(new_class_hash)` without access control.

2. **Attacker submits** an `INVOKE_FUNCTION` transaction calling `C.upgrade(0xdeadbeef)` where `0xdeadbeef` is an arbitrary, never-declared Sierra class hash.

3. **OS executes** `execute_replace_class` at `syscall_impls.cairo` L896–L914. The TODO check is absent; the OS writes `StateEntry(class_hash=0xdeadbeef, ...)` into `contract_state_changes` for contract `C`. The transaction succeeds and is committed to state.

4. **Any subsequent call** to `C` (e.g., a withdrawal) reaches `execute_entry_point` at `execute_entry_point.cairo` L154. `dict_read(contract_class_changes, 0xdeadbeef)` returns `0`. `find_element(..., key=0)` fails as a Cairo assertion. The block proof is invalid; the sequencer cannot include any call to `C`.

5. **Result**: All funds in `C` are permanently frozen. The OS accepted the intra-transaction class hash manipulation (analogous to LP.sol accepting intra-transaction balance manipulation) without validation.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L896-914)
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
