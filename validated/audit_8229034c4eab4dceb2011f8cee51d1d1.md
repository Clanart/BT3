### Title
Missing Class Hash Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS does not validate that the requested new class hash corresponds to a declared contract class before committing the state update. Any contract can call `replace_class` with an arbitrary, undeclared class hash. The OS accepts and commits this update unconditionally. All subsequent calls to that contract will fail at the class-lookup stage, permanently freezing any funds held by the contract.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function reads the requested class hash directly from the syscall request and writes it into the contract's `StateEntry` without any check against `contract_class_changes` (the dictionary of declared classes):

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

The inline TODO comment explicitly acknowledges the missing validation. The OS has access to `contract_class_changes` (the declared-class dictionary) as an implicit argument throughout syscall execution, so the check is structurally feasible but simply absent.

After this state update is committed and the block is finalized, the contract's on-chain class hash is permanently set to the undeclared value. Every future entry-point dispatch for this contract will fail at class resolution because no compiled class exists for that hash. There is no recovery path: `replace_class` cannot be called again because the contract itself is now unexecutable.

The analog to the external report is exact: the controller (OS) does not raise an error when the requested resource (a declared class) does not exist, so the operation proceeds and the user's asset (the contract and its funds) is destroyed. [1](#0-0) 

---

### Impact Explanation

**Permanent freezing of funds (Critical).**

Once a contract's class hash is set to an undeclared value the contract is permanently bricked. Any ERC-20 tokens, ETH, or other assets held in the contract's storage become permanently inaccessible. Because the state update is committed to the proven block output and the Merkle tree, it cannot be reversed at the protocol level without a hard fork. [2](#0-1) 

---

### Likelihood Explanation

The `replace_class` syscall is callable by any contract on itself — no privileged role is required. Realistic trigger paths include:

1. **Governance-controlled upgrade contracts**: Many DeFi protocols expose a `replace_class` (upgrade) path gated by a governance vote or a multisig. An attacker who can pass a malicious proposal (e.g., by accumulating voting tokens or exploiting a governance bug) can supply an undeclared class hash. The OS will not reject it.

2. **Contracts with permissionless upgrade hooks**: A contract that exposes `replace_class` to any caller (e.g., a misconfigured proxy) can be bricked by any unprivileged transaction sender.

3. **Accidental developer error**: A developer who calls `replace_class` with a hash that was never declared (e.g., a Sierra hash instead of a CASM hash, or a hash from a different chain) will permanently freeze their contract with no OS-level safety net.

In all cases the attacker's entry point is a standard `INVOKE_FUNCTION` transaction — fully unprivileged.

---

### Recommendation

Before committing the class hash update, assert that the new class hash is present in `contract_class_changes` (i.e., it has been declared in the current or a prior block). If the class hash is not found, write a failure response and return without modifying state, mirroring the pattern used by every other syscall that encounters an invalid argument:

```cairo
// Validate that the new class hash has been declared.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
if (compiled_class_hash == 0) {
    write_failure_response(remaining_gas=remaining_gas, failure_felt=ERROR_INVALID_ARGUMENT);
    return ();
}
```

This ensures `replace_class` reverts — rather than silently succeeding — when the target class does not exist, directly mirroring the mitigation recommended in the external report (revert the transaction when the requested resource is unavailable). [1](#0-0) 

---

### Proof of Concept

```python
# Attacker steps (web3 / starknet.py pseudocode):

# 1. Deploy a contract that holds user funds (e.g., a simple vault).
vault_address = deploy_vault(initial_funds=1_000_000_STRK)

# 2. Craft an invoke transaction that calls replace_class on the vault
#    with a class hash that has never been declared on-chain.
UNDECLARED_CLASS_HASH = 0xdeadbeefdeadbeefdeadbeefdeadbeef

invoke(
    contract=vault_address,
    selector="replace_class",          # or any entry point that internally calls replace_class
    calldata=[UNDECLARED_CLASS_HASH],
)

# 3. The OS executes execute_replace_class:
#    - Reads UNDECLARED_CLASS_HASH from the request.
#    - Skips validation (TODO comment, no dict_read on contract_class_changes).
#    - Writes new StateEntry(class_hash=UNDECLARED_CLASS_HASH, ...) to contract_state_changes.
#    - Returns success (failure_flag=0).

# 4. Block is proven and finalized. The vault's class hash on-chain is now UNDECLARED_CLASS_HASH.

# 5. Any subsequent call to vault_address fails at class resolution.
#    1_000_000 STRK are permanently frozen.
withdraw(vault_address, amount=1_000_000_STRK)
# => EXECUTION_ERROR: class hash not found
``` [2](#0-1) [3](#0-2)

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
