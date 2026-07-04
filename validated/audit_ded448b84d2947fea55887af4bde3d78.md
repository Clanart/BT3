### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Permanent Fund Freezing — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS accepts any arbitrary felt value as a new class hash without verifying that the class hash corresponds to a previously declared contract class. A contract deployer can call `replace_class` with an undeclared (or zero) class hash, permanently setting the contract's on-chain class to an invalid value. Any subsequent call to that contract will be unexecutable by the OS, permanently freezing all funds held in the contract.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function processes the `REPLACE_CLASS` syscall:

```cairo
func execute_replace_class{
    range_check_ptr,
    syscall_ptr: felt*,
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
    ...
}
```

The developer-acknowledged TODO at line 898 confirms the missing check. The OS Cairo code unconditionally writes `request.class_hash` — which can be any felt, including `0` or any undeclared hash — into the contract's `StateEntry` in `contract_state_changes`. No assertion against the `contract_class_changes` dictionary (which tracks declared classes) is performed.

This is the direct analog of the external report: just as `BaseBridgeReceiver.setLocalTimelock` failed to verify the new address implements `ITimelock`, `execute_replace_class` fails to verify the new class hash is a declared class. In both cases, the critical state variable is updated to an invalid value with no recovery path. [1](#0-0) 

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once `execute_replace_class` commits an undeclared class hash to the contract's `StateEntry`, the state commitment (Patricia Merkle Tree root) is updated to reflect this invalid class hash. The OS proof is generated and accepted by the L1 verifier. From that point:

1. Any future transaction targeting the contract causes the OS to look up the class hash in the class tree.
2. The class hash is not present in the class tree (it was never declared).
3. The OS cannot produce a valid execution trace for the contract.
4. All assets (ERC-20 balances, NFTs, protocol TVL) stored in the contract's storage are permanently inaccessible — no withdrawal, transfer, or upgrade is possible.

There is no recovery mechanism: the contract cannot call `replace_class` again because calling any entry point on the contract requires the OS to execute the (now-invalid) class.

---

### Likelihood Explanation

**High.** The entry path requires only that a contract deployer (an unprivileged role — anyone can deploy a contract on StarkNet) writes a contract that calls `replace_class` with an undeclared class hash. This can be:

- **Intentional self-sabotage by a rug-pull attacker**: Deploy a shared protocol contract, attract user deposits, then call `replace_class(0)` or any random felt to freeze all deposited funds.
- **Accidental**: A contract developer passes an incorrect class hash (e.g., a Sierra class hash instead of the compiled class hash, or a hash from a different chain) and the OS silently accepts it.

The missing check is explicitly flagged by the development team in a TODO comment, confirming awareness that the validation is absent and that the current code is incomplete.

---

### Recommendation

Before updating `contract_state_changes` in `execute_replace_class`, assert that `class_hash` is present in `contract_class_changes` (i.e., it was declared in the current block) or in the existing class commitment tree. Concretely:

1. Perform a `dict_read` on `contract_class_changes` for `class_hash` and assert the result is non-zero (a declared compiled class hash).
2. Alternatively, add a range-check that `class_hash != UNINITIALIZED_CLASS_HASH` (value `0`) as a minimum guard, and enforce full declaration verification as the TODO indicates.

This mirrors the fix applied in the referenced Compound PR 665: validate the new address/hash against the required interface/registry before committing the state change. [2](#0-1) 

---

### Proof of Concept

1. **Attacker deploys** a contract `VaultAttack` that:
   - Accepts user deposits (stores balances in storage).
   - Exposes an `attack()` external function that calls the `replace_class` syscall with `class_hash = 1` (an arbitrary undeclared felt).

2. **Users deposit funds** into `VaultAttack`. The contract holds, e.g., 1,000,000 STRK.

3. **Attacker calls `attack()`**. The OS processes the transaction:
   - `execute_replace_class` is invoked with `class_hash = 1`.
   - Line 898's TODO check is absent; no validation occurs.
   - `dict_update` writes `StateEntry(class_hash=1, ...)` for `VaultAttack`'s address.
   - The transaction succeeds; the proof is generated and verified on L1.

4. **Any subsequent call** to `VaultAttack` (e.g., `withdraw()`) causes the OS to attempt to execute class hash `1`, which does not exist in the class tree. The OS cannot produce a valid proof for any such transaction.

5. **Result**: All 1,000,000 STRK are permanently frozen. The contract is inoperable with no recovery path. [3](#0-2)

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
