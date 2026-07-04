### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Permanent Fund Freezing — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS does not validate that the caller-supplied class hash is a declared class. Any contract can replace its class hash with an arbitrary, undeclared value. Once committed to the global state root, the contract becomes permanently non-executable and all funds it holds are frozen.

---

### Finding Description

In `syscall_impls.cairo`, `execute_replace_class` reads `class_hash` directly from the user-controlled syscall request and writes it into `contract_state_changes` with no validation:

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

The TODO comment at line 898 explicitly acknowledges the missing check. The `class_hash` value is taken from `request.class_hash` (fully attacker-controlled) and written into the contract's `StateEntry` without verifying that a class with that hash has been declared via `contract_class_changes`.

**Structural analogy to the Ajna bug:** In Ajna, `removeQuoteToken()` correctly updates the bankruptcy flag when all quote tokens are removed, but `moveQuoteToken()` — which has the same effect on the source bucket — omits that update, leaving the bucket in an inconsistent state. Here, `execute_declare_transaction` correctly enforces that a class hash is valid before recording it in `contract_class_changes`:

```cairo
// Note that prev_value=0 enforces that a class may be declared only once.
assert_not_zero(compiled_class_hash);
dict_update{dict_ptr=contract_class_changes}(
    key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
);
``` [2](#0-1) 

But `execute_replace_class` — which also writes a class hash into contract state — omits the equivalent invariant check entirely. Both operations mutate the class hash field of a contract's `StateEntry`, but only the declare path enforces validity.

The updated `StateEntry` flows into `state_update` → `compute_contract_state_commitment`, where it is committed to the global state root:

```cairo
let contract_state_tree_update_output = compute_contract_state_commitment(
    contract_state_changes_start=squashed_contract_state_changes_start,
    n_contract_state_changes=n_contract_state_changes,
    patricia_update_constants=patricia_update_constants,
);
``` [3](#0-2) 

Once committed, the invalid class hash is part of the proven state and cannot be reversed without a protocol-level intervention.

---

### Impact Explanation

If a contract's class hash is replaced with an undeclared hash:

1. The global state root records the invalid class hash as the contract's authoritative class.
2. Every future call to the contract attempts to load the class, fails (class not found), and reverts.
3. All funds (ERC-20 token balances, any storage-backed assets) held by the contract become permanently inaccessible.
4. The state is proven on L1 and is irreversible.

**Impact: Critical — Permanent freezing of funds.**

---

### Likelihood Explanation

The `replace_class` syscall is dispatched for any contract during execution via `execute_syscalls`:

```cairo
if (selector == REPLACE_CLASS_SELECTOR) {
    execute_replace_class(contract_address=execution_context.execution_info.contract_address);
    ...
}
``` [4](#0-3) 

An unprivileged transaction sender can:

1. Deploy a contract that holds funds (or find an existing contract whose logic allows an external caller to trigger `replace_class`).
2. Invoke `replace_class` with an arbitrary felt value that is not a declared class hash.
3. The OS accepts the call without validation and commits the invalid state.
4. The contract's funds are permanently frozen.

The OS is the last line of defense for protocol invariants. The absence of this check means any contract with a `replace_class` code path — intentional or exploitable — is vulnerable.

---

### Recommendation

Before writing the new `StateEntry`, verify that `class_hash` is present in `contract_class_changes` with a non-zero compiled class hash. Concretely, perform a lookup analogous to the one used in `execute_get_class_hash_at` against the class changes dictionary, and revert the syscall (write a failure response) if the class is not declared.

---

### Proof of Concept

1. Attacker deploys contract `C` that holds 10,000 STRK tokens in its storage.
2. Attacker sends an invoke transaction calling a function in `C` that issues the `replace_class` syscall with `class_hash = 0xdeadbeef` (an arbitrary undeclared felt).
3. `execute_replace_class` in the OS reads `request.class_hash = 0xdeadbeef`, skips the missing validation, and calls `dict_update` to set `C`'s class hash to `0xdeadbeef` in `contract_state_changes`.
4. `state_update` squashes and commits this entry to the global state root, which is proven on L1.
5. Any subsequent call to `C` (e.g., to transfer the 10,000 STRK) attempts to load class `0xdeadbeef`, finds no compiled class, and reverts.
6. The 10,000 STRK tokens in `C`'s storage are permanently inaccessible.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L815-819)
```text
    // Note that prev_value=0 enforces that a class may be declared only once.
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/state.cairo (L70-74)
```text
    let contract_state_tree_update_output = compute_contract_state_commitment(
        contract_state_changes_start=squashed_contract_state_changes_start,
        n_contract_state_changes=n_contract_state_changes,
        patricia_update_constants=patricia_update_constants,
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_syscalls.cairo (L195-203)
```text
    if (selector == REPLACE_CLASS_SELECTOR) {
        execute_replace_class(contract_address=execution_context.execution_info.contract_address);
        %{ OsLoggerExitSyscall %}
        return execute_syscalls(
            block_context=block_context,
            execution_context=execution_context,
            syscall_ptr_end=syscall_ptr_end,
        );
    }
```
