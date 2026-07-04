### Title
Missing Class Hash Existence Validation in `execute_replace_class` Allows Permanent Contract Freezing - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS does not validate that the supplied `class_hash` corresponds to a previously declared class. Any contract can invoke `replace_class` with an arbitrary, undeclared class hash, causing the contract's class pointer to be permanently set to a non-existent class. All subsequent calls to that contract will fail, permanently freezing any funds it holds.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function reads the requested `class_hash` directly from the syscall request and writes it into the contract state without any existence check:

```cairo
// execute_replace_class (lines 878–916)
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

The developer-acknowledged TODO at line 898 explicitly confirms the missing check. There is no assertion, dictionary lookup, or whitelist verification that `class_hash` exists in `contract_class_changes` (the declared-class registry). The OS unconditionally commits the new class hash to state.

This is the direct analog of the reported "lack of chain whitelisting": just as `addCrosschainRequest` accepted any chain ID without checking it was supported, `execute_replace_class` accepts any class hash without checking it was declared. [1](#0-0) 

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once `dict_update` commits an undeclared `class_hash` to `contract_state_changes`, the contract's on-chain class pointer is permanently set to a non-existent class. Every future call to that contract (including any entry point that would allow fund withdrawal) will fail at class resolution time. There is no recovery path: the OS has no upgrade or rollback mechanism for a committed state root. All ERC-20 tokens, ETH, or other assets held in the contract's storage are irrecoverably locked. [2](#0-1) 

---

### Likelihood Explanation

**Medium.**

The `replace_class` syscall is available to any contract during execution — it requires no privileged role. The realistic trigger scenarios are:

1. **Buggy contract**: A contract with a logic error passes an incorrect class hash (e.g., a hash computed off-chain with a typo, or a hash of an undeclared Sierra class).
2. **Shared vault / multisig**: A contract holding multiple users' funds is controlled by one party who calls `replace_class` with an invalid hash, freezing all depositors' assets.
3. **Malicious contract**: A contract advertised as a yield vault calls `replace_class` with a garbage hash after collecting deposits.

The entry path is fully reachable by an unprivileged transaction sender with no special permissions. [3](#0-2) 

---

### Recommendation

Before committing the new class hash to state, verify that it exists in the declared-class registry (`contract_class_changes` dict). Concretely, perform a `dict_read` on `contract_class_changes` keyed by `class_hash` and assert the returned compiled class hash is non-zero:

```cairo
// After reading class_hash from request:
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
with_attr error_message("replace_class: class hash not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

This mirrors the whitelist pattern used in `execute_declare_transaction`, where `assert_not_zero(compiled_class_hash)` guards the `dict_update` call before a class is registered. [4](#0-3) 

---

### Proof of Concept

1. Attacker deploys a vault contract `V` that accepts ETH deposits from users. Users deposit funds; `V` holds 1000 ETH in storage.
2. Attacker submits an invoke transaction calling `V.__execute__`, which internally calls the `replace_class` syscall with `class_hash = 0xdeadbeef` (an arbitrary felt that has never been declared).
3. The OS executes `execute_replace_class`:
   - Reads `class_hash = 0xdeadbeef` from the syscall request.
   - Skips the (missing) existence check (line 898 TODO).
   - Calls `dict_update` writing `StateEntry { class_hash: 0xdeadbeef, ... }` for contract `V`.
4. The block is proven and the new state root (containing `V → 0xdeadbeef`) is committed to L1.
5. Any subsequent call to `V` (withdraw, transfer, etc.) fails: the OS cannot resolve class `0xdeadbeef` to any compiled class, so execution aborts.
6. The 1000 ETH in `V`'s storage is permanently frozen with no recovery path. [5](#0-4)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L878-915)
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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L816-819)
```text
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```
