### Title
Missing Class Hash Validation in `execute_replace_class` Allows Permanent Freezing of Funds - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS does not verify that the replacement class hash corresponds to a previously declared contract class. An attacker-controlled contract can call `replace_class` with an arbitrary undeclared felt value, permanently setting its class to an invalid hash. Any funds held by that contract become permanently frozen because no valid class bytecode can ever be found for it.

---

### Finding Description

In `execute_replace_class`, the OS reads the requested `class_hash` from the syscall request and immediately writes it into `contract_state_changes` without checking whether that hash exists in `contract_class_changes` (the declared-class registry):

```cairo
func execute_replace_class{
    ...
    contract_state_changes: DictAccess*,
    revert_log: RevertLogEntry*,
}(contract_address: felt) {
    ...
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

The TODO comment at line 898 is an explicit acknowledgment by the developers that this validation is absent. The implicit argument `contract_class_changes: DictAccess*` is **not** present in `execute_replace_class`'s signature, making it structurally impossible for the function to perform the required lookup: [2](#0-1) 

By contrast, `execute_declare_transaction` correctly enforces `prev_value=0` to prevent re-declaration, and `deploy_contract` enforces `state_entry.class_hash = UNINITIALIZED_CLASS_HASH` before deployment. No equivalent guard exists for `replace_class`. [3](#0-2) [4](#0-3) 

The state update flows through `squash_state_changes` and then `compute_contract_state_commitment`, which squash and commit the dict without any class-existence check: [5](#0-4) 

Once committed to the global state root, the invalid class hash is permanent.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

After `replace_class` writes an undeclared hash into `contract_state_changes`, every future call to that contract address will fail at class resolution time because no bytecode exists for the hash. The contract's storage (including any token balances, ETH, or other assets it holds) becomes permanently inaccessible. Because the state root is committed on-chain, there is no recovery path.

---

### Likelihood Explanation

The `replace_class` syscall is available to any deployed contract with no privilege requirement. An attacker can:

1. Deploy a contract whose constructor or any callable function invokes `replace_class` with an arbitrary felt (e.g., `0x1`).
2. Advertise the contract as a legitimate vault or token contract.
3. Wait for users to deposit funds.
4. Trigger the `replace_class` call.

The entry path is fully attacker-controlled and requires no privileged role, leaked key, or external dependency. The only prerequisite is the ability to deploy a contract and submit an invoke transaction — both are available to any unprivileged user.

---

### Recommendation

Add `contract_class_changes: DictAccess*` as an implicit argument to `execute_replace_class` and perform a `dict_read` on it with the requested `class_hash` as the key before updating `contract_state_changes`. Assert that the returned value is non-zero (i.e., the class has been declared). This mirrors the pattern already used in `execute_call_contract` and `execute_deploy`, which both read from `contract_state_changes` to validate the target class exists before proceeding.

---

### Proof of Concept

1. Attacker declares a valid class `C` and deploys contract `V` (a "vault") using class `C`.
2. Users call `V.deposit()` and transfer tokens into `V`; `V`'s storage now records their balances.
3. Attacker submits an invoke transaction calling `V.freeze()`, a function in class `C` that executes the `replace_class` syscall with `class_hash = 0xdead` (an undeclared felt).
4. The OS executes `execute_replace_class`: it reads `request.class_hash = 0xdead`, skips the missing declared-class check, and writes `StateEntry(class_hash=0xdead, ...)` into `contract_state_changes`.
5. `squash_state_changes` and `compute_contract_state_commitment` commit this entry into the new global state root.
6. All subsequent calls to `V` — including `V.withdraw()` — fail at class resolution because `0xdead` has no associated bytecode.
7. All user funds in `V` are permanently frozen with no recovery mechanism.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L878-884)
```text
func execute_replace_class{
    range_check_ptr,
    syscall_ptr: felt*,
    contract_state_changes: DictAccess*,
    revert_log: RevertLogEntry*,
}(contract_address: felt) {
    alloc_locals;
```

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/squash.cairo (L7-36)
```text
func squash_state_changes{range_check_ptr}(
    contract_state_changes_start: DictAccess*, contract_state_changes_end: DictAccess*
) -> (n_contract_state_changes: felt, squashed_contract_state_dict: DictAccess*) {
    alloc_locals;

    // State changes after squashing the outer dictionary.
    let (local squashed_dict: DictAccess*) = alloc();

    // Squash the global dictionary to get a list of triples (addr, dict_begin, dict_end).
    let (squashed_dict_end) = squash_dict(
        dict_accesses=contract_state_changes_start,
        dict_accesses_end=contract_state_changes_end,
        squashed_dict=squashed_dict,
    );

    local n_contract_state_changes = (squashed_dict_end - squashed_dict) / DictAccess.SIZE;

    // State changes after squashing the outer dictionary and the inner dictionaries.
    let (local fully_squashed_dict: DictAccess*) = alloc();
    squash_state_changes_inner(
        n_contract_state_changes=n_contract_state_changes,
        state_changes=squashed_dict,
        squashed_state_changes=fully_squashed_dict,
    );

    return (
        n_contract_state_changes=n_contract_state_changes,
        squashed_contract_state_dict=fully_squashed_dict,
    );
}
```
