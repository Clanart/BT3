### Title
Missing Declared Class Validation in `execute_replace_class` Enables Permanent Freezing of Funds - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS does not verify that the replacement class hash has been declared on-chain. Any contract can call `replace_class` with an arbitrary, undeclared class hash. The OS commits this invalid state to the contract state trie without validation, permanently rendering the contract unexecutable and freezing all funds held within it.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function reads the requested `class_hash` directly from the syscall request and updates `contract_state_changes` without checking whether that hash exists in `contract_class_changes` (the declared class registry). The code itself contains an explicit acknowledgment of this missing check:

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

The OS enforces no constraint that `class_hash` must be a key present in `contract_class_changes`. By contrast, the `execute_declare_transaction` path enforces `prev_value=0` to prevent re-declaration, and `deploy_contract` enforces `UNINITIALIZED_CLASS_HASH` before deployment — but `execute_replace_class` has no symmetric enforcement on the destination class. [2](#0-1) [3](#0-2) 

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once `contract_state_changes` is committed via `state_update` and the Patricia Merkle Tree is updated, the contract's class hash in the global state permanently points to an undeclared class. The OS compiled-class-facts bundle, validated post-execution in `validate_compiled_class_facts_post_execution`, will never contain a matching entry for this hash. All subsequent transactions targeting this contract will be unexecutable by any sequencer, because no valid compiled class exists for the stored hash. Any ERC-20 tokens or ETH held by the contract are irrecoverably frozen. [4](#0-3) [5](#0-4) 

---

### Likelihood Explanation

**Medium.** The attack requires an unprivileged user to:
1. Deploy a contract (standard `deploy_account` or `deploy` syscall — no privilege required).
2. Have that contract invoke `replace_class` with an arbitrary felt value not present in the declared class registry.

No trusted role, leaked key, or network-level capability is needed. The `execute_replace_class` syscall is reachable by any contract executing within a normal transaction. A realistic attack vector is a malicious vault or pool contract that accepts user deposits and then calls `replace_class` with a crafted undeclared hash, permanently freezing deposited funds.

---

### Recommendation

Before committing the new class hash to `contract_state_changes`, the OS must assert that `class_hash` is present as a key in `contract_class_changes` with a non-zero compiled class hash. Concretely, a `dict_read` on `contract_class_changes` for `class_hash` should be performed, and the result must be asserted non-zero (`UNINITIALIZED_CLASS_HASH` must be rejected). This mirrors the invariant already enforced for class declaration (`prev_value=0` in `dict_update` for `contract_class_changes`).

---

### Proof of Concept

1. Attacker deploys `MaliciousVault` — a contract that accepts ERC-20 deposits and exposes a `drain()` function.
2. Users deposit tokens into `MaliciousVault`, trusting its published source code.
3. Attacker calls `drain()`, which internally invokes the `replace_class` syscall with `class_hash = 0xdeadbeef` (any felt not present in the declared class registry).
4. The OS executes `execute_replace_class`:
   - Reads `class_hash = 0xdeadbeef` from the syscall request.
   - Skips the missing declared-class check (TODO at line 898).
   - Writes `new StateEntry(class_hash=0xdeadbeef, ...)` into `contract_state_changes`.
5. `state_update` commits this entry to the Patricia Merkle Tree; the global state root now encodes `MaliciousVault.class_hash = 0xdeadbeef`.
6. All future `invoke` transactions targeting `MaliciousVault` are rejected by every sequencer: no compiled class for `0xdeadbeef` exists, so execution cannot proceed.
7. All deposited user funds are permanently frozen with no recovery path. [6](#0-5) [7](#0-6)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L878-916)
```text
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L815-819)
```text
    // Note that prev_value=0 enforces that a class may be declared only once.
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deploy_contract.cairo (L51-54)
```text
    local state_entry: StateEntry*;
    %{ GetContractAddressStateEntry %}
    assert state_entry.class_hash = UNINITIALIZED_CLASS_HASH;
    assert state_entry.nonce = 0;
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/os.cairo (L228-238)
```text
    // Update the state.
    %{ EnterScopeWithAliases %}
    let (squashed_os_state_update, state_update_output) = state_update{hash_ptr=pedersen_ptr}(
        os_state_update=OsStateUpdate(
            contract_state_changes_start=contract_state_changes_start,
            contract_state_changes_end=contract_state_changes,
            contract_class_changes_start=contract_class_changes_start,
            contract_class_changes_end=contract_class_changes,
        ),
        should_allocate_aliases=should_allocate_aliases(),
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
