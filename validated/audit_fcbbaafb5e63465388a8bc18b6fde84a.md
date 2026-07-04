### Title
Missing Declared Class Validation in `execute_replace_class` Enables Permanent Fund Freezing — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS accepts any arbitrary class hash without verifying it corresponds to a declared contract class. This allows any unprivileged contract caller to permanently break a contract — and freeze all funds it holds — by replacing its class with an undeclared hash. The OS generates a valid proof for the `replace_class` call itself, making the damage irreversible on-chain.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function contains an explicit, unresolved TODO acknowledging the missing validation:

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

The `class_hash` value is taken directly from `request.class_hash` with no check that it exists in `contract_class_changes`. The OS updates the contract's `StateEntry` unconditionally and writes a valid proof for the block.

After the replacement, any subsequent call to the affected contract follows this path in `execute_entry_point`:

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

Reading an undeclared class hash from `contract_class_changes` returns `0` (the Cairo dict default for uninitialized keys). `find_element` then searches for a compiled class with hash `0`. If none exists, the hint fails and no valid proof can be generated for any transaction calling the broken contract. The sequencer is permanently unable to include such transactions.

Since `replace_class` can only be invoked from within the contract's own execution, and the contract can no longer execute after its class hash is broken, there is no recovery path.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Any funds held by the contract (native ETH, ERC20 tokens, or other assets) become permanently inaccessible. The contract cannot be called, cannot call `replace_class` again to self-repair, and no external mechanism exists to restore a valid class hash. The OS produces a valid proof for the `replace_class` call itself, so the state transition is accepted and finalized on L1.

---

### Likelihood Explanation

The attack is reachable by any unprivileged user who:
- Deploys a contract (or controls an existing contract) that holds other users' funds, and
- Calls any function in that contract that invokes `replace_class` with an arbitrary undeclared felt value (e.g., `0xdeadbeef` or any random hash with no corresponding Sierra class).

No privileged access, leaked key, or operator collusion is required. The OS enforces no constraint on the new class hash value. The attack is one transaction.

---

### Recommendation

In `execute_replace_class`, after reading `class_hash` from the request, validate that it is a declared class by reading its compiled class hash from `contract_class_changes` and asserting it is non-zero:

```cairo
let class_hash = request.class_hash;

// Validate that the class hash is declared.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
with_attr error_message("Class hash is not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

This mirrors the validation already performed implicitly in `execute_entry_point` and makes the constraint explicit and enforced at the syscall boundary.

---

### Proof of Concept

1. Deploy `VaultContract` — a contract that accepts user deposits and exposes a `break_vault()` function that calls `replace_class(0xdeadbeef)` (an undeclared hash).
2. Multiple users deposit funds into `VaultContract`.
3. Attacker calls `break_vault()`. The OS processes the `replace_class` syscall via `execute_replace_class`, skips the missing declared-class check, updates `VaultContract`'s `StateEntry.class_hash` to `0xdeadbeef`, and includes the transaction in a proven block.
4. Any subsequent transaction attempting to call `VaultContract` (e.g., `withdraw()`) causes `execute_entry_point` to read compiled class hash `0` for `0xdeadbeef`, fail to find it in `compiled_class_facts_bundle`, and produce an unprovable execution. The sequencer cannot include these transactions.
5. All deposited funds are permanently frozen with no recovery path.

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
