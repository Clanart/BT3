### Title
Missing Validation of `replace_class` Target Hash Allows Permanent Contract Freezing — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` function in the StarkNet OS accepts a user-controlled `class_hash` from the syscall request and writes it directly into the contract's `StateEntry` without verifying that the target class hash has been declared. A contract can therefore replace its own class with an arbitrary, undeclared felt value, permanently rendering itself unexecutable and freezing any funds it holds.

---

### Finding Description

In `execute_replace_class`, the OS reads `class_hash` directly from the caller-supplied `ReplaceClassRequest` and immediately writes it into `contract_state_changes` via `dict_update`:

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

The TODO comment at line 898 explicitly acknowledges the missing check. There is no assertion that `class_hash` exists in `contract_class_changes` (the declared-class dictionary) before the state update is committed. The analogous protection that *does* exist for `declare` transactions — `assert_not_zero(compiled_class_hash)` and `dict_update{dict_ptr=contract_class_changes}(key=[class_hash_ptr], prev_value=0, ...)` — is entirely absent here.

The vulnerability class is identical to the reference report: **user-controlled input is used directly as a state key/value without validation**, corrupting a critical data structure. In the reference report the corrupted structure was a JavaScript prototype chain; here it is the contract's class-hash slot in the global state trie. [1](#0-0) 

---

### Impact Explanation

Once `replace_class` commits an undeclared class hash into the state trie, every subsequent attempt to execute the contract will fail at class-lookup time: the OS will find no compiled class corresponding to that hash and cannot dispatch any entry point. The contract becomes permanently inaccessible. All ERC-20 tokens, NFTs, or other assets held at that contract address are irreversibly frozen.

**Impact category: Critical — Permanent freezing of funds.**

---

### Likelihood Explanation

The `replace_class` syscall is reachable by any contract that is currently executing. The attacker role is **contract deployer / class declarer** — an unprivileged role. A malicious actor can:

1. Deploy a contract whose code unconditionally calls `replace_class` with an arbitrary felt (e.g., `1`) on the first invocation.
2. Convince a victim to send funds to that contract (e.g., by presenting it as a wallet or vault).
3. Trigger the first invocation; the OS writes the invalid class hash into the state trie with no revert.

Alternatively, a buggy legitimate contract that exposes an unguarded `replace_class` call path is equally exploitable by any caller. The syscall is part of the standard ABI and requires no privileged role.

---

### Recommendation

Before writing the new `StateEntry`, assert that `class_hash` is present in `contract_class_changes` (or in a pre-squashed declared-class set provided by the prover). Concretely, add a `dict_read` on `contract_class_changes` keyed by `class_hash` and assert the returned compiled class hash is non-zero:

```cairo
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
assert_not_zero(compiled_class_hash);
```

This mirrors the invariant already enforced in `execute_declare_transaction` (`assert_not_zero(compiled_class_hash)` before `dict_update`) and closes the gap noted in the TODO comment. [2](#0-1) 

---

### Proof of Concept

1. **Declare and deploy** a malicious contract whose `__execute__` entry point issues:
   ```
   replace_class(class_hash=0xdeadbeef)   // arbitrary undeclared felt
   ```
2. **Deposit funds** into the deployed contract address (e.g., transfer ERC-20 tokens to it).
3. **Invoke** the contract once (any `__execute__` call).
4. The OS calls `execute_replace_class`; `request.class_hash = 0xdeadbeef` passes through with no validation and is written into `contract_state_changes`.
5. The block is proven and finalized. The state trie now records `class_hash = 0xdeadbeef` for that address.
6. All subsequent invocations of the contract fail at class dispatch — no compiled class exists for `0xdeadbeef`. The deposited funds are permanently frozen.

The root cause is the absence of a declared-class membership check in `execute_replace_class`, directly analogous to the reference report's unchecked use of user-supplied keys to index into a protected internal data structure. [3](#0-2)

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
