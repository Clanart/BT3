### Title
Missing Class Existence Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS program does not verify that the new class hash supplied by a contract corresponds to a previously declared class. This is explicitly acknowledged in the code with a `TODO` comment. As a result, any contract can call `replace_class` with an arbitrary, undeclared class hash. Once the OS commits this invalid state, the contract becomes permanently uncallable, freezing all funds it holds.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function processes the `replace_class` syscall. After deducting gas, it reads the current `StateEntry` for the calling contract and writes a new `StateEntry` with the caller-supplied `class_hash` — with no check that this hash exists in the declared class registry (`contract_class_changes`):

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

The `contract_class_changes` dictionary (which maps `class_hash → compiled_class_hash`) is the authoritative registry of declared classes for the block. The OS validates compiled class facts post-execution in `validate_compiled_class_facts_post_execution` (called from `os.cairo`), but `execute_replace_class` never cross-references the new `class_hash` against this registry before writing it to `contract_state_changes`. [2](#0-1) 

The `StateEntry` struct stores `class_hash` as a plain `felt`. There is no type-level or runtime enforcement that it must correspond to a declared class: [3](#0-2) 

When the OS later tries to execute any entry point on a contract whose `class_hash` is undeclared, it cannot locate the compiled class facts for that hash. Because compiled class lookup is hint-driven (the prover supplies the class bytecode), a missing class causes an irrecoverable hint failure — the block cannot be proven, and the contract is permanently uncallable.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Any contract that calls `replace_class` with an undeclared class hash will have its `class_hash` field in the global state set to an invalid value. All subsequent calls to that contract — including calls needed to withdraw or transfer any ERC-20 tokens, ETH, or other assets held in its storage — will fail at the OS execution layer because the class bytecode cannot be resolved. The funds are permanently inaccessible with no recovery path, since the contract itself cannot be called to self-correct.

---

### Likelihood Explanation

The entry path is fully reachable by any unprivileged contract deployer:

1. An attacker deploys a contract (or uses an existing contract they control).
2. The contract executes the `replace_class` syscall with an arbitrary felt value that does not correspond to any declared class hash.
3. The OS processes the syscall, deducts gas, and writes the invalid `class_hash` into `contract_state_changes` without any existence check.
4. The state is committed. The contract is now permanently uncallable.

No privileged role, leaked key, or external dependency is required. The `replace_class` syscall is a standard, publicly accessible syscall available to any Sierra/CASM contract. The missing check is explicitly flagged in the source with a `TODO` dated `1/1/2026`, confirming the developers are aware the validation is absent.

---

### Recommendation

Before writing the new `StateEntry`, verify that `class_hash` exists in `contract_class_changes` (i.e., has a non-zero `compiled_class_hash` mapping). Concretely, perform a `dict_read` on `contract_class_changes` keyed by `request.class_hash` and assert the result is non-zero before proceeding with the `dict_update` on `contract_state_changes`. This mirrors the enforcement already present in `execute_declare_transaction`, where `prev_value=0` is used to guarantee a class is declared exactly once before it can be used. [4](#0-3) 

---

### Proof of Concept

1. Declare a valid class `C` and deploy a contract `A` using class `C`. Fund contract `A` with tokens.
2. From contract `A`, issue the `replace_class` syscall with `new_class_hash = 0xdeadbeef` (an arbitrary felt never declared on-chain).
3. The OS executes `execute_replace_class`: gas is deducted, `state_entry` is fetched, and `dict_update` writes `StateEntry(class_hash=0xdeadbeef, ...)` for contract `A`'s address — no existence check fires.
4. The block is sequenced and the state root is updated to reflect `A.class_hash = 0xdeadbeef`.
5. Any subsequent transaction attempting to call contract `A` (e.g., to withdraw funds) reaches the OS entry-point dispatch. The OS attempts to resolve compiled class facts for `0xdeadbeef`, finds none, and the hint fails — the block cannot be proven.
6. All funds in contract `A` are permanently frozen with no recovery mechanism. [5](#0-4)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L877-916)
```text
// Replaces the class.
func execute_replace_class{
    range_check_ptr,
    syscall_ptr: felt*,
    contract_state_changes: DictAccess*,
    revert_log: RevertLogEntry*,
}(contract_address: felt) {
    alloc_locals;
    let request = cast(syscall_ptr + RequestHeader.SIZE, ReplaceClassRequest*);

    // Reduce gas.
    let success = reduce_syscall_gas_and_write_response_header(
        total_gas_cost=REPLACE_CLASS_GAS_COST, request_struct_size=ReplaceClassRequest.SIZE
    );
    if (success == FALSE) {
        // Not enough gas to execute the syscall.
        return ();
    }

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

    return ();
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/os.cairo (L114-120)
```text
    // Validate the guessed compile class facts.
    let compiled_class_facts_bundle = os_global_context.compiled_class_facts_bundle;
    validate_compiled_class_facts_post_execution(
        n_compiled_class_facts=compiled_class_facts_bundle.n_compiled_class_facts,
        compiled_class_facts=compiled_class_facts_bundle.compiled_class_facts,
        builtin_costs=compiled_class_facts_bundle.builtin_costs,
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/commitment.cairo (L25-29)
```text
struct StateEntry {
    class_hash: felt,
    storage_ptr: DictAccess*,
    nonce: felt,
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L814-819)
```text
    // Declare the class hash.
    // Note that prev_value=0 enforces that a class may be declared only once.
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```
