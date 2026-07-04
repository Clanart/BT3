### Title
Missing Zero Class Hash Validation in `execute_replace_class` Allows Permanent Contract Freezing - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo` and `deprecated_execute_syscalls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation in both the new and deprecated syscall handlers does not validate that the caller-supplied `class_hash` is non-zero. This allows any contract to replace its own class hash with `UNINITIALIZED_CLASS_HASH` (0), permanently destroying its own executability and freezing any funds it holds.

---

### Finding Description

`UNINITIALIZED_CLASS_HASH` is defined as `0` in `commitment.cairo`: [1](#0-0) 

The new-style `execute_replace_class` in `syscall_impls.cairo` reads the caller-supplied `class_hash` directly from the request and writes it into `contract_state_changes` with no zero-value guard: [2](#0-1) 

The developer-acknowledged TODO at line 898 confirms the missing validation: [3](#0-2) 

The deprecated handler in `deprecated_execute_syscalls.cairo` has the identical gap — it reads `class_hash` from the syscall pointer and writes it directly to state with no zero check: [4](#0-3) 

This is an asymmetric omission. The OS enforces non-zero class hash in two analogous places but not here:

1. **Deploy**: `deploy_contract` asserts `state_entry.class_hash = UNINITIALIZED_CLASS_HASH` to prevent deploying over an initialized contract — the inverse invariant is enforced on the way in. [5](#0-4) 

2. **Declare**: `execute_declare_transaction` calls `assert_not_zero(compiled_class_hash)` before writing to `contract_class_changes`. [6](#0-5) 

Neither `execute_replace_class` implementation performs the equivalent `assert_not_zero(class_hash)`.

---

### Impact Explanation

When a contract's `class_hash` is set to `0` (`UNINITIALIZED_CLASS_HASH`), the OS state commitment logic in `get_contract_state_hash` treats it as an uninitialized slot: [7](#0-6) 

Any subsequent call to the contract will attempt to dispatch to a class with hash `0`. Since no valid class is ever declared with hash `0` (the declare path itself forbids it), every future call to the contract will fail at entry-point resolution. The contract becomes permanently uncallable. All ERC-20 tokens, ETH, or other assets held in the contract's storage are permanently frozen with no recovery path.

**Impact: Critical — Permanent freezing of funds.**

---

### Likelihood Explanation

The attack surface is reachable by any unprivileged contract deployer or transaction sender:

1. A contract deployer deploys a contract whose logic calls `replace_class(0)` under some condition (e.g., triggered by a specific calldata pattern).
2. An attacker sends a transaction that satisfies that condition.
3. The OS executes `execute_replace_class` with `class_hash = 0`, writes `UNINITIALIZED_CLASS_HASH` into `contract_state_changes`, and commits it to the global state root.
4. The contract is permanently bricked.

More critically, any existing contract that exposes a `replace_class` path with insufficient input validation on the class hash (a common pattern in upgradeable contracts) becomes exploitable by an external attacker to freeze the contract's funds. The OS is the last line of defense and currently provides none.

---

### Recommendation

Add an `assert_not_zero(class_hash)` guard in both `execute_replace_class` implementations before writing to `contract_state_changes`, mirroring the pattern already used in `execute_declare_transaction`:

In `syscall_impls.cairo`, after line 896 (`let class_hash = request.class_hash;`):
```cairo
assert_not_zero(class_hash);
```

In `deprecated_execute_syscalls.cairo`, after line 311 (`let class_hash = syscall_ptr.class_hash;`):
```cairo
assert_not_zero(class_hash);
```

---

### Proof of Concept

1. Deploy a contract `VictimContract` that holds user funds and exposes a `replace_class(class_hash: felt)` entrypoint that calls the `replace_class` syscall with the provided argument.
2. Attacker calls `VictimContract.replace_class(0)`.
3. The OS executes `execute_replace_class` with `class_hash = 0`. No zero-check exists; the function proceeds to `dict_update` writing `StateEntry(class_hash=0, ...)` into `contract_state_changes`.
4. After block finalization, `VictimContract`'s class hash in the global state is `UNINITIALIZED_CLASS_HASH = 0`.
5. Any subsequent invoke to `VictimContract` fails at entry-point lookup — no class with hash `0` exists.
6. All funds in `VictimContract`'s storage are permanently inaccessible.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/commitment.cairo (L16-16)
```text
const UNINITIALIZED_CLASS_HASH = 0;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/commitment.cairo (L55-61)
```text
    if (class_hash == UNINITIALIZED_CLASS_HASH) {
        if (storage_root == 0) {
            if (nonce == 0) {
                return (hash=0);
            }
        }
    }
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deprecated_execute_syscalls.cairo (L307-328)
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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deploy_contract.cairo (L53-53)
```text
    assert state_entry.class_hash = UNINITIALIZED_CLASS_HASH;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L816-818)
```text
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
```
