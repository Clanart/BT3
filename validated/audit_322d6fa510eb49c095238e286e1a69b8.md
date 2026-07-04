### Title
Missing Declared Class Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS accepts an arbitrary class hash from the caller and writes it directly into the contract's state entry without verifying that the hash corresponds to a previously declared contract class. A malicious contract can exploit this to permanently replace its class with an undeclared hash, rendering the contract uncallable and freezing all funds it holds.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function processes the `replace_class` syscall by reading `class_hash` directly from the caller-supplied request and immediately writing it into `contract_state_changes` via `dict_update`. There is no check that the new class hash exists in `contract_class_changes` (i.e., has been declared in the current or any prior block).

The code at lines 896–910 reads:

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

The TODO comment at line 898 explicitly acknowledges the missing validation. The `class_hash` field is a raw felt value supplied by the calling contract with no constraint tying it to any entry in `contract_class_changes`. [2](#0-1) 

By contrast, the `execute_declare_transaction` function enforces that a class hash must be the result of a valid Sierra class hash computation before it is written to `contract_class_changes`:

```cairo
let expected_class_hash = finalize_class_hash(
    contract_class_component_hashes=contract_class_component_hashes
);
with_attr error_message("Invalid class hash pre-image.") {
    assert [class_hash_ptr] = expected_class_hash;
}
``` [3](#0-2) 

No equivalent guard exists in `execute_replace_class`. The vulnerability class is directly analogous to the external report: a critical state-changing operation consumes caller-supplied data (class hash) without validating it against the authoritative source (declared classes), just as the reported protocol consumed oracle prices without ensuring they were current.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

After `execute_replace_class` commits an undeclared class hash into a contract's `StateEntry`, every subsequent attempt to call that contract will fail. When the OS (or any caller) tries to execute an entry point on the contract, it reads `state_entry.class_hash` and looks up the corresponding class bytecode. If the hash is undeclared, no valid class bytecode exists; the prover cannot produce a valid execution trace for any call to that contract. The contract becomes permanently uncallable.

Any ERC-20 tokens, ETH, or other assets held in the contract's storage are permanently inaccessible — a direct, irreversible loss of user funds meeting the "Critical — Permanent freezing of funds" threshold. [2](#0-1) 

---

### Likelihood Explanation

**Medium.**

The `replace_class` syscall is callable by any contract during normal execution — no privileged role is required. A malicious contract deployer can:
1. Deploy a contract that accepts user deposits (e.g., a vault or pool).
2. Accumulate user funds.
3. Invoke `replace_class` with an arbitrary undeclared felt value as the class hash.
4. The OS accepts the call without validation and commits the invalid class hash.

The attack requires only that the attacker controls a deployed contract, which is an unprivileged capability available to any user. The only friction is that the attacker must deploy a contract that users trust enough to deposit funds into.

---

### Recommendation

Before writing the new class hash into `contract_state_changes`, verify that it exists in `contract_class_changes` (i.e., it was declared in the current block) or in the pre-existing class tree (i.e., it was declared in a prior block). Concretely, add a lookup into `contract_class_changes` for `class_hash` and assert that the result is non-zero (a declared compiled class hash), analogous to how `execute_declare_transaction` validates class hash pre-images before accepting them. [4](#0-3) 

---

### Proof of Concept

1. **Deploy** a contract `Vault` that accepts ERC-20 deposits from users and stores balances in its storage.
2. **Collect** user deposits; `Vault` now holds significant funds.
3. **Invoke** the `replace_class` syscall from within `Vault`'s execution, supplying `class_hash = 0xdeadbeef` (an arbitrary undeclared felt).
4. The OS calls `execute_replace_class`, reads `request.class_hash = 0xdeadbeef`, and writes it into `contract_state_changes` for `Vault`'s address with no validation.
5. The block is proven and committed. `Vault`'s `StateEntry.class_hash` is now `0xdeadbeef`.
6. Any subsequent `call_contract` or `invoke` targeting `Vault` causes the OS to look up class `0xdeadbeef`. No such class exists in `contract_class_changes` or the class tree.
7. No valid execution trace can be generated for calls to `Vault`. The contract is permanently uncallable.
8. All user funds stored in `Vault` are permanently frozen. [5](#0-4)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L738-743)
```text
        let expected_class_hash = finalize_class_hash(
            contract_class_component_hashes=contract_class_component_hashes
        );
        with_attr error_message("Invalid class hash pre-image.") {
            assert [class_hash_ptr] = expected_class_hash;
        }
```
