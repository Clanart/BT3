### Title
Missing Declared Class Validation in `execute_replace_class` Enables Permanent Freezing of Contract Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS accepts any arbitrary felt value as a new class hash without verifying that the hash corresponds to a previously declared class. This allows any contract to replace its own class with an undeclared hash, permanently rendering the contract uncallable and freezing all funds held in its storage. The missing check is explicitly acknowledged by a TODO comment in the production code.

---

### Finding Description

The `execute_replace_class` function processes the `replace_class` syscall by reading the caller-supplied class hash from the syscall request and directly updating the contract's `StateEntry` in `contract_state_changes`. It performs no validation that the new class hash exists in the declared class set (`contract_class_changes`).

The production code at line 898 contains an explicit acknowledgment of this missing check:

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

The function reads `class_hash` directly from the request and writes it into the state with no cross-check against `contract_class_changes`: [2](#0-1) 

By contrast, `execute_declare_transaction` correctly enforces that a class can only be registered once by using `prev_value=0` in the dict update, ensuring the class hash is new and the compiled class hash is non-zero: [3](#0-2) 

The `StateEntry` struct stores `class_hash` as a plain felt. When the OS later executes a transaction targeting the contract, it reads `state_entry.class_hash` and uses it to look up the compiled class. If the hash is undeclared, the class cannot be found and execution fails permanently. [4](#0-3) 

The class lookup path during invocation reads the class hash from state and passes it directly to the entry point executor: [5](#0-4) 

**Analogy to H-03:** H-03 validated code identity (codehash) but not initialized state values (`unlocked`, `HONEY_QUEEN`, `referral`), allowing a bypass of security invariants. Here, `execute_replace_class` validates gas cost but not class identity (whether the new class hash is declared), allowing a contract to transition to an invalid state that permanently breaks all future execution.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once a contract's class hash is set to an undeclared value:

1. Every subsequent transaction targeting the contract fails at class lookup — the contract is permanently uncallable.
2. All ERC-20 tokens, ETH, or other assets stored in the contract's storage become permanently inaccessible with no recovery path.
3. The state commitment still records the contract's storage (funds remain in the Merkle tree), but no valid execution path can reach them.

This satisfies the "permanent freezing of funds" critical impact category.

---

### Likelihood Explanation

**Medium.**

The attack path is directly reachable by any unprivileged contract deployer:

- No privileged role, leaked key, or operator cooperation is required.
- Any contract can issue the `replace_class` syscall from within its own execution context.
- A malicious deployer can attract user deposits (e.g., by presenting a legitimate-looking DeFi interface), then trigger `replace_class` with an arbitrary undeclared hash to freeze all deposited funds.
- The TODO comment confirms the check is known to be missing and has not been implemented.

---

### Recommendation

Before updating the contract's class hash in `execute_replace_class`, verify that the new class hash exists in the `contract_class_changes` dictionary (i.e., it was declared in the current block) **or** in the pre-existing class commitment tree (i.e., it was declared in a prior block). Concretely:

1. Perform a `dict_read` on `contract_class_changes` for the requested `class_hash`.
2. If the result is `UNINITIALIZED_CLASS_HASH` (0), additionally verify against the class commitment tree root that the class was declared in a previous block.
3. Revert the syscall with an appropriate error if neither check passes.

This mirrors the enforcement already present in `execute_declare_transaction`, which uses `prev_value=0` to guarantee class uniqueness.

---

### Proof of Concept

**Step-by-step attack:**

1. Attacker deploys `MaliciousVault` — a contract that accepts ERC-20 deposits from users and exposes a `drain()` function callable only by the deployer.
2. Users deposit tokens into `MaliciousVault`, trusting its declared class hash.
3. Attacker calls `drain()`, which internally issues the `replace_class` syscall with `class_hash = 0xDEAD` (an arbitrary undeclared felt).
4. The OS executes `execute_replace_class`:
   - Gas is deducted.
   - `class_hash = 0xDEAD` is written into the contract's `StateEntry` with no validation.
   - The revert log records the old class hash.
5. The block is proven and finalized. The contract's state entry now has `class_hash = 0xDEAD`.
6. Any future transaction targeting `MaliciousVault` fails at class lookup — the class `0xDEAD` does not exist in the class tree.
7. All user deposits are permanently frozen in the contract's storage with no recovery mechanism.

**Relevant code path:**

```
execute_syscalls (execute_syscalls.cairo:195)
  → execute_replace_class (syscall_impls.cairo:878)
      class_hash = request.class_hash          // attacker-controlled, no validation
      new_state_entry = StateEntry(class_hash=class_hash, ...)
      dict_update(contract_state_changes, ...)  // undeclared hash written to state
``` [6](#0-5)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L463-487)
```text
    let (state_entry: StateEntry*) = dict_read{dict_ptr=contract_state_changes}(
        key=contract_address
    );
    let (tx_info_ptr: TxInfo*) = alloc();
    let (deprecated_tx_info_ptr: DeprecatedTxInfo*) = alloc();
    local calldata_size;
    local calldata: felt*;
    %{ TxCalldata %}
    local tx_execution_context: ExecutionContext* = new ExecutionContext(
        entry_point_type=entry_point_type,
        class_hash=state_entry.class_hash,
        calldata_size=calldata_size,
        calldata=calldata,
        execution_info=new ExecutionInfo(
            block_info=block_context.block_info_for_execute,
            tx_info=tx_info_ptr,
            caller_address=ORIGIN_ADDRESS,
            contract_address=contract_address,
            selector=entry_point_selector,
        ),
        deprecated_tx_info=deprecated_tx_info_ptr,
    );
    assert_nn_le(tx_execution_context.calldata_size, SIERRA_ARRAY_LEN_BOUND - 1);

    return (tx_execution_context=tx_execution_context);
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L815-819)
```text
    // Note that prev_value=0 enforces that a class may be declared only once.
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/commitment.cairo (L25-29)
```text
struct StateEntry {
    class_hash: felt,
    storage_ptr: DictAccess*,
    nonce: felt,
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
