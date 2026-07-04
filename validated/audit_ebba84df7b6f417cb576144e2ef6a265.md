### Title
Missing Class Existence Validation in `execute_replace_class` Allows Permanent Freezal of Contract Funds — (`File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS does not verify that the new class hash supplied by the caller corresponds to a previously declared contract class. Any contract can call `replace_class` with an arbitrary, undeclared class hash. The OS commits this invalid hash to state, permanently rendering the contract un-executable and freezing all funds held within it.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function processes the `replace_class` syscall. After deducting gas, it reads the current `StateEntry` for the calling contract and unconditionally writes the caller-supplied `class_hash` into the state — with no check that the hash corresponds to a declared class:

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

The developer-acknowledged TODO at line 898 confirms the missing prerequisite check. The `contract_class_changes` dictionary (which tracks declared classes) is not consulted at all during this syscall. The OS accepts and commits any arbitrary felt value as the new class hash.

This is directly analogous to the external report's root cause: a function performs a state-changing operation that requires a prior prerequisite (in the original report, an ERC-20 `approve`; here, a prior `declare` of the target class) without verifying that prerequisite exists.

---

### Impact Explanation

Once `replace_class` is executed with an undeclared class hash, the contract's class hash in the committed state points to a non-existent class. Every subsequent call to that contract will fail at class resolution time, because the OS/prover cannot find the bytecode for the stored hash. There is no recovery path at the protocol level — the state is finalized. All funds (tokens, NFTs, or other assets) held by the contract are permanently frozen.

This matches the allowed impact: **Critical — Permanent freezing of funds**.

---

### Likelihood Explanation

The `replace_class` syscall is callable by any contract from within its own execution context. No privileged role or special permission is required. A malicious contract author, or a contract with a logic bug, can trigger this path with a single transaction. The attacker-controlled entry path is:

1. Attacker deploys a contract (or controls an existing one holding user funds).
2. Users deposit assets into the contract.
3. Attacker submits an invoke transaction that calls `replace_class(undeclared_hash)` from within the contract.
4. The OS processes the syscall, commits the invalid class hash to state, and finalizes the block.
5. All future calls to the contract fail; funds are permanently frozen.

No trusted role, leaked key, or network-level attack is required.

---

### Recommendation

Before committing the new class hash to `contract_state_changes`, the OS must verify that the supplied `class_hash` exists as a key in `contract_class_changes` (i.e., it was declared in the current or a prior block). The check should assert:

```cairo
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
assert_not_zero(compiled_class_hash);
```

This mirrors the validation already performed implicitly during contract deployment and class execution, and closes the gap acknowledged by the existing TODO comment.

---

### Proof of Concept

1. Deploy contract `A` holding user funds.
2. From within `A`, issue the `replace_class` syscall with `class_hash = 0xdeadbeef` (any value not present in `contract_class_changes`).
3. The OS executes `execute_replace_class`:
   - Gas is deducted successfully.
   - `state_entry` is fetched for contract `A`.
   - `new_state_entry` is written with `class_hash = 0xdeadbeef`.
   - `dict_update` commits this to `contract_state_changes`.
   - No lookup into `contract_class_changes` occurs.
4. The block is finalized. Contract `A`'s on-chain class hash is now `0xdeadbeef`.
5. Any subsequent transaction targeting contract `A` fails at class resolution — the prover cannot find bytecode for `0xdeadbeef`.
6. All funds in contract `A` are permanently inaccessible.

**Relevant code location:** [1](#0-0)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L896-914)
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
