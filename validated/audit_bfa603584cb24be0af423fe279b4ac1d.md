### Title
Missing Declared Class Validation in `execute_replace_class` Allows Permanent Fund Freezing — (`File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS accepts an arbitrary `class_hash` value and writes it into `contract_state_changes` without verifying that the hash corresponds to a declared class in `contract_class_changes`. This is the direct analog of the Spartan M-10 bug: a state update is applied in one registry (contract state) without propagating or validating consistency with a dependent registry (class declarations). Any contract owner can permanently brick their contract — and freeze all funds held in it — by calling `replace_class` with an undeclared class hash.

---

### Finding Description

In `syscall_impls.cairo`, `execute_replace_class` reads the requested `class_hash` from the syscall request and unconditionally writes it into `contract_state_changes`:

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

The TODO comment at line 898 explicitly acknowledges the missing check. There is no assertion that `class_hash` exists as a key in `contract_class_changes` (the class declaration registry). The OS accepts the block and commits the new state root with the invalid class hash embedded in the contract's `StateEntry`.

The same gap exists in the deprecated path:

```cairo
func execute_replace_class{contract_state_changes: DictAccess*, revert_log: RevertLogEntry*}(
    contract_address, syscall_ptr: ReplaceClass*
) {
    ...
    let class_hash = syscall_ptr.class_hash;
    local state_entry: StateEntry*;
    %{ GetContractAddressStateEntry %}
    tempvar new_state_entry = new StateEntry(
        class_hash=class_hash, storage_ptr=state_entry.storage_ptr, nonce=state_entry.nonce
    );
    dict_update{dict_ptr=contract_state_changes}(...);
```

No cross-check against `contract_class_changes` is performed in either path.

---

### Impact Explanation

Once a contract's `class_hash` is set to an undeclared value and the block is finalized:

- The new state root is committed with the invalid class hash in the contract's leaf.
- In every subsequent block, any transaction targeting that contract will attempt to look up the class hash in the `compiled_class_facts_bundle`. Since the hash was never declared, no compiled class exists for it.
- Execution of the contract is permanently impossible.
- All funds (tokens, NFTs, or any assets) stored in the contract's storage are permanently frozen with no recovery path.

**Impact: Critical — Permanent freezing of funds.**

---

### Likelihood Explanation

The attack requires only that the attacker controls a deployed contract (e.g., a token vault or escrow they deployed). They call `replace_class` with an arbitrary undeclared hash in a single transaction. No privileged role, leaked key, or external dependency is needed. The OS Cairo code has no guard — the TODO comment confirms the check is simply absent. Any unprivileged contract deployer can trigger this.

---

### Recommendation

Before writing the new `class_hash` into `contract_state_changes`, assert that the hash exists in `contract_class_changes` (i.e., it was declared in the current or a prior block). Concretely, perform a `dict_read` on `contract_class_changes` for the given `class_hash` and assert the result is non-zero (i.e., a valid compiled class hash was registered). The TODO comment at line 898 already identifies this as the required fix.

---

### Proof of Concept

1. Attacker deploys a contract (e.g., a token vault holding user funds).
2. Attacker submits a transaction calling the `replace_class` syscall with `class_hash = 0xdeadbeef` (an arbitrary value never declared via a `declare` transaction).
3. `execute_replace_class` in `syscall_impls.cairo` (line 896–910) writes `class_hash = 0xdeadbeef` into `contract_state_changes` with no validation against `contract_class_changes`.
4. The OS finalizes the block; `state_update` squashes and commits the Patricia tree, producing a new state root that encodes `class_hash = 0xdeadbeef` for the contract.
5. In the next block, any transaction targeting the contract reads `class_hash = 0xdeadbeef` from state, then searches `compiled_class_facts_bundle` — finding nothing. Execution fails unconditionally.
6. All funds in the contract's storage are permanently inaccessible.

**Root cause files:**

- `execute_replace_class` (non-deprecated): [1](#0-0) 
- `execute_replace_class` (deprecated): [2](#0-1) 
- `charge_fee` (shows how `class_hash` from state is used directly for execution, confirming the downstream impact): [3](#0-2)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L138-163)
```text
    local fee_token_address = block_context.os_global_context.starknet_os_config.fee_token_address;
    let (fee_state_entry: StateEntry*) = dict_read{dict_ptr=contract_state_changes}(
        key=fee_token_address
    );
    let (__fp__, _) = get_fp_and_pc();
    // Use block_info directly from block_context, so that charge_fee will always run in
    // execute-mode rather than validate-mode.
    local execution_context: ExecutionContext = ExecutionContext(
        entry_point_type=ENTRY_POINT_TYPE_EXTERNAL,
        class_hash=fee_state_entry.class_hash,
        calldata_size=TransferCallData.SIZE,
        calldata=&calldata,
        execution_info=new ExecutionInfo(
            block_info=block_context.block_info_for_execute,
            tx_info=tx_info,
            caller_address=tx_info.account_contract_address,
            contract_address=fee_token_address,
            selector=TRANSFER_ENTRY_POINT_SELECTOR,
        ),
        deprecated_tx_info=tx_execution_context.deprecated_tx_info,
    );

    let remaining_gas = DEFAULT_INITIAL_GAS_COST;
    non_reverting_select_execute_entry_point_func{remaining_gas=remaining_gas}(
        block_context=block_context, execution_context=&execution_context
    );
```
