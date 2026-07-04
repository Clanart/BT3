### Title
Revert Log Sentinel Collision Allows Reverted Storage Writes to Persist — (`File: execution/revert.cairo`, `execution/syscall_impls.cairo`)

---

### Summary

The revert log in the StarkNet OS uses two sentinel felt values (`CHANGE_CONTRACT_ENTRY = 2^251` and `CHANGE_CLASS_ENTRY = 2^251 + 1`) as in-band markers within the same flat array that also stores raw storage write entries `(storage_key, prev_value)`. There is no validation in `execute_storage_write` that the storage key is not equal to these sentinel values. A malicious contract can write to storage key `2^251` with a crafted previous value, injecting a fake sentinel into the revert log. When the transaction reverts, `handle_revert` / `revert_contract_changes` misinterprets the injected entry as a real sentinel, causing the revert to terminate early or jump to the wrong contract — leaving storage changes that should have been rolled back permanently committed to state.

---

### Finding Description

**Root cause — no storage key validation in `execute_storage_write`:** [1](#0-0) 

The function writes the raw `request.key` directly into the revert log as `selector`:

```cairo
tempvar storage_key = request.key;
assert [storage_ptr] = DictAccess(key=storage_key, prev_value=prev_value, new_value=request.value);
...
assert [revert_log] = RevertLogEntry(selector=storage_key, value=prev_value);
```

No check is performed that `storage_key ≠ CHANGE_CONTRACT_ENTRY` and `storage_key ≠ CHANGE_CLASS_ENTRY`.

**Sentinel constants used as in-band markers:** [2](#0-1) 

```cairo
const CONTRACT_ADDRESS_UPPER_BOUND = 2 ** 251;
const CHANGE_CONTRACT_ENTRY = CONTRACT_ADDRESS_UPPER_BOUND;   // 2^251
const CHANGE_CLASS_ENTRY    = CHANGE_CONTRACT_ENTRY + 1;      // 2^251 + 1
```

The StarkNet field prime is `P = 2^251 + 17·2^192 + 1`, so `2^251` and `2^251 + 1` are valid felt values and valid storage keys.

**Termination entry written by `init_revert_log`:** [3](#0-2) 

```cairo
assert revert_log[0] = RevertLogEntry(
    selector=CHANGE_CONTRACT_ENTRY, value=CONTRACT_ADDRESS_UPPER_BOUND
);
```

The termination sentinel is `(selector=2^251, value=2^251)`.

**Backwards processing in `revert_contract_changes` cannot distinguish real sentinels from injected ones:** [4](#0-3) 

```cairo
tempvar selector = revert_log_end[0].selector;
if (selector == CHANGE_CONTRACT_ENTRY) {
    return ();   // ← stops here, hands control back to handle_revert
}
if (selector == CHANGE_CLASS_ENTRY) {
    let class_hash = revert_log_end[0].value;   // ← treats prev_value as class hash
    return revert_contract_changes();
}
// else: treat as storage write
```

**`handle_revert` uses the `value` field of the fake sentinel as the next contract address:** [5](#0-4) 

```cairo
tempvar next_contract_address = revert_log_end[0].value;
if (next_contract_address == CONTRACT_ADDRESS_UPPER_BOUND) {
    return ();   // ← terminates entire revert if value == 2^251
}
return handle_revert(contract_address=next_contract_address, revert_log_end=revert_log_end);
```

**Attack scenario:**

1. Attacker deploys a malicious contract `M`.
2. In a setup transaction, `M` writes value `2^251` to storage key `2^251`. The revert log entry for this write is `(selector=2^251, value=0)` (prev was 0). This write is committed.
3. In the attack transaction, `M` is called (e.g., via `call_contract`):
   a. `M` performs meaningful storage writes (e.g., mints tokens, modifies balances).
   b. `M` writes to storage key `2^251` again. Now `prev_value = 2^251` (from step 2). The revert log entry appended is `RevertLogEntry(selector=2^251, value=2^251)` — identical to the termination sentinel.
   c. `M` reverts (out of gas, explicit failure, or the outer call reverts it).
4. `handle_revert` processes the log backwards. It hits the injected `(2^251, 2^251)` entry, interprets it as the termination sentinel, and returns immediately.
5. All storage writes from step 3a are **never rolled back** — they are permanently committed to `contract_state_changes`.

The `call_contract` path that triggers revert handling: [6](#0-5) 

---

### Impact Explanation

**Critical — Direct loss of funds.**

Storage writes that should have been reverted persist in the global `contract_state_changes` dict and are committed to the Patricia Merkle Tree via `state_update`. If the un-reverted writes affect an ERC-20 token contract's balance mapping (e.g., the fee token or any user token), an attacker can credit themselves tokens without a valid transfer, constituting direct loss of funds from other users or the protocol. The corrupted state root is then proven and settled on L1, making the theft permanent and irreversible.

---

### Likelihood Explanation

**High.** Any unprivileged transaction sender can deploy a contract and invoke it. Writing to an arbitrary storage key (including `2^251`) requires no special privilege — `execute_storage_write` imposes no key range restriction. The setup step (writing `2^251` to slot `2^251`) is a single ordinary transaction. The attack is deterministic and requires no brute force, timing, or external dependency.

---

### Recommendation

In `execute_storage_write`, validate that the storage key does not collide with any revert-log sentinel before appending to the revert log:

```cairo
// Reject storage keys that collide with revert-log sentinels.
assert_not_equal(storage_key, CHANGE_CONTRACT_ENTRY);
assert_not_equal(storage_key, CHANGE_CLASS_ENTRY);
```

Alternatively, redesign the revert log to use an out-of-band tagging scheme (e.g., a separate `entry_type` field) so that storage keys and sentinel markers occupy disjoint namespaces and no collision is possible regardless of the key value.

---

### Proof of Concept

```
// Step 1 – setup tx (attacker contract M, address A):
storage_write(key=2^251, value=2^251)
// Committed: M.storage[2^251] = 2^251

// Step 2 – attack tx:
//   Outer account calls M via call_contract.
//   Inside M:
storage_write(key=TOKEN_BALANCE_SLOT, value=LARGE_AMOUNT)  // mint tokens
storage_write(key=2^251, value=<anything>)
//   Revert log now contains (among others):
//     RevertLogEntry(selector=2^251, value=2^251)  ← injected termination sentinel
//   M reverts (e.g., explicit failure).

// handle_revert processes backwards:
//   Hits RevertLogEntry(selector=2^251, value=2^251)
//   → selector == CHANGE_CONTRACT_ENTRY → returns to handle_revert
//   → next_contract_address = 2^251 == CONTRACT_ADDRESS_UPPER_BOUND → returns (terminates)
//   TOKEN_BALANCE_SLOT write is NEVER reverted.

// Result: M.storage[TOKEN_BALANCE_SLOT] = LARGE_AMOUNT is committed to state.
``` [7](#0-6) [2](#0-1) [8](#0-7) [4](#0-3)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L638-686)
```text
func execute_storage_write{
    range_check_ptr,
    syscall_ptr: felt*,
    contract_state_changes: DictAccess*,
    revert_log: RevertLogEntry*,
}(contract_address: felt) {
    alloc_locals;
    let request = cast(syscall_ptr + RequestHeader.SIZE, StorageWriteRequest*);

    // Reduce gas.
    let success = reduce_syscall_gas_and_write_response_header(
        total_gas_cost=STORAGE_WRITE_GAS_COST, request_struct_size=StorageWriteRequest.SIZE
    );
    if (success == FALSE) {
        // Not enough gas to execute the syscall.
        return ();
    }

    local prev_value: felt;
    local state_entry: StateEntry*;
    %{ WriteSyscallResult %}

    // Update the contract's storage.
    static_assert StorageWriteRequest.SIZE == 3;
    assert request.reserved = 0;
    tempvar storage_ptr = state_entry.storage_ptr;
    tempvar storage_key = request.key;
    assert [storage_ptr] = DictAccess(
        key=storage_key, prev_value=prev_value, new_value=request.value
    );
    let storage_ptr = storage_ptr + DictAccess.SIZE;

    assert [revert_log] = RevertLogEntry(selector=storage_key, value=prev_value);
    let revert_log = &revert_log[1];

    // Update the state.
    dict_update{dict_ptr=contract_state_changes}(
        key=contract_address,
        prev_value=cast(state_entry, felt),
        new_value=cast(
            new StateEntry(
                class_hash=state_entry.class_hash, storage_ptr=storage_ptr, nonce=state_entry.nonce
            ),
            felt,
        ),
    );

    return ();
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/revert.cairo (L5-7)
```text
const CONTRACT_ADDRESS_UPPER_BOUND = 2 ** 251;
const CHANGE_CONTRACT_ENTRY = CONTRACT_ADDRESS_UPPER_BOUND;
const CHANGE_CLASS_ENTRY = CHANGE_CONTRACT_ENTRY + 1;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/revert.cairo (L26-33)
```text
func init_revert_log() -> RevertLogEntry* {
    let (revert_log: RevertLogEntry*) = alloc();
    // Add termination entry.
    assert revert_log[0] = RevertLogEntry(
        selector=CHANGE_CONTRACT_ENTRY, value=CONTRACT_ADDRESS_UPPER_BOUND
    );
    return &revert_log[1];
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/revert.cairo (L36-71)
```text
// changes.
func handle_revert{contract_state_changes: DictAccess*}(
    contract_address, revert_log_end: RevertLogEntry*
) {
    alloc_locals;

    local state_entry: StateEntry*;

    %{ PrepareStateEntryForRevert %}

    let class_hash = state_entry.class_hash;
    let storage_ptr = state_entry.storage_ptr;
    with class_hash, storage_ptr, revert_log_end {
        revert_contract_changes();
    }

    dict_update{dict_ptr=contract_state_changes}(
        key=contract_address,
        prev_value=cast(state_entry, felt),
        new_value=cast(
            new StateEntry(class_hash=class_hash, storage_ptr=storage_ptr, nonce=state_entry.nonce),
            felt,
        ),
    );

    // `revert_contract_changes()` stops where
    // `revert_log_end[0].selector == CHANGE_CONTRACT_ENTRY`.
    tempvar next_contract_address = revert_log_end[0].value;

    if (next_contract_address == CONTRACT_ADDRESS_UPPER_BOUND) {
        // Finish backward processing: this entry marks the beginning of the revert log.
        return ();
    }

    return handle_revert(contract_address=next_contract_address, revert_log_end=revert_log_end);
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/revert.cairo (L75-101)
```text
func revert_contract_changes{
    class_hash: felt, storage_ptr: DictAccess*, revert_log_end: RevertLogEntry*
}() {
    alloc_locals;
    let revert_log_end = &revert_log_end[-1];

    tempvar selector = revert_log_end[0].selector;
    if (selector == CHANGE_CONTRACT_ENTRY) {
        // Change contract entries are handled by the caller.
        return ();
    }

    if (selector == CHANGE_CLASS_ENTRY) {
        // Change class entry.
        let class_hash = revert_log_end[0].value;
        return revert_contract_changes();
    }

    // Storage write entry.
    let storage_key = selector;
    let value = revert_log_end[0].value;
    local prev_value;
    %{ ReadStorageKeyForRevert %}
    assert storage_ptr[0] = DictAccess(key=storage_key, prev_value=prev_value, new_value=value);
    %{ WriteStorageKeyForRevert %}
    let storage_ptr = &storage_ptr[1];
    return revert_contract_changes();
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L309-320)
```text
    if (is_reverted != FALSE) {
        handle_revert(
            contract_address=execution_context.execution_info.contract_address,
            revert_log_end=revert_log,
        );
        // Restore the original revert log and outputs.
        let revert_log = orig_revert_log;
        let outputs = orig_outputs;
        return (
            is_reverted=is_reverted, retdata_size=retdata_end - retdata_start, retdata=retdata_start
        );
    }
```
