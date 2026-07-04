### Title
Missing Declared-Class Existence Check in `execute_replace_class` Allows Permanent Freezing of Contract Funds - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` function in the StarkNet OS program updates a contract's class hash in `contract_state_changes` without verifying that the supplied class hash has actually been declared in `contract_class_changes`. This is the direct analog of the `addFeedFor` bug: just as that function failed to check the inverse feed's existence before registering a new one, `execute_replace_class` fails to check that the target class hash exists before committing the state update. Any contract can call `replace_class` with an arbitrary, undeclared class hash, permanently making itself unexecutable and freezing all funds it holds.

---

### Finding Description

In `execute_replace_class` (`syscall_impls.cairo`, lines 877–916), after gas is deducted, the function reads `request.class_hash` and immediately writes it into `contract_state_changes` via `dict_update`:

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

The self-admitted TODO at line 898 confirms the missing check. There is no lookup into `contract_class_changes` to verify that `class_hash` maps to a valid compiled class hash. The `contract_class_changes` dict is not even an implicit parameter of this function.

The same flaw exists in the deprecated path at `deprecated_execute_syscalls.cairo` lines 307–329, where `execute_replace_class` also writes the caller-supplied `class_hash` directly into `contract_state_changes` with no existence check and no TODO acknowledgment.

By contrast, `execute_declare_transaction` correctly enforces `prev_value=0` to prevent double-declaration, and `deploy_contract` enforces `assert state_entry.class_hash = UNINITIALIZED_CLASS_HASH` to prevent double-deployment. No equivalent guard exists for `replace_class`.

---

### Impact Explanation

After `replace_class` is called with an undeclared class hash:

1. The contract's `class_hash` field in `contract_state_changes` is permanently set to the undeclared value.
2. Every subsequent call to that contract causes the OS to look up the class hash in `contract_class_changes` and find no entry.
3. The contract becomes permanently unexecutable — no entry point (including token transfer or withdrawal) can ever run again.
4. All ERC-20 tokens, ETH, or other assets held by the contract are permanently frozen with no recovery path.

**Impact: Critical — Permanent freezing of funds.**

---

### Likelihood Explanation

The `replace_class` syscall is callable by any contract during normal execution. No privileged role, operator key, or external dependency is required. A malicious contract deployer can deploy a contract, fund it (or wait for others to fund it), and then call `replace_class` with an arbitrary felt value as the class hash. The OS enforces no constraint on the value of `request.class_hash` beyond what gas allows. The attack is fully reachable by an unprivileged transaction sender.

---

### Recommendation

Add a read from `contract_class_changes` inside `execute_replace_class` to assert that the supplied `class_hash` maps to a non-zero compiled class hash before committing the state update. Concretely, perform a `dict_read` on `contract_class_changes` keyed by `class_hash` and assert the result is non-zero (i.e., the class has been declared). This mirrors how `execute_declare_transaction` uses `prev_value=0` to enforce that a class can only be declared once. The same fix must be applied to the deprecated variant in `deprecated_execute_syscalls.cairo`.

---

### Proof of Concept

1. **Deploy** a contract `C` that holds user funds (e.g., an ERC-20 vault). `C` is deployed with a valid, declared class hash `H_valid`.
2. Users deposit funds into `C`. `C`'s storage now holds balances.
3. **Attacker** (the contract itself, or its owner via an invoke transaction) calls the `replace_class` syscall from within `C`, passing `class_hash = 0xdeadbeef` — a felt value that has never been declared via a Declare transaction and therefore has no entry in `contract_class_changes`.
4. `execute_replace_class` in `syscall_impls.cairo` (line 896–910) reads `class_hash = 0xdeadbeef` and writes `new StateEntry(class_hash=0xdeadbeef, ...)` into `contract_state_changes` without any lookup into `contract_class_changes`.
5. The block is proven and finalized. `C`'s on-chain class hash is now `0xdeadbeef`.
6. Any future transaction invoking `C` (e.g., a withdrawal) causes the OS to look up `0xdeadbeef` in `contract_class_changes`. No entry exists. Execution cannot proceed.
7. All funds in `C` are permanently frozen.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

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
