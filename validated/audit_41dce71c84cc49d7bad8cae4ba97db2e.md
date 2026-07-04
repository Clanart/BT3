### Title
Missing Declared Class Validation in `execute_replace_class` Enables Permanent Fund Freezing — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary
The `execute_replace_class` syscall implementation in the StarkNet OS accepts any arbitrary felt value as a replacement class hash without verifying that the hash corresponds to a class that has actually been declared on-chain. A contract can substitute its own class hash with an undeclared value, permanently rendering itself unexecutable and freezing any funds it holds. The missing check is explicitly acknowledged by a TODO comment in the production code.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function (lines 877–916) reads the requested class hash directly from the syscall request and writes it into `contract_state_changes` with no validation that the hash is declared:

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

The `class_hash` value is taken verbatim from `request.class_hash` (line 896) and is never cross-checked against the `contract_class_changes` dictionary (which tracks declared classes) or any other registry. The dict update is then squashed and committed into the Patricia Merkle Tree by `state_update` in `os.cairo`, making the change permanent and provable. [2](#0-1) 

The analog to the external report's vulnerability class is direct: just as the taker in the NFT order protocol could substitute collateral NFT A with an arbitrary NFT B of the same collection (bypassing identity validation while satisfying type validation), a contract here can substitute its class hash with any arbitrary felt (bypassing identity validation — "is this hash declared?" — while satisfying type validation — "is this a felt?").

---

### Impact Explanation

After `replace_class` succeeds with an undeclared hash, the contract's `class_hash` field in the global state permanently points

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L896-913)
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
