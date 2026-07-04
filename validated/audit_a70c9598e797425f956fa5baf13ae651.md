### Title
Unvalidated Class Hash in `replace_class` Syscall Allows Permanent Contract Freezing - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` function in `syscall_impls.cairo` accepts an arbitrary `class_hash` from the caller and writes it directly to the contract state without verifying that the class hash has been declared on-chain. This is structurally identical to the reported vulnerability class: user-controlled input is accepted without registry validation, corrupting persistent state. A contract can replace its own class with a non-existent class hash, permanently rendering itself uncallable and freezing any funds it holds.

---

### Finding Description

In `execute_replace_class` (lines 877–916 of `syscall_impls.cairo`), the OS reads `request.class_hash` directly from the syscall buffer and writes it into `contract_state_changes` without checking whether that class hash exists in the declared-class registry (`contract_class_changes`):

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
```

The explicit `TODO` comment at line 898 acknowledges the missing check. The declared-class registry is the `contract_class_changes` dict, which is populated only by `execute_declare_transaction` (enforcing `prev_value=0` to prevent re-declaration). The `replace_class` syscall performs no analogous lookup.

When any subsequent call to the affected contract is made (in the same or a future block), `execute_entry_point` performs:

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

If `class_hash` was never declared, `dict_read` returns 0 (the default uninitialized value), and `find_element` with key `0` will fail to locate a matching compiled class fact, causing the OS execution to abort. Because the corrupted class hash is committed to the global state root, the contract is permanently inaccessible.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once a contract's class hash is set to a non-existent value and the block is committed, the global state root encodes this invalid class hash. Every future call to the contract will fail at the OS level when `find_element` cannot resolve the compiled class. There is no recovery path: `replace_class` itself requires the contract to be callable, and no other mechanism exists to reset a contract's class hash from outside. Any funds (ERC-20 balances, ETH, or other assets) held by the contract are permanently frozen.

---

### Likelihood Explanation

The `replace_class` syscall is reachable by any deployed contract without any privileged role. A malicious contract can be written to call `replace_class(arbitrary_felt)` in a single transaction. The missing validation is explicitly flagged in the source with a `TODO` comment, confirming the developers are aware the check is absent. The sequencer will include the transaction because `replace_class` itself does not fail during execution — the corruption only manifests on the next call to the contract.

---

### Recommendation

Before writing the new class hash to `contract_state_changes`, verify that `request.class_hash` exists in the declared-class registry by performing a lookup in `contract_class_changes` and asserting the result is non-zero:

```cairo
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(
    key=class_hash
);
assert_not_zero(compiled_class_hash);  // Revert if class is not declared.
```

This mirrors the validation already enforced in `execute_declare_transaction`, which uses `prev_value=0` to guarantee a class is declared before its compiled hash is registered.

---

### Proof of Concept

1. Deploy a contract `Victim` that holds 100 ETH and exposes a `freeze()` entry point.
2. `freeze()` calls the `replace_class` syscall with `class_hash = 0xdeadbeef` (never declared).
3. The OS executes `execute_replace_class`: no validation occurs; `contract_state_changes` is updated with `class_hash = 0xdeadbeef` for `Victim`'s address.
4. The block is committed; the global state root now encodes `Victim.class_hash = 0xdeadbeef`.
5. Any subsequent invoke targeting `Victim` reaches `execute_entry_point`, calls `dict_read(key=0xdeadbeef)` → returns `0`, then `find_element(..., key=0)` → fails (no compiled class with hash 0 exists).
6. The OS cannot produce a valid proof for any block containing a call to `Victim`. The 100 ETH is permanently frozen.

---

**Relevant code references:** [1](#0-0) [2](#0-1) [3](#0-2)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L815-819)
```text
    // Note that prev_value=0 enforces that a class may be declared only once.
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```
