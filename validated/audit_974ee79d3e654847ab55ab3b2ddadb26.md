### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Permanent Contract Freezing — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS does not verify that the replacement class hash has been declared in `contract_class_changes`. A contract can replace its own class with an arbitrary, undeclared hash. Any subsequent call to that contract will fail at the OS level because no valid class exists for the new hash, permanently freezing all funds held by the contract.

---

### Finding Description

In `execute_replace_class` (lines 878–916 of `syscall_impls.cairo`), the OS reads the requested `class_hash` from the syscall request and immediately writes it into `contract_state_changes` without checking whether that hash was ever declared:

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

The function signature does not even include `contract_class_changes` as an implicit argument, making it structurally impossible to perform the check:

```cairo
func execute_replace_class{
    range_check_ptr,
    syscall_ptr: felt*,
    contract_state_changes: DictAccess*,
    revert_log: RevertLogEntry*,
}(contract_address: felt) {
``` [2](#0-1) 

By contrast, `execute_declare_transaction` correctly enforces that a class hash is only registered once by writing `prev_value=0` into `contract_class_changes`:

```cairo
dict_update{dict_ptr=contract_class_changes}(
    key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
);
``` [3](#0-2) 

The `execute_replace_class` handler never cross-references `contract_class_changes`, so any felt value is accepted as a valid replacement class hash.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once a contract's class hash is overwritten with an undeclared hash, every subsequent call to that contract will fail at the OS execution layer because no class body can be resolved for the hash. The contract becomes permanently inoperable. Any ERC-20 tokens, ETH, or other assets held in that contract's storage are irrecoverably frozen. There is no on-chain mechanism to reverse a committed state update.

---

### Likelihood Explanation

The `replace_class` syscall is available to any contract. A malicious contract deployer can:

1. Deploy a contract that accepts user deposits.
2. After accumulating funds, invoke `replace_class` with an arbitrary undeclared felt value.
3. The OS accepts the state update without complaint.
4. All deposited funds are permanently frozen.

No privileged role, leaked key, or external dependency is required. The attack is fully self-contained and executable by any unprivileged contract deployer.

---

### Recommendation

Add `contract_class_changes: DictAccess*` as an implicit argument to `execute_replace_class` and perform a `dict_read` to assert that the requested `class_hash` maps to a non-zero compiled class hash before committing the state update. This mirrors the existing pattern used in `execute_declare_transaction`, which enforces `prev_value=0` to guarantee uniqueness of declarations.

---

### Proof of Concept

1. Deploy contract `A` that holds user funds and exposes an `upgrade(new_hash: felt)` entry point that calls `replace_class(new_hash)`.
2. Users deposit funds into `A`.
3. Attacker calls `upgrade(0xdeadbeef)` — an arbitrary felt that was never declared.
4. The OS executes `execute_replace_class`, writes `class_hash=0xdeadbeef` into `contract_state_changes` for contract `A`, and commits the block.
5. All subsequent calls to contract `A` fail because `0xdeadbeef` has no entry in `contract_class_changes`.
6. All funds in `A` are permanently frozen with no recovery path.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L817-819)
```text
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```
