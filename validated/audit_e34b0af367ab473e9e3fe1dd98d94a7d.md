### Title
Missing Class Hash Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` function in `syscall_impls.cairo` performs a permanent state update — replacing a contract's class hash — **without validating** that the supplied class hash corresponds to any declared contract class. An acknowledged TODO comment in the code explicitly marks this missing check. This is the direct StarkNet analog of the original report's pattern: a consequential state mutation executes before (or entirely without) the required validity check. Any contract can call `replace_class` with an arbitrary, undeclared felt value, permanently bricking itself and freezing all funds it holds.

---

### Finding Description

In `execute_replace_class` (lines 877–916 of `syscall_impls.cairo`), the OS handles the `replace_class` syscall as follows:

1. Gas is deducted.
2. The new `class_hash` is read directly from the syscall request.
3. A `dict_update` is issued to `contract_state_changes`, permanently overwriting the contract's class hash with the caller-supplied value.
4. A revert-log entry is appended.

**No check is performed to confirm that `class_hash` is a declared class.** The code itself contains an explicit acknowledgment of this gap:

```cairo
// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.
``` [1](#0-0) 

The state mutation at `dict_update` is unconditional: [2](#0-1) 

This mirrors the original report's root cause exactly: the consequential action (state change / transfer) occurs **before and without** the required validity check (pair-variant check / declared-class check).

For comparison, `execute_deploy` in `deploy_contract.cairo` also writes the class hash to state before executing the constructor, but a failed constructor causes `assert is_reverted = 0` to trap the transaction — providing an implicit guard. `execute_replace_class` has no such guard; the syscall succeeds and the invalid class hash is committed regardless. [3](#0-2) 

---

### Impact Explanation

Once a contract's class hash is overwritten with an undeclared value:

- Every subsequent call to that contract will fail at class resolution time, because the OS cannot find a compiled class for the stored hash.
- The contract is **permanently non-functional** — there is no recovery path in the protocol.
- All ERC-20 balances, ERC-721 tokens, or any other assets held in the contract's storage are **permanently frozen**.

This satisfies the **Critical: Permanent freezing of funds** impact category.

---

### Likelihood Explanation

The attack path requires only an unprivileged transaction sender who can deploy a contract:

1. A malicious deployer publishes a contract that accepts user deposits and exposes a privileged `replace_class` call path (or a re-entrancy / access-control bug that an external attacker can exploit).
2. Users deposit funds.
3. The deployer (or an attacker exploiting a contract bug) invokes `replace_class` with an arbitrary felt (e.g., `0xdeadbeef`) that has never been declared.
4. The OS executes `execute_replace_class`, deducts gas, and commits the invalid class hash to `contract_state_changes` — no validation fires.
5. The contract is permanently bricked; all deposited funds are frozen.

The `replace_class` syscall is a standard, documented StarkNet syscall reachable by any contract execution. The missing OS-level guard means the protocol itself provides no safety net, regardless of how well-intentioned the contract author is.

---

### Recommendation

Before issuing the `dict_update` in `execute_replace_class`, verify that `class_hash` exists as a key in `contract_class_changes` (i.e., it has been declared in the current or a prior block). Concretely, perform a `dict_read` on `contract_class_changes` for the supplied `class_hash` and assert the returned compiled class hash is non-zero (non-`UNINITIALIZED`). This is the check the TODO comment already calls for and would close the gap analogous to the `isPair()` variant check recommended in the original report.

---

### Proof of Concept

```
1. Attacker deploys ContractA (holds user ETH/token balances).
   ContractA exposes: fn drain_class(new_hash: felt) { replace_class(new_hash); }

2. Users call ContractA.deposit(...) — funds accumulate in storage.

3. Attacker calls ContractA.drain_class(0xdeadbeef).
   → ContractA issues replace_class syscall with class_hash = 0xdeadbeef.

4. OS dispatches to execute_replace_class(contract_address=ContractA).
   → Gas deducted (success).
   → dict_update: ContractA.class_hash ← 0xdeadbeef  [NO VALIDATION]
   → Revert log entry written.
   → Syscall returns success.

5. Block is proven and finalized with ContractA.class_hash = 0xdeadbeef.

6. Any future call to ContractA → OS looks up compiled class for 0xdeadbeef
   → not found → execution fails permanently.

7. All user funds in ContractA.storage are permanently frozen.
```

The root cause is entirely within the OS at: [4](#0-3)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deploy_contract.cairo (L82-92)
```text
    let (is_reverted, retdata_size, retdata, _is_deprecated) = select_execute_entry_point_func(
        block_context=block_context, execution_context=constructor_execution_context
    );

    // Entries before this point belong to the deployed contract.
    assert [revert_log] = RevertLogEntry(selector=CHANGE_CONTRACT_ENTRY, value=contract_address);
    let revert_log = &revert_log[1];

    // The deprecated deploy syscalls do not support reverts.
    assert is_reverted = 0;
    return (retdata_size=retdata_size, retdata=retdata);
```
