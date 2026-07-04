### Title
Missing Class Hash Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

### Summary
The `execute_replace_class` syscall implementation in the StarkNet OS does not validate that the new class hash corresponds to a declared contract class before updating the contract's state. This is directly analogous to the external report's vulnerability class: a state-modifying operation is performed without checking whether the target is in a valid/eligible state, allowing an attacker to corrupt the protocol's state and permanently freeze funds.

### Finding Description
In `syscall_impls.cairo`, the `execute_replace_class` function processes the `replace_class` syscall as follows:

1. It reads the new `class_hash` from the attacker-controlled request.
2. It fetches the current `StateEntry` for the contract.
3. It writes a new `StateEntry` with the attacker-supplied `class_hash` — **with no validation that this hash corresponds to a declared class**.
4. It logs the old class hash in the revert log.

The code itself acknowledges this gap with an explicit TODO:

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

The analogy to the external report is precise:

| External Report | StarkNet OS |
|---|---|
| `cancelOrderFarFromOracle` checks `nextId == TAIL` without verifying the order is active | `execute_replace_class` accepts any felt as a class hash without verifying it is declared |
| Cancelled orders satisfy the structural check, allowing re-cancellation | Undeclared hashes satisfy no check at all, allowing invalid replacement |
| Corrupts the linked list; new orders overwrite active orders | Corrupts the contract's `StateEntry`; the contract becomes permanently unexecutable |
| Users lose funds | Users' funds locked in the contract are permanently frozen |

The `execute_replace_class` function is reachable by any contract via the `REPLACE_CLASS_SELECTOR` syscall dispatched in `execute_syscalls`: [2](#0-1) 

The OS is the final proof-generating layer. Because it does not enforce the invariant that a contract's class hash must be declared, a valid STARK proof can be generated for a state transition that sets a contract's class hash to an arbitrary (undeclared) value. The L1 verifier would accept this proof, making the corruption permanent and on-chain.

### Impact Explanation
Once a contract's class hash is set to an undeclared value (e.g., `0` or any random felt):

- Every future call to that contract will fail at class resolution time, because the OS cannot find a compiled class for the invalid hash.
- The contract's storage — including all user funds — becomes permanently inaccessible.
- There is no recovery path: the contract cannot execute any function (including withdrawal), and the class hash cannot be corrected because `replace_class` itself requires the contract to execute.

This constitutes **Critical — Permanent freezing of funds**.

### Likelihood Explanation
The attack path is reachable by any unprivileged contract deployer:

1. Attacker deploys a contract that accepts user deposits and contains a function that calls `replace_class(class_hash=0)` (or any undeclared hash).
2. Users deposit funds into the contract.
3. Attacker triggers the `replace_class` call.
4. The OS processes the syscall, writes the invalid class hash to the `StateEntry`, and generates a valid proof — because no validation is performed.
5. The L1 verifier accepts the proof.
6. The contract is permanently broken; all deposited funds are frozen.

No privileged role, leaked key, or malicious sequencer is required. The sequencer includes the transaction normally; the OS's missing validation is the sole vulnerable step.

### Recommendation
Before updating the `StateEntry`, verify that `class_hash` exists in `contract_class_changes` (i.e., it was declared in the current or a prior block). Concretely, perform a `dict_read` on `contract_class_changes` for the given `class_hash` and assert the returned compiled class hash is non-zero. This mirrors how `execute_declare_transaction` enforces `prev_value=0` to prevent double-declaration: [3](#0-2) 

### Proof of Concept
```cairo
// Malicious contract (pseudocode)
@external
func freeze_contract() {
    // Call replace_class with an undeclared hash (e.g., 0 or any random felt).
    replace_class(class_hash=0);
}
```

1. Attacker deploys the above contract and advertises it as a yield vault.
2. Users call `deposit()` and lock funds in the contract's storage.
3. Attacker calls `freeze_contract()`.
4. The OS executes `execute_replace_class`, writes `class_hash=0` to the `StateEntry` without validation, and produces a valid proof.
5. After the block is proven and accepted on L1, the contract's class hash is `0` on-chain.
6. All subsequent calls to the contract fail at class resolution; deposited funds are permanently frozen.

### Citations

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L815-819)
```text
    // Note that prev_value=0 enforces that a class may be declared only once.
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```
