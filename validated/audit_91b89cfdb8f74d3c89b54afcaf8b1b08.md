### Title
Undeclared Class Hash Accepted in `replace_class` Syscall Enables Permanent Fund Freezing - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` function in the StarkNet OS does not verify that the new `class_hash` supplied by a contract corresponds to a previously declared class. Any contract can call `replace_class` with an arbitrary, undeclared class hash, permanently corrupting its own on-chain state. Because the corrupted state is committed to the global state root, any funds held by that contract become permanently inaccessible — a direct analog to the external report's pattern of a critical type-controlling field being mutable after commitments are made.

---

### Finding Description

In `syscall_impls.cairo`, `execute_replace_class` accepts the caller-supplied `class_hash` and writes it unconditionally into `contract_state_changes`:

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

The inline `TODO` comment at line 898 explicitly acknowledges the missing check. The same omission exists in the deprecated path: [2](#0-1) 

By contrast, `execute_declare_transaction` enforces `prev_value=0` to guarantee a class is declared at most once and verifies the Sierra class hash pre-image before writing to `contract_class_changes`: [3](#0-2) 

There is no cross-check between `contract_state_changes` (which records the new `class_hash` for a contract) and `contract_class_changes` (which records only legitimately declared classes). `replace_class` writes to the former without consulting the latter.

The corrupted `class_hash` is then committed permanently through `compute_contract_state_commitment` → `hash_contract_state_changes` → Patricia tree update → global state root: [4](#0-3) 

Once the block is proven and the state root is accepted on L1, the undeclared `class_hash` is canonical. No future transaction can execute against the contract because the OS cannot resolve the class bytecode for an undeclared hash.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Any ERC-20 balance, ETH equivalent, or protocol reserve held in a contract whose `class_hash` is replaced with an undeclared value becomes permanently inaccessible. The state root is verified by the L1 verifier; there is no rollback mechanism once a block is finalized. The contract's storage (including token balances) remains in the state tree but is unreachable because no valid entry point can be dispatched.

---

### Likelihood Explanation

The `replace_class` syscall is reachable by any unprivileged transaction sender who deploys or controls a contract. The attacker does not need any privileged role. The attack path is:

1. Attacker deploys a contract that accepts user deposits (e.g., a vault or liquidity pool).
2. Users deposit funds into the contract.
3. Attacker sends a transaction that calls `replace_class` with an arbitrary felt value that is not a declared class hash.
4. The OS writes the undeclared hash into `contract_state_changes` with no validation.
5. The block is proven; the state root is updated on L1.
6. All user funds in the contract are permanently frozen.

The syscall is dispatched from `execute_syscalls` with no pre-flight guard: [5](#0-4) 

---

### Recommendation

Inside `execute_replace_class`, assert that the requested `class_hash` exists in `contract_class_changes` (i.e., it was declared in the current block) **or** in the pre-existing class commitment tree (i.e., it was declared in a prior block). This is exactly what the existing `TODO` comment calls for. The check should be a Cairo-level constraint (`assert`), not a hint, so that it is part of the proof and cannot be bypassed by a malicious prover.

---

### Proof of Concept

```
Block N:
  Tx 1 (DEPLOY_ACCOUNT): Attacker deploys VaultContract at address A.
  Tx 2 (INVOKE):         Users deposit 1000 STRK into VaultContract.
  Tx 3 (INVOKE):         Attacker calls VaultContract.__execute__() which
                         internally calls replace_class(0xdeadbeef).
                         OS executes execute_replace_class(contract_address=A):
                           class_hash = 0xdeadbeef  // not in contract_class_changes
                           // TODO check is absent — no assertion fires
                           dict_update(contract_state_changes, A,
                               prev=StateEntry{class_hash=real_hash, ...},
                               new=StateEntry{class_hash=0xdeadbeef, ...})
  Block N committed to L1. State root now encodes A.class_hash = 0xdeadbeef.

Block N+1:
  Any Tx targeting A: OS reads class_hash=0xdeadbeef from state,
                      cannot find compiled class, execution fails.
  1000 STRK permanently frozen.
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deprecated_execute_syscalls.cairo (L307-329)
```text
func execute_replace_class{contract_state_changes: DictAccess*, revert_log: RevertLogEntry*}(
    contract_address, syscall_ptr: ReplaceClass*
) {
    alloc_locals;
    let class_hash = syscall_ptr.class_hash;

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/commitment.cairo (L51-70)
```text
func get_contract_state_hash{hash_ptr: HashBuiltin*}(
    class_hash: felt, storage_root: felt, nonce: felt
) -> (hash: felt) {
    const CONTRACT_STATE_HASH_VERSION = 0;
    if (class_hash == UNINITIALIZED_CLASS_HASH) {
        if (storage_root == 0) {
            if (nonce == 0) {
                return (hash=0);
            }
        }
    }

    // Set res = H(H(class_hash, storage_root), nonce).
    let (hash_value) = hash2(class_hash, storage_root);
    let (hash_value) = hash2(hash_value, nonce);

    // Return H(hash_value, CONTRACT_STATE_HASH_VERSION). CONTRACT_STATE_HASH_VERSION must be in the
    // outermost hash to guarantee unique "decoding".
    let (hash) = hash2(hash_value, CONTRACT_STATE_HASH_VERSION);
    return (hash=hash);
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
