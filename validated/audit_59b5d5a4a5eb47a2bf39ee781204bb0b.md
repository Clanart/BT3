### Title
Missing Validation of New Class Hash in `execute_replace_class` Enables Permanent Freezing of Contract Funds - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS accepts any arbitrary felt value as the replacement class hash without verifying that it corresponds to a declared class. This is a one-way, irreversible state transition — analogous to the "pause without unpause" pattern — because once a contract's class hash is set to a non-existent value and the block is proven and committed, there is no mechanism to restore the original class hash. Any funds held in the affected contract are permanently frozen.

---

### Finding Description

In `execute_replace_class` (`syscall_impls.cairo`, lines 877–916), the OS updates the contract's `StateEntry` with the caller-supplied `class_hash` unconditionally. The code itself contains an explicit acknowledgment of the missing validation:

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

The revert log records the old class hash (`CHANGE_CLASS_ENTRY`) only for intra-transaction revert purposes:

```cairo
assert [revert_log] = RevertLogEntry(selector=CHANGE_CLASS_ENTRY, value=state_entry.class_hash);
``` [2](#0-1) 

Once the transaction is not reverted and the block is proven, the state change is permanent. There is no "restore class" syscall or any other mechanism to undo a committed `replace_class` call. This is structurally identical to the "pause without unpause" pattern: a one-way state transition that can be triggered by any contract but cannot be reversed.

Contrast this with class declaration, which enforces `prev_value=0` to prevent re-declaration:

```cairo
// Note that prev_value=0 enforces that a class may be declared only once.
assert_not_zero(compiled_class_hash);
dict_update{dict_ptr=contract_class_changes}(
    key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
);
``` [3](#0-2) 

No equivalent guard exists in `execute_replace_class` to ensure the new class hash is a declared class.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

If a contract calls `replace_class` with a class hash that has not been declared (e.g., any arbitrary felt), the OS Cairo code accepts the call, the state is updated, and the block is proven. On all subsequent calls to that contract, the OS will attempt to dispatch execution to the non-existent class. The dispatch will fail at the class lookup stage, making the contract permanently uncallable. All ERC-20 tokens, NFTs, or other assets held in the contract's storage are permanently inaccessible with no recovery path.

---

### Likelihood Explanation

**Medium.**

The `replace_class` syscall is available to any Sierra (Cairo 1) contract via the standard syscall interface. A malicious contract can deliberately call `replace_class(0)` or any undeclared hash to self-destruct and freeze its own funds (e.g., as part of a rug pull or griefing attack against users who deposited funds). A buggy contract could trigger this accidentally. No privileged role or operator access is required — any deployed contract can invoke this syscall.

The attacker-controlled entry path is:
1. Deploy a contract (standard `deploy_account` or `deploy` syscall).
2. Call `replace_class` with an undeclared class hash from within that contract.
3. The OS Cairo code (`execute_replace_class`) accepts the call without validation.
4. The transaction is included in a proven block; the state is committed on-chain.
5. The contract is permanently bricked; all funds are frozen.

---

### Recommendation

Before updating `contract_state_changes`, verify that the requested `class_hash` exists in `contract_class_changes` (i.e., has a non-zero compiled class hash entry). This is exactly what the existing TODO comment calls for:

```cairo
// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.
```

The fix should perform a `dict_read` on `contract_class_changes` keyed by `class_hash` and assert the result is non-zero before proceeding with the state update, analogous to how `execute_deploy` validates the class hash at deploy time.

---

### Proof of Concept

1. Attacker deploys Contract A holding user funds (e.g., an ERC-20 vault).
2. Contract A calls the `replace_class` syscall with `class_hash = 0xdeadbeef` (an undeclared hash).
3. `execute_replace_class` in `syscall_impls.cairo` (line 896–913) accepts the call: no validation of `class_hash` against declared classes is performed.
4. The transaction succeeds (is not reverted); the block is proven and submitted to L1.
5. Contract A's `StateEntry.class_hash` is now `0xdeadbeef` in the committed global state.
6. Any subsequent call to Contract A causes the OS to look up class `0xdeadbeef`, which does not exist — execution fails unconditionally.
7. All funds in Contract A's storage are permanently frozen with no recovery mechanism. [4](#0-3)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L815-819)
```text
    // Note that prev_value=0 enforces that a class may be declared only once.
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```
