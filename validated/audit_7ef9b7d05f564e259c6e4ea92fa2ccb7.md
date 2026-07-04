### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS does not verify that the new `class_hash` supplied by the caller corresponds to a previously declared class. The function also structurally lacks access to `contract_class_changes`, the dictionary that tracks declared classes, making the check impossible at the current call site. A malicious contract can call `replace_class` with an arbitrary, undeclared class hash, permanently rendering itself uncallable and freezing any funds it holds.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function accepts a caller-supplied `class_hash` and unconditionally writes it into `contract_state_changes` for the calling contract:

```cairo
func execute_replace_class{
    range_check_ptr,
    syscall_ptr: felt*,
    contract_state_changes: DictAccess*,   // ← only state changes, no class changes
    revert_log: RevertLogEntry*,
}(contract_address: felt) {
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

The developer-acknowledged TODO at line 898 confirms the missing check. Critically, the function's implicit argument list does not include `contract_class_changes`, so it structurally cannot perform the lookup needed to verify that `class_hash` is declared.

Contrast this with `execute_declare_transaction`, which correctly enforces that a class can only be declared once by using `prev_value=0` in a `dict_update` against `contract_class_changes` and asserting `compiled_class_hash != 0`:

```cairo
// Note that prev_value=0 enforces that a class may be declared only once.
assert_not_zero(compiled_class_hash);
dict_update{dict_ptr=contract_class_changes}(
    key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
);
```

No equivalent guard exists in `execute_replace_class`. Any non-zero felt value — including one that has never been declared — is accepted as the new class hash.

---

### Impact Explanation

**Impact: Critical — Permanent freezing of funds.**

Once a contract's `class_hash` is set to an undeclared value, every subsequent attempt to call that contract will fail at the class-lookup stage inside the OS (the hint that loads the class bytecode will find no entry). The contract becomes permanently uncallable. Any ERC-20 tokens, ETH, or STRK held in the contract's storage are irrecoverably frozen, because no entry point (including a withdrawal function) can ever be executed again.

---

### Likelihood Explanation

**Likelihood: High.**

The attack path requires only:
1. Deploying a contract (permissionless).
2. Attracting user deposits (e.g., presenting as a vault, escrow, or DeFi primitive).
3. Issuing a single `replace_class` syscall with an arbitrary undeclared felt as the new class hash.

Step 3 is a single, cheap, permissionless operation available to any contract. No privileged role, leaked key, or external dependency is required. The OS proof for the block containing the `replace_class` call is valid (the call itself succeeds), so the state change is finalized on L1. There is no on-chain mechanism to reverse it.

---

### Recommendation

1. Add `contract_class_changes: DictAccess*` to the implicit arguments of `execute_replace_class`.
2. Before writing the new `StateEntry`, perform a `dict_read` on `contract_class_changes` keyed by `request.class_hash` and assert the returned value is non-zero (i.e., the class has been declared).
3. Optionally, also assert `request.class_hash != 0` as a cheap first guard.

This mirrors the pattern already used in `execute_declare_transaction` and closes the structural gap noted in the TODO comment.

---

### Proof of Concept

1. Attacker deploys `VaultContract` with a legitimate `class_hash` (e.g., a simple ERC-20 vault). Users deposit 1,000 STRK.
2. Attacker's contract internally calls the `replace_class` syscall with `class_hash = 0xdeadbeef` (an arbitrary felt that has never been declared via a `declare` transaction).
3. `execute_replace_class` accepts the value without any check and writes `StateEntry(class_hash=0xdeadbeef, ...)` into `contract_state_changes`.
4. The block is proven and finalized on L1. The state transition is permanent.
5. Any subsequent transaction attempting to call `VaultContract` (e.g., to withdraw funds) fails at the OS class-lookup stage. The 1,000 STRK are permanently frozen.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L878-883)
```text
func execute_replace_class{
    range_check_ptr,
    syscall_ptr: felt*,
    contract_state_changes: DictAccess*,
    revert_log: RevertLogEntry*,
}(contract_address: felt) {
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L896-910)
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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L815-819)
```text
    // Note that prev_value=0 enforces that a class may be declared only once.
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```
