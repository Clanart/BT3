### Title
Unvalidated Class Hash in `replace_class` Syscall Allows Permanent Freezal of Contract Funds - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` function in the StarkNet OS accepts an arbitrary, attacker-controlled class hash without verifying that the hash corresponds to a declared contract class. A contract can replace its own class with a non-existent class hash, permanently rendering itself non-callable and freezing any funds it holds. The missing check is explicitly acknowledged by a TODO comment in the production code.

---

### Finding Description

In `execute_replace_class`, the OS reads the requested `class_hash` directly from the syscall request and writes it into `contract_state_changes` without any validation that the hash corresponds to a class that has been declared on-chain:

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
``` [1](#0-0) 

The TODO comment on line 898 explicitly confirms the missing check is known and unimplemented. The OS state transition commits the arbitrary class hash to the contract's state entry unconditionally.

This is the direct analog to the NodeRegistry URL issue: just as the IN3 registry accepted arbitrary URLs without validation and nodes blindly used them, the StarkNet OS accepts arbitrary class hashes in `replace_class` without checking they correspond to declared classes, and the state transition commits them unconditionally.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once a contract's class hash is replaced with a hash that has no corresponding declared class:

1. Any future call to that contract (including `__execute__`, `transfer`, or any withdrawal function) will fail at class resolution time, because the OS cannot find the compiled class for the stored hash.
2. All funds (ERC-20 balances, NFTs, or native ETH/STRK held in the contract's storage) become permanently inaccessible — there is no recovery path since the class hash is committed to the proven state.
3. The state transition is included in the OS output and committed to L1, making it irreversible.

---

### Likelihood Explanation

**High.** The entry path requires only that an attacker deploy or control a contract and invoke the `replace_class` syscall with a fabricated felt value as the class hash. No privileged role, leaked key, or external dependency is needed. The attack is a single transaction. The TODO comment confirms the check is absent in the current production code, not merely a theoretical gap.

---

### Recommendation

Before committing the `replace_class` state update, the OS must verify that `request.class_hash` exists in `contract_class_changes` (i.e., it was declared in the current block) or in the pre-existing on-chain class registry. Concretely, a lookup into `contract_class_changes` for `key=class_hash` should assert a non-zero `compiled_class_hash` value, analogous to the check already performed in `execute_declare_transaction`:

```cairo
// In execute_replace_class, before dict_update:
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
assert_not_zero(compiled_class_hash);
``` [2](#0-1) 

---

### Proof of Concept

1. Attacker deploys a contract `C` that holds user funds (e.g., an escrow or vault).
2. Attacker calls a function on `C` that internally invokes the `replace_class` syscall with `class_hash = 0xdeadbeef` (any felt not corresponding to a declared class).
3. The OS executes `execute_replace_class`:
   - Reads `class_hash = 0xdeadbeef` from the request.
   - Skips the missing declared-class check (TODO line 898).
   - Writes `StateEntry(class_hash=0xdeadbeef, ...)` into `contract_state_changes`.
4. The block is proven and the state is committed to L1.
5. Any subsequent call to `C` — including withdrawal of funds — fails because no compiled class exists for `0xdeadbeef`.
6. All funds in `C` are permanently frozen with no recovery mechanism. [3](#0-2)

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
