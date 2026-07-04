### Title
`execute_replace_class` Accepts Undeclared Class Hash Without Validation — (`File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS does not verify that the new class hash supplied by the caller corresponds to a previously declared contract class. The OS unconditionally writes the caller-supplied hash into the contract state, accepting an invalid state transition. Any contract that replaces its class with an undeclared hash becomes permanently non-executable, permanently freezing all funds held in its storage.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function processes the `REPLACE_CLASS` syscall:

```cairo
func execute_replace_class{...}(contract_address: felt) {
    ...
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
    ...
}
```

The developer-acknowledged TODO at line 898 confirms the missing check: **there is no assertion that `class_hash` exists in `contract_class_changes`** (i.e., was declared in the current or a prior block). The OS accepts any felt value as the new class hash and commits it to the global state tree.

By contrast, `execute_declare_transaction` enforces `assert_not_zero(compiled_class_hash)` and `dict_update{dict_ptr=contract_class_changes}(key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash)` — meaning a class is only valid if it has a non-zero compiled class hash entry in the class trie. `execute_replace_class` performs no equivalent lookup.

This is the direct analog of the reported Solidity bug: an "upgrade" (class replacement) can be performed even when no new implementation (declared class) exists.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

When a contract's class hash is set to an undeclared value:
- The contract's storage (including ERC20 balances, vault balances, etc.) remains in the state trie.
- Every subsequent call to that contract will fail at class resolution time because no compiled class exists for the hash.
- There is no recovery path: the contract cannot execute any function, including any administrative recovery function, because execution itself requires a valid class.
- All funds held in the contract's storage are permanently inaccessible.

The OS-level missing check means the proof system produces and accepts valid proofs for blocks containing this invalid state transition, making the freeze irreversible at the protocol level.

---

### Likelihood Explanation

The `replace_class` syscall is callable by any contract from within its own execution context. An unprivileged actor can:

1. Deploy a contract (no special permission required).
2. Have users deposit funds into it (e.g., as a token contract or vault).
3. Call any function on the contract that internally invokes `replace_class` with an arbitrary felt (e.g., `1`, a random value, or a hash of an undeclared class).
4. The OS accepts the block; the contract is permanently bricked.

No privileged role, leaked key, or external dependency is required. The entry path is a standard user-initiated transaction.

---

### Recommendation

Before writing the new class hash to `contract_state_changes`, assert that the class hash exists in `contract_class_changes` (i.e., has a non-zero compiled class hash entry). Concretely, perform a `dict_read` on `contract_class_changes` for `class_hash` and assert the result is non-zero:

```cairo
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
assert_not_zero(compiled_class_hash);
```

This mirrors the invariant already enforced by `execute_declare_transaction` and closes the gap between class declaration and class replacement.

---

### Proof of Concept

1. Declare a contract class `A` (valid, compiled class hash exists).
2. Deploy a contract `C` using class `A`. `C` holds a function `brick()` that calls `replace_class(0xdeadbeef)` where `0xdeadbeef` is never declared.
3. Users deposit tokens into `C`.
4. Attacker (or malicious deployer) sends a transaction invoking `C.brick()`.
5. The OS executes `execute_replace_class` with `class_hash = 0xdeadbeef`. No validation occurs.
6. `contract_state_changes` is updated: `C`'s class hash is now `0xdeadbeef`.
7. The block is proven and accepted. `C`'s storage (with user funds) is committed to the state trie.
8. All subsequent calls to `C` fail — no compiled class exists for `0xdeadbeef`. Funds are permanently frozen.

**Root cause line:** `syscall_impls.cairo:898` — the acknowledged TODO where the missing class existence check should be. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L878-916)
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
