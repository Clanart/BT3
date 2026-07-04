### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS does not validate that the caller-supplied class hash corresponds to a previously declared contract class. An unprivileged contract deployer can exploit this to replace a contract's class with an arbitrary, undeclared hash, rendering the contract permanently inoperable and freezing all funds held within it.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function reads the new class hash directly from the user-controlled syscall request and writes it into `contract_state_changes` without any check that the hash is a declared class:

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
```

The `class_hash` value originates from `request.class_hash`, which is fully attacker-controlled. The OS commits this arbitrary hash to the Patricia state tree without verifying it exists in `contract_class_changes` (current block declarations) or in any prior block's state. Once committed, any future call to the contract will attempt to look up the compiled class facts for this hash; since no such facts exist, execution permanently fails. [1](#0-0) 

---

### Impact Explanation

**Permanent freezing of funds.** Once a contract's class hash is replaced with an undeclared value:

1. The new class hash is committed to the global state root via the Patricia tree.
2. All subsequent invocations of the contract fail at the class-lookup stage — the sequencer cannot supply compiled class facts for a hash that was never declared.
3. Any ERC-20 tokens, ETH, or other assets held in the contract's storage become permanently inaccessible.

There is no recovery path: the contract cannot call `replace_class` again (execution fails before reaching any entry point), and no external party can override the state.

---

### Likelihood Explanation

The attack is reachable by any **contract deployer** — an unprivileged protocol participant. The realistic scenario:

1. Attacker deploys a contract that appears legitimate (e.g., a vault, multisig, or yield aggregator).
2. Other users deposit funds, trusting the contract's published logic.
3. The attacker's contract internally calls the `replace_class` syscall with an arbitrary felt value that has never been declared.
4. The OS accepts the state update without validation.
5. All user funds are permanently frozen.

This is directly analogous to the reference report: just as a zero-value token transfer updated a timestamp to block withdrawals, a zero-cost `replace_class` call with a garbage hash permanently blocks all contract interactions. The attack requires no privileged key, no operator cooperation, and no network-level capability — only the ability to deploy and invoke a contract.

---

### Recommendation

Before writing the new class hash to `contract_state_changes`, verify that it is present in either:

- `contract_class_changes` (declared in the current block), or
- The existing committed state (declared in a prior block, verifiable via a dict read on `contract_class_changes` with a known prior value).

This is already acknowledged as a required fix in the codebase itself:

```cairo
// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.
```

The fix should assert that `class_hash` maps to a non-zero compiled class hash before the `dict_update` is performed. [2](#0-1) 

---

### Proof of Concept

**Attack flow (Cairo pseudocode):**

```cairo
// Attacker's malicious contract
@external
func freeze_funds() {
    // Call replace_class with a completely arbitrary, undeclared hash.
    // The OS accepts this without validation.
    starknet::replace_class_syscall(class_hash=0xdeadbeefdeadbeef);
    // Contract is now permanently bricked. All deposited funds are frozen.
}
```

**OS execution path:**

1. `execute_syscalls` dispatches `REPLACE_CLASS_SELECTOR` → `execute_replace_class` in `syscall_impls.cairo`.
2. `execute_replace_class` reads `request.class_hash = 0xdeadbeefdeadbeef` (attacker-supplied).
3. No validation against `contract_class_changes` or prior state is performed (the TODO confirms this check is absent).
4. `dict_update` writes the garbage hash into `contract_state_changes`.
5. `state_update` commits this to the Patricia tree.
6. All future calls to the contract fail: the sequencer cannot provide compiled class facts for `0xdeadbeefdeadbeef`. [3](#0-2) [4](#0-3)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_syscalls.cairo (L195-203)
```text
    if (selector == REPLACE_CLASS_SELECTOR) {
        execute_replace_class(contract_address=execution_context.execution_info.contract_address);
        %{ OsLoggerExitSyscall %}
        return execute_syscalls(
            block_context=block_context,
            execution_context=execution_context,
            syscall_ptr_end=syscall_ptr_end,
        );
    }
```
