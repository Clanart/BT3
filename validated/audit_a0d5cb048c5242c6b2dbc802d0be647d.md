### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Permanent Contract Freezing — (File: `execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS does not verify that the replacement class hash corresponds to a previously declared class. An unprivileged contract caller can invoke `replace_class` with an arbitrary, undeclared class hash. The OS will commit this invalid class hash into the state without complaint, permanently making the contract non-executable and freezing any funds it holds. The missing check is explicitly acknowledged by a TODO comment in the production code.

---

### Finding Description

In `syscall_impls.cairo`, `execute_replace_class` reads the requested class hash from the syscall request and directly writes it into `contract_state_changes` with no cross-reference against `contract_class_changes` (the dictionary that tracks declared classes):

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

The function has access to `contract_class_changes` (it is an implicit argument in the broader syscall dispatch chain), but the check is simply absent. The TODO comment at line 898 is the only guard — it is not enforced at the Cairo constraint level.

The `contract_class_changes` dictionary is populated by `execute_declare_transaction` via `dict_update` with `prev_value=0` (enforcing uniqueness): [2](#0-1) 

Because `execute_replace_class` never consults this dictionary, any felt value — including one that was never declared — is accepted as a valid new class hash.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once a contract's class hash is set to an undeclared value and the block is committed:

1. The state root permanently records the invalid class hash for that contract address.
2. Every future transaction targeting that contract will cause the OS to look up the class in its compiled-class facts bundle and fail to find it.
3. The contract becomes permanently non-executable.
4. All token balances, collateral, or other assets held in the contract's storage are inaccessible forever — there is no recovery path because the contract cannot execute any withdrawal or upgrade logic.

This satisfies the **Critical: Permanent freezing of funds** impact category.

---

### Likelihood Explanation

**High.** The attack requires only that a contract exposes any code path that calls the `replace_class` syscall — which is a standard, documented StarkNet syscall available to every Cairo 1 contract. The attacker does not need any privileged role, leaked key, or external dependency. The entry path is:

- An unprivileged user (contract deployer or caller) deploys or interacts with a contract that calls `replace_class`.
- The user supplies an arbitrary felt as the class hash (e.g., `1` or any value not in `contract_class_changes`).
- The OS accepts it without constraint.

Any DeFi vault, multisig, or token contract that includes an upgrade mechanism via `replace_class` is directly exploitable by any caller who can reach that code path.

---

### Recommendation

Before writing the new class hash into `contract_state_changes`, verify that the class hash exists in `contract_class_changes`. Concretely, perform a `dict_read` on `contract_class_changes` with `key=class_hash` and assert that the returned compiled class hash is non-zero (i.e., the class was declared). This mirrors the enforcement already applied in `execute_declare_transaction`, which uses `prev_value=0` to guarantee a class is declared at most once. [3](#0-2) 

---

### Proof of Concept

1. **Setup**: Attacker deploys `VaultContract` (a contract holding user ETH/STRK deposits). `VaultContract` contains an upgrade function that calls `replace_class(new_class_hash)`.

2. **Attack**: Attacker calls `VaultContract.upgrade(0xdeadbeef)` where `0xdeadbeef` is a felt that was never passed to a `declare` transaction.

3. **OS processing**: `execute_replace_class` is invoked. It reads `class_hash = 0xdeadbeef` from the syscall request. The TODO-guarded check is absent. `dict_update` writes `StateEntry(class_hash=0xdeadbeef, ...)` into `contract_state_changes`.

4. **State commitment**: `state_update` in `os.cairo` squashes and commits the state. The Merkle root now permanently records `VaultContract → class_hash=0xdeadbeef`. [4](#0-3) 

5. **Consequence**: Every subsequent transaction targeting `VaultContract` fails at class lookup. All deposited funds are permanently frozen. No withdrawal, migration, or rescue is possible.

**Analogy to the reference report**: Just as the Paraspace flash-claim vulnerability allowed returning a valueless NFT because the post-return health-factor check was missing, this vulnerability allows committing an invalid class hash because the post-`replace_class` declared-class check is missing. In both cases, the system transitions into an irrecoverable invalid state due to a single absent validation.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L816-819)
```text
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/os.cairo (L230-238)
```text
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
