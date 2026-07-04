### Title
Missing Declared-Class Validation in `execute_replace_class` Enables Permanent Freezing of Contract Funds - (File: `execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall in the StarkNet OS accepts any arbitrary class hash as the replacement without verifying that the hash corresponds to a previously declared class. A contract can therefore replace its own class with an undeclared hash, making itself permanently non-callable and freezing all funds it holds. The OS itself contains a `TODO` acknowledging this missing check.

---

### Finding Description

In `syscall_impls.cairo`, `execute_replace_class` reads the requested new class hash directly from the syscall request and writes it into `contract_state_changes` with no validation:

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

The `TODO` comment at line 898 is an explicit in-code acknowledgment that the OS does **not** check whether `class_hash` is a declared class.

When a subsequent call is made to the contract whose class was replaced with an undeclared hash, `execute_entry_point` performs:

```cairo
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=execution_context.class_hash
);
``` [2](#0-1) 

For an undeclared class hash, `dict_read` returns `0`. The OS then calls `find_element` searching for a compiled class with hash `0`:

```cairo
let (compiled_class_fact: CompiledClassFact*) = find_element(
    array_ptr=compiled_class_facts_bundle.compiled_class_facts,
    elm_size=CompiledClassFact.SIZE,
    n_elms=compiled_class_facts_bundle.n_compiled_class_facts,
    key=compiled_class_hash,
);
``` [3](#0-2) 

`find_element` panics (Cairo assertion failure) when the key is absent. The sequencer therefore permanently rejects every transaction targeting the contract, making it non-callable and all funds inside it irrecoverable.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once a contract's class hash is set to an undeclared value, no transaction can ever successfully execute against it. Any ERC-20 balances, ETH, or protocol-specific assets held in the contract's storage are permanently inaccessible. There is no OS-level recovery path: the state is committed on-chain and the class replacement is irreversible once the block is finalized.

---

### Likelihood Explanation

The attack path is fully reachable by an unprivileged transaction sender:

1. Attacker deploys a contract that exposes a callable function invoking `replace_class(<undeclared_hash>)`.
2. Users deposit funds into the contract (e.g., a yield vault, DEX pool, or lending market).
3. Attacker calls the trigger function; the OS executes `execute_replace_class` and writes the undeclared hash into state with no validation.
4. The block is finalized with the corrupted class hash committed.
5. All subsequent calls to the contract fail at the OS level; funds are permanently frozen.

A buggy contract that accidentally passes an undeclared hash to `replace_class` produces the same outcome without any attacker intent. The missing check is a single-point-of-failure with no compensating control anywhere in the OS execution path.

---

### Recommendation

Before writing the new class hash into `contract_state_changes`, verify that it is a declared class by asserting that its compiled class hash is non-zero:

```cairo
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
with_attr error_message("Class hash is not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

This mirrors the invariant already enforced during `execute_declare_transaction`, which asserts `assert_not_zero(compiled_class_hash)` before writing to `contract_class_changes`. [4](#0-3) 

---

### Proof of Concept

1. Deploy contract `Vault` with:
   - `deposit()` — accepts user funds.
   - `freeze()` — calls `replace_class(0x1337dead)` where `0x1337dead` is never declared.

2. Users call `deposit()`, locking funds in `Vault`.

3. Attacker calls `freeze()`. The OS executes `execute_replace_class`:
   - `class_hash = 0x1337dead` is written to `contract_state_changes` with no validation.
   - `revert_log` records the old class hash (but the transaction is not reverted).

4. Block is finalized. `Vault`'s class hash in the committed state is `0x1337dead`.

5. Any user attempts `withdraw()`:
   - `execute_entry_point` calls `dict_read(key=0x1337dead)` → returns `0`.
   - `find_element(..., key=0)` → Cairo panic; OS execution fails.
   - Sequencer rejects the transaction.

6. All funds in `Vault` are permanently frozen with no recovery mechanism at the protocol level.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L154-156)
```text
    let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
        key=execution_context.class_hash
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L161-166)
```text
    let (compiled_class_fact: CompiledClassFact*) = find_element(
        array_ptr=compiled_class_facts_bundle.compiled_class_facts,
        elm_size=CompiledClassFact.SIZE,
        n_elms=compiled_class_facts_bundle.n_compiled_class_facts,
        key=compiled_class_hash,
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L816-819)
```text
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```
