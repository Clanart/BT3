### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Permanent Contract Freezing — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS accepts any arbitrary class hash from a contract without verifying that the hash corresponds to a previously declared class. This is structurally identical to the Morpho bug: just as Morpho assumed an asset was set as collateral after `supplyToPool` without verifying the actual resulting state, the StarkNet OS assumes the new class hash supplied to `replace_class` is valid without checking whether it exists in `contract_class_changes`. A contract that replaces its class with an undeclared hash becomes permanently inaccessible, freezing all funds it holds.

---

### Finding Description

In `execute_replace_class`, the OS reads the requested class hash directly from the syscall request and writes it into `contract_state_changes` with no existence check:

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

The TODO comment at line 898 is an explicit acknowledgment by the authors that the required invariant check — confirming the new class hash exists in `contract_class_changes` — is absent. [2](#0-1) 

By contrast, the `execute_declare_transaction` path in `transaction_impls.cairo` enforces `prev_value=0` to ensure a class is declared exactly once and that `compiled_class_hash` is non-zero before writing to `contract_class_changes`:

```cairo
assert_not_zero(compiled_class_hash);
dict_update{dict_ptr=contract_class_changes}(
    key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
);
``` [3](#0-2) 

There is no corresponding check in `execute_replace_class` that the supplied `class_hash` has a non-zero entry in `contract_class_changes`. The OS commits the invalid class hash to the state unconditionally.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once a contract's class hash is replaced with an undeclared value and the block is committed, the state root encodes an invalid class hash for that contract address. Every subsequent transaction that attempts to call or interact with that contract will fail to resolve the class, causing all calls to revert. Any ERC-20 tokens, ETH, or other assets held in the contract's storage become permanently inaccessible. The state transition is irreversible because the OS has already committed the invalid hash into the Patricia Merkle Tree via `state_update`. [4](#0-3) 

---

### Likelihood Explanation

**Medium.**

The `replace_class` syscall is callable by any contract from within its own execution context. The realistic attack paths are:

1. **Malicious contract deployer**: A deployer publishes a contract whose logic calls `replace_class` with a crafted invalid hash (e.g., `felt::MAX` or any value not in `contract_class_changes`). Users who deposit funds into this contract before the replacement trigger the freeze.
2. **Upgradeable contract with insufficient input validation**: Many DeFi contracts expose an `upgrade` entry point. If that entry point does not validate the new class hash before calling `replace_class`, an unprivileged caller who can invoke the upgrade path (e.g., through a governance bypass or missing access control) can supply an invalid hash.

Both paths are reachable by an unprivileged transaction sender with no special privileges required at the OS level.

---

### Recommendation

Inside `execute_replace_class`, after reading `class_hash` from the request, add a lookup into `contract_class_changes` to confirm the hash has a non-zero compiled class hash entry — mirroring the invariant enforced by `execute_declare_transaction`:

```cairo
// Verify the new class hash is a declared class.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
with_attr error_message("Class hash is not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

This check must occur before the `dict_update` to `contract_state_changes` so that the invalid state is never committed.

---

### Proof of Concept

1. Declare a valid class `A` (class hash `0xAAAA`, compiled class hash `0x1111`).
2. Deploy contract `C` using class `A`. Fund `C` with tokens.
3. From within `C`'s execution (e.g., via an `upgrade` entry point), call the `replace_class` syscall with `class_hash = 0xDEAD` — a value that has never been declared and has no entry in `contract_class_changes`.
4. The OS executes `execute_replace_class`:
   - Reads `class_hash = 0xDEAD` from the request. [5](#0-4) 
   - Skips the missing declared-class check (TODO at line 898). [2](#0-1) 
   - Writes `StateEntry(class_hash=0xDEAD, ...)` into `contract_state_changes`. [6](#0-5) 
5. `state_update` commits this entry into the Patricia Merkle Tree. [4](#0-3) 
6. All future transactions targeting `C` fail to resolve class `0xDEAD`. The contract is permanently frozen. All tokens in `C`'s storage are irrecoverable.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L816-819)
```text
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/os.cairo (L230-238)
```text
    let (squashed_os_state_update, state_update_output) = state_update{hash_ptr=pedersen_ptr}(
        os_state_update=OsStateUpdate(
            contract_state_changes_start=contract_state_changes_start,
            contract_state_changes_end=contract_state_changes,
            contract_class_changes_start=contract_class_changes_start,
            contract_class_changes_end=contract_class_changes,
        ),
        should_allocate_aliases=should_allocate_aliases(),
    );
```
