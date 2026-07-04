### Title
Missing Zero/Undeclared Class Hash Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` function in the StarkNet OS Cairo program does not validate that the caller-supplied `class_hash` is non-zero or corresponds to a declared class. An unprivileged contract can invoke the `replace_class` syscall with `class_hash = 0`, causing the OS to write `UNINITIALIZED_CLASS_HASH` (0) into the contract's state entry. Any subsequent execution of that contract will cause `find_element` in `execute_entry_point` to panic on an undeclared compiled class, permanently bricking the contract and freezing any funds it holds.

---

### Finding Description

In `syscall_impls.cairo`, `execute_replace_class` reads `class_hash` directly from the syscall request and writes it into `contract_state_changes` with no validation:

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

The embedded TODO comment explicitly acknowledges the missing check. There is no `assert_not_zero(class_hash)` and no verification that `class_hash` maps to a compiled class in `contract_class_changes`.

Contrast this with `execute_declare_transaction`, which **does** enforce a non-zero compiled class hash before writing to state:

```cairo
assert_not_zero(compiled_class_hash);
dict_update{dict_ptr=contract_class_changes}(
    key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
);
``` [2](#0-1) 

When a contract's class hash is set to 0, any subsequent call to that contract reaches `execute_entry_point`, which performs:

```cairo
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=execution_context.class_hash   // = 0
);
// compiled_class_hash is now 0 (no class declared for hash 0)

let (compiled_class_fact: CompiledClassFact*) = find_element(
    array_ptr=compiled_class_facts_bundle.compiled_class_facts,
    elm_size=CompiledClassFact.SIZE,
    n_elms=compiled_class_facts_bundle.n_compiled_class_facts,
    key=compiled_class_hash,           // = 0
);
``` [3](#0-2) 

`find_element` panics (hard failure, not a graceful revert) when the key is absent. No compiled class fact will ever have hash 0, so the OS execution aborts, making the block unprovable for any block that includes a call to the bricked contract.

Additionally, `deploy_contract` uses `UNINITIALIZED_CLASS_HASH` (= 0) as the sentinel for an undeployed address:

```cairo
assert state_entry.class_hash = UNINITIALIZED_CLASS_HASH;
``` [4](#0-3) 

Setting a live contract's class hash back to 0 collapses it to the uninitialized sentinel, but the nonce is non-zero so redeployment is also blocked, making the freeze permanent.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Any ERC-20 tokens, ETH, or other assets held in the storage of the bricked contract become permanently inaccessible. The contract cannot execute (class lookup panics), cannot be redeployed (nonce guard), and cannot be recovered through any on-chain mechanism. This matches the "permanent freezing of funds" impact category exactly.

---

### Likelihood Explanation

The `replace_class` syscall is callable by any contract from its own execution context. An attacker can:

1. Deploy a contract whose `__execute__` function calls `replace_class(0)`.
2. Send a single invoke transaction to that contract.
3. The OS processes the syscall, writes `class_hash = 0` to state, and the contract is permanently bricked.

No privileged role, leaked key, or operator cooperation is required. The entry path is a standard unprivileged invoke transaction. A buggy contract (e.g., one that reads the replacement class hash from calldata without validation) can be exploited by any caller passing `0` as the argument.

---

### Recommendation

Add an explicit non-zero check and a declared-class check in `execute_replace_class`, mirroring the guard already present in `execute_declare_transaction`:

```cairo
// Reject class_hash = 0 (UNINITIALIZED_CLASS_HASH).
assert_not_zero(class_hash);

// Verify the class has been declared (compiled_class_hash != 0).
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
assert_not_zero(compiled_class_hash);
```

This resolves the acknowledged TODO and closes the permanent-freeze vector at negligible gas cost.

---

### Proof of Concept

1. Deploy contract `Bricker` whose `__execute__` entry point issues `replace_class(class_hash=0)`.
2. Send an invoke transaction calling `Bricker.__execute__`.
3. The OS processes `execute_replace_class`: no zero check fires; `contract_state_changes` is updated with `class_hash = 0` for `Bricker`'s address.
4. Any ETH/token balance stored in `Bricker`'s storage slots is now permanently frozen.
5. Any subsequent block that includes a call to `Bricker` will reach `execute_entry_point` with `class_hash = 0`, causing `find_element` to panic and the block to be unprovable.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L816-819)
```text
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deploy_contract.cairo (L53-53)
```text
    assert state_entry.class_hash = UNINITIALIZED_CLASS_HASH;
```
