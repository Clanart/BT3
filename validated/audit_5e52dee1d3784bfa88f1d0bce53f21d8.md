### Title
Missing Class Hash Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS does not verify that the replacement class hash corresponds to a previously declared contract class. Any contract can call `replace_class` with an arbitrary, undeclared class hash. The OS will accept and commit this state change. Once committed, the contract becomes permanently uncallable, freezing any funds it holds.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function reads the requested class hash directly from the syscall request and writes it into the contract state without any validation:

```cairo
func execute_replace_class{...}(contract_address: felt) {
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
    ...
}
```

The inline `TODO` comment at line 898 explicitly acknowledges the missing check: *"Check that there is a declared contract class with the given hash."* No such check exists. The `class_hash` field from the request is written directly into `contract_state_changes` without consulting `contract_class_changes` to confirm the hash is declared.

This is the direct analog of the BridgeRoles report's "Direct Role Transfer" finding: just as `transferBtcBridge` immediately overwrites the role without verifying the new address is valid, `execute_replace_class` immediately overwrites the contract's class hash without verifying the new hash is declared.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once a contract's class hash is set to an undeclared value, every future call to that contract will fail at class resolution time. There is no recovery path in the OS: the state transition is committed to the Patricia Merkle Tree, and no mechanism exists to revert a committed class replacement.

Attack scenario:
1. Attacker deploys a contract (e.g., a vault or token contract) that accepts user deposits.
2. Users deposit funds into the contract.
3. The attacker's contract calls `replace_class` with an arbitrary felt value that has never been declared (e.g., `0xdeadbeef`).
4. The OS writes the undeclared class hash into `contract_state_changes` with no validation.
5. The state is committed. All future calls to the contract fail because the class does not exist.
6. Deposited funds are permanently frozen with no withdrawal path.

---

### Likelihood Explanation

**Medium.**

- Any unprivileged user can deploy a contract on StarkNet.
- Any contract can issue the `replace_class` syscall with an arbitrary class hash.
- The OS imposes zero constraints on the new class hash value.
- The attack requires social engineering (convincing users to deposit into the attacker's contract), but this is a well-established attack pattern (malicious vault / rug-pull variant).
- Accidental triggering is also possible: a legitimate contract with a bug in its upgrade logic could call `replace_class` with an invalid hash, permanently bricking itself and freezing any funds it holds.

---

### Recommendation

Before committing the class hash update in `execute_replace_class`, verify that the requested class hash exists in `contract_class_changes` (or the underlying class commitment tree). Specifically:

1. Add `contract_class_changes: DictAccess*` as an implicit argument to `execute_replace_class`.
2. Perform a `dict_read` on `contract_class_changes` with `key=class_hash` and assert the returned value is non-zero (i.e., the class has been declared with a valid `compiled_class_hash`).
3. Additionally, assert `class_hash != UNINITIALIZED_CLASS_HASH` to prevent resetting a contract to the uninitialized sentinel value.

---

### Proof of Concept

**Attacker-controlled entry path:**

1. Attacker deploys `MaliciousVault` contract (class declared normally, class hash `C_valid`).
2. Users call `MaliciousVault.deposit()`, transferring ETH/STRK into the contract.
3. Attacker sends an invoke transaction calling an internal function of `MaliciousVault` that issues:
   ```
   replace_class(class_hash=0x1234567890abcdef)  // never declared
   ```
4. The OS executes `execute_replace_class`:
   - Reads `class_hash = 0x1234567890abcdef` from the syscall request.
   - Reads the current `StateEntry` for `MaliciousVault`.
   - Writes `new StateEntry(class_hash=0x1234567890abcdef, ...)` into `contract_state_changes`.
   - **No check against `contract_class_changes` is performed.**
5. The block is proven and committed. `MaliciousVault`'s class hash is now `0x1234567890abcdef`.
6. Any subsequent call to `MaliciousVault` (including `withdraw()`) fails at class resolution. Funds are permanently frozen.

**Relevant code location:** [1](#0-0) 

The acknowledged missing check is at: [2](#0-1) 

The contrast with `deploy_contract`, which correctly validates `state_entry.class_hash = UNINITIALIZED_CLASS_HASH` before writing, shows the OS does enforce class-state invariants elsewhere — but not in `execute_replace_class`: [3](#0-2)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deploy_contract.cairo (L51-66)
```text
    local state_entry: StateEntry*;
    %{ GetContractAddressStateEntry %}
    assert state_entry.class_hash = UNINITIALIZED_CLASS_HASH;
    assert state_entry.nonce = 0;

    tempvar new_state_entry = new StateEntry(
        class_hash=constructor_execution_context.class_hash,
        storage_ptr=state_entry.storage_ptr,
        nonce=0,
    );

    dict_update{dict_ptr=contract_state_changes}(
        key=contract_address,
        prev_value=cast(state_entry, felt),
        new_value=cast(new_state_entry, felt),
    );
```
