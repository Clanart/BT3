### Title
Missing Declared Class Validation in `execute_replace_class` Allows Permanent Contract Freezing - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` function in `syscall_impls.cairo` (and its deprecated counterpart in `deprecated_execute_syscalls.cairo`) accepts any arbitrary class hash without verifying that the hash corresponds to a declared contract class. Once a contract's class hash is replaced with an undeclared hash, the contract can never execute again — permanently freezing all funds it holds. This is an irreversible state transition with no recovery path.

---

### Finding Description

In `execute_replace_class` (`syscall_impls.cairo`, lines 877–916), the OS processes the `replace_class` syscall by directly writing the caller-supplied `class_hash` into the contract's `StateEntry` without any check that the hash exists in `contract_class_changes` (i.e., has been declared):

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

The acknowledged TODO at line 898 confirms the missing validation. The identical gap exists in the deprecated path at `deprecated_execute_syscalls.cairo` lines 307–329, which also performs no declared-class check.

The analog to the external report is direct: just as `setInvestorLiquidateOnly` contained a check that made a state transition irreversible (once set to `true`, it could never be set to `false`), `execute_replace_class` is missing a check that would prevent an irreversible bad state transition — once the class hash is set to an undeclared value, the contract is permanently bricked with no recovery path.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

After a successful `replace_class` call with an undeclared class hash:

1. The contract's `StateEntry.class_hash` is committed to the global state root as the undeclared hash.
2. Every subsequent invocation of the contract fails at class lookup — no compiled class exists for the hash.
3. The contract cannot call `replace_class` again to self-recover, because execution itself is impossible.
4. The revert log records the old class hash (`CHANGE_CLASS_ENTRY, old_class_hash`), but this only helps within the same transaction. Once the transaction is committed, the state is final.
5. All ETH/ERC-20 tokens or other assets held in the contract's storage are permanently inaccessible.

---

### Likelihood Explanation

**High.** The entry path requires only deploying a contract and issuing a `replace_class` syscall with an arbitrary felt value as the class hash. No privileged role, leaked key, or external dependency is needed. Any contract deployer on the network can trigger this. A malicious actor could also craft a contract that tricks users into sending funds before calling `replace_class` with an invalid hash, rug-pulling the deposited assets permanently.

---

### Recommendation

Before committing the class hash update, verify that the supplied `class_hash` exists in `contract_class_changes` (i.e., has a non-zero compiled class hash entry). The check should be added at the location of the existing TODO in both `execute_replace_class` implementations:

```cairo
// Verify the new class hash is declared.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
assert_not_zero(compiled_class_hash);
```

This mirrors the enforcement already applied in `execute_declare_transaction`, where `prev_value=0` and `assert_not_zero(compiled_class_hash)` together guarantee that only valid, non-zero compiled class hashes are accepted.

---

### Proof of Concept

1. Attacker deploys `VaultContract` that accepts ETH deposits and stores balances in its storage.
2. Users deposit funds; `VaultContract` holds 1000 ETH.
3. Attacker calls `VaultContract.__execute__` with a crafted calldata that triggers `replace_class(class_hash=0xdeadbeef)` — an arbitrary undeclared felt.
4. The OS processes `execute_replace_class`:
   - Line 896: `let class_hash = request.class_hash;` → `0xdeadbeef`
   - Line 898: **No validation** — the TODO check is absent.
   - Lines 902–910: `StateEntry` is updated with `class_hash=0xdeadbeef` and written to `contract_state_changes`.
   - Line 912: Revert log records old class hash (only useful within this transaction).
5. Transaction succeeds and is included in a block. The global state root now commits `VaultContract → class_hash=0xdeadbeef`.
6. Any future `invoke`, `l1_handler`, or `replace_class` call targeting `VaultContract` fails at class resolution — no compiled class for `0xdeadbeef` exists.
7. The 1000 ETH in `VaultContract`'s storage is permanently frozen with no recovery mechanism.

**Relevant code locations:** [1](#0-0) [2](#0-1)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L896-916)
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
