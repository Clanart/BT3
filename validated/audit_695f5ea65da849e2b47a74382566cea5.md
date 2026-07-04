### Title
Missing Declared Class Hash Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary
The `execute_replace_class` syscall handler in the StarkNet OS does not verify that the new class hash provided by a contract corresponds to a previously declared contract class. A contract can call `replace_class` with an arbitrary, undeclared class hash. The OS will commit this invalid class hash to state without rejection. Any subsequent call to that contract will fail at the OS level because the class hash cannot be resolved to a compiled class, permanently freezing all funds held in the contract.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function processes the `replace_class` syscall by updating the contract's class hash in `contract_state_changes` without verifying that the new class hash is actually declared in `contract_class_changes`. A developer TODO comment at line 898 explicitly acknowledges this missing check:

```cairo
// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.
```

The full function (lines 877–916) performs gas deduction and then unconditionally writes the attacker-supplied `class_hash` into the contract's `StateEntry`:

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

No lookup into `contract_class_changes` is performed to confirm the class hash is declared. The `dict_update` commits the arbitrary hash to the global state trie unconditionally.

The downstream consequence is visible in `execute_entry_point.cairo`. When any future call targets this contract, the OS resolves its class hash via:

```cairo
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=execution_context.class_hash
);
...
let (compiled_class_fact: CompiledClassFact*) = find_element(
    array_ptr=compiled_class_facts_bundle.compiled_class_facts,
    elm_size=CompiledClassFact.SIZE,
    n_elms=compiled_class_facts_bundle.n_compiled_class_facts,
    key=compiled_class_hash,
);
``` [2](#0-1) 

If the class hash is undeclared, `dict_read` returns `0` (`UNINITIALIZED_CLASS_HASH`), and `find_element` will fail to locate a matching `CompiledClassFact`, causing the OS program to abort. The contract becomes permanently unreachable at the protocol level.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once a contract's class hash is replaced with an undeclared value and committed to the state trie, the contract is permanently inaccessible. No entry point can be invoked because the OS cannot resolve the class to bytecode. Any ERC-20 tokens, ETH, or other assets held in the contract's storage are irrecoverably frozen. The state commitment (`compute_contract_state_commitment`) will faithfully record the invalid class hash in the Patricia Merkle Tree, making the freeze permanent and verifiable on-chain. [3](#0-2) 

---

### Likelihood Explanation

The `replace_class` syscall is accessible to any deployed contract — no privileged role is required. An attacker deploys a contract (or exploits an existing one), calls `replace_class` with an arbitrary felt value that has never been declared, and the OS accepts it. The attack requires only a single transaction and standard gas. The TODO comment confirms the check was known to be missing and was scheduled for a past deadline (1/1/2026) that has already passed (today is 2026-07-03), indicating the gap is still present in production code.

---

### Recommendation

In `execute_replace_class`, immediately after the gas deduction succeeds, add a lookup into `contract_class_changes` to confirm the requested `class_hash` maps to a non-zero compiled class hash before writing the new `StateEntry`:

```cairo
// Verify the class hash is declared.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
with_attr error_message("Class hash is not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

This mirrors the validation already performed implicitly in `execute_entry_point` and makes the OS consistent with the expected protocol invariant. [4](#0-3) 

---

### Proof of Concept

1. **Deploy** a contract `VictimVault` holding user funds (e.g., via ERC-20 storage).
2. **Craft** a transaction that calls `replace_class` from within `VictimVault`, supplying `class_hash = 0xdeadbeef` — a felt value that has never been declared via a `declare` transaction.
3. **Observe** that `execute_replace_class` in the OS deducts gas and writes `StateEntry(class_hash=0xdeadbeef, ...)` into `contract_state_changes` with no validation.
4. **Commit** the block. The state trie now records `VictimVault` as having class hash `0xdeadbeef`.
5. **Attempt** any subsequent call to `VictimVault`. The OS executes `dict_read(contract_class_changes, key=0xdeadbeef)` → returns `0`. `find_element` fails to locate a compiled class. The call is unresolvable at the OS level.
6. **Result**: All funds in `VictimVault` are permanently frozen. No withdrawal, transfer, or administrative function can ever be executed.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L154-166)
```text
    let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
        key=execution_context.class_hash
    );

    // The key must be at offset 0.
    static_assert CompiledClassFact.hash == 0;
    let compiled_class_facts_bundle = block_context.os_global_context.compiled_class_facts_bundle;
    let (compiled_class_fact: CompiledClassFact*) = find_element(
        array_ptr=compiled_class_facts_bundle.compiled_class_facts,
        elm_size=CompiledClassFact.SIZE,
        n_elms=compiled_class_facts_bundle.n_compiled_class_facts,
        key=compiled_class_hash,
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/commitment.cairo (L51-71)
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
}
```
