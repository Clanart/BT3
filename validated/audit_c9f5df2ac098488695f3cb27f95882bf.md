### Title
Missing Class Declaration Validation in `execute_replace_class` Allows Permanent Contract Freezing — (File: `execution/syscall_impls.cairo`)

### Summary
The `execute_replace_class` syscall handler in the StarkNet OS accepts any arbitrary class hash as the replacement class without verifying that the class has been declared on-chain. An unprivileged contract deployer can call `replace_class` with an undeclared class hash, permanently rendering the contract un-executable and freezing any funds it holds.

### Finding Description
In `syscall_impls.cairo`, the `execute_replace_class` function processes the `REPLACE_CLASS` syscall. After reducing gas, it reads the requested `class_hash` from the syscall request and immediately writes it into `contract_state_changes` via `dict_update`, with no check that the class hash corresponds to a previously declared (and thus executable) class:

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
```

The developer-acknowledged TODO at line 898 confirms the missing prerequisite check. The `contract_class_changes` dictionary (which tracks declared classes) is not consulted at all. Compare this to `execute_declare_transaction`, which enforces `prev_value=0` to prevent double-declaration, and `deploy_contract`, which asserts `state_entry.class_hash = UNINITIALIZED_CLASS_HASH` before deploying — both enforce prerequisite state. `execute_replace_class` has no equivalent guard.

This is structurally identical to the reported `refund` vulnerability: a state-mutating function executes and marks state as changed (`coverage.refunded = true` / new class hash written) without verifying the prerequisite state is valid (`refundMap != 0` / class is declared), permanently locking future legitimate operations.

### Impact Explanation
Once a contract's class hash is set to an undeclared value:

1. Any subsequent call to the contract will fail at class resolution — the OS cannot find the compiled class for the hash.
2. The contract cannot call `replace_class` again to self-repair, because it cannot execute at all.
3. There is no protocol-level recovery mechanism.
4. All funds (tokens, ETH, protocol state) held by the contract are permanently inaccessible.

**Impact: Critical — Permanent freezing of funds.**

### Likelihood Explanation
The `REPLACE_CLASS` syscall is a standard, documented StarkNet syscall reachable by any Sierra contract. An attacker needs only to:

1. Deploy a contract (unprivileged action).
2. Have the contract call `replace_class` with a felt value that is not a declared class hash.

No privileged role, leaked key, or external dependency is required. The OS will accept the transaction and commit the invalid class hash to the state trie, producing a valid proof. The attack is deterministic and requires a single transaction.

### Recommendation
Before writing the new class hash to `contract_state_changes`, verify that the class hash exists in `contract_class_changes` (i.e., it was declared in a prior or current block). Concretely, perform a `dict_read` on `contract_class_changes` for `class_hash` and assert the returned compiled class hash is non-zero:

```cairo
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
assert_not_zero(compiled_class_hash);
```

This mirrors the existing guard in `execute_declare_transaction` (`assert_not_zero(compiled_class_hash)`) and closes the missing prerequisite check the TODO comment acknowledges.

### Proof of Concept

1. Attacker deploys Contract A (a vault that accepts user deposits).
2. Users deposit funds into Contract A.
3. Attacker calls a function in Contract A that emits `replace_class(0xdeadbeef)` where `0xdeadbeef` is not a declared class hash.
4. `execute_replace_class` in `syscall_impls.cairo` (lines 878–916) processes the syscall, reads `class_hash = 0xdeadbeef`, skips any declaration check (line 898 TODO), and writes the new `StateEntry` with `class_hash=0xdeadbeef` to `contract_state_changes`.
5. The OS generates a valid proof committing this state.
6. In all subsequent blocks, any call to Contract A fails at class resolution — the OS cannot locate a compiled class for `0xdeadbeef`.
7. User deposits are permanently frozen with no recovery path. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L814-819)
```text
    // Declare the class hash.
    // Note that prev_value=0 enforces that a class may be declared only once.
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deploy_contract.cairo (L51-54)
```text
    local state_entry: StateEntry*;
    %{ GetContractAddressStateEntry %}
    assert state_entry.class_hash = UNINITIALIZED_CLASS_HASH;
    assert state_entry.nonce = 0;
```
