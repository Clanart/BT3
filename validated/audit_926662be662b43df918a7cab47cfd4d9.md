Now I have all the information needed to analyze this claim. Let me trace the exact code path.

The code trace confirms this is a real vulnerability. Here is the analysis:

---

### Title
Revert Log Sentinel Collision: Storage Write to Key `CHANGE_CLASS_ENTRY` Corrupts Class Hash and Skips Storage Revert — (`crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/revert.cairo`)

---

### Summary

`execute_storage_write` appends a `RevertLogEntry` using the raw storage key as the `selector` field, with no validation that the key is below the sentinel range. If a contract writes to storage key `2**251 + 1` (which equals `CHANGE_CLASS_ENTRY`), the resulting log entry is structurally identical to a class-change entry. During backward traversal in `revert_contract_changes`, the entry is misclassified: the class hash implicit variable is overwritten with the old storage value, and the storage write is silently skipped — leaving both the class hash and the storage slot in a corrupted post-revert state.

---

### Finding Description

**Sentinel definitions** (`revert.cairo`, lines 5–7):

```cairo
const CONTRACT_ADDRESS_UPPER_BOUND = 2 ** 251;
const CHANGE_CONTRACT_ENTRY = CONTRACT_ADDRESS_UPPER_BOUND;       // 2**251
const CHANGE_CLASS_ENTRY    = CHANGE_CONTRACT_ENTRY + 1;          // 2**251 + 1
``` [1](#0-0) 

**Revert log write in `execute_storage_write`** (`syscall_impls.cairo`, line 670):

```cairo
assert [revert_log] = RevertLogEntry(selector=storage_key, value=prev_value);
```

There is **no range check** on `storage_key` before this write. The only validation present is `assert request.reserved = 0` (line 662), which checks an unrelated field. [2](#0-1) 

**Backward traversal in `revert_contract_changes`** (`revert.cairo`, lines 81–101):

```cairo
tempvar selector = revert_log_end[0].selector;
if (selector == CHANGE_CONTRACT_ENTRY) { return (); }

if (selector == CHANGE_CLASS_ENTRY) {
    let class_hash = revert_log_end[0].value;   // ← set to old storage value
    return revert_contract_changes();            // ← storage NOT reverted
}

// Storage write entry — only reached if selector is neither sentinel.
let storage_key = selector;
...
assert storage_ptr[0] = DictAccess(key=storage_key, prev_value=prev_value, new_value=value);
``` [3](#0-2) 

When `storage_key == CHANGE_CLASS_ENTRY`, the entry written by `execute_storage_write` is byte-for-byte identical to one written by `execute_replace_class`. The traversal function cannot distinguish them. It therefore:

1. Sets `class_hash` to `prev_value` (the old storage value at key `2**251+1`, typically `0`), instead of the contract's actual pre-transaction class hash.
2. Skips writing the `DictAccess` entry that would revert the storage slot — so the storage write at key `2**251+1` persists after the revert.

For comparison, `execute_replace_class` correctly writes:

```cairo
assert [revert_log] = RevertLogEntry(selector=CHANGE_CLASS_ENTRY, value=state_entry.class_hash);
``` [4](#0-3) 

The two entry types share the same `RevertLogEntry` struct with no type tag, relying entirely on the `selector` value to distinguish them — a design that breaks when a user-controlled storage key equals a sentinel. [5](#0-4) 

---

### Impact Explanation

After the reverted transaction, the OS-computed state has:

- **Class hash corrupted**: set to whatever was previously stored at key `2**251+1` (typically `0`, making the contract unexecutable).
- **Storage slot `2**251+1` not reverted**: retains the value written by the reverted transaction.

The OS proof attests to this incorrect state. Full nodes or alternative implementations that re-execute transactions independently will compute the correct post-revert state (class hash unchanged, storage reverted). The two groups commit to different state roots, producing a **chain split / network partition**.

**Impact: High — Unintended chain split (network partition).**

---

### Likelihood Explanation

The attack requires deploying a contract that calls `storage_write` with key `2**251+1` and then causes the transaction to revert. Both steps are fully within the capability of an unprivileged user:

- `2**251 + 1` is a valid `felt252` field element (well below the Stark prime `2**251 + 17·2**192 + 1`).
- The OS imposes no upper-bound check on storage keys.
- Transaction reversion can be triggered by running out of gas or by an explicit `panic`.

No privileged access, leaked keys, or external dependencies are required.

---

### Recommendation

Add an explicit upper-bound assertion on the storage key inside `execute_storage_write`, before the revert log entry is written:

```cairo
// Ensure storage_key cannot collide with revert-log sentinels.
assert_lt(storage_key, CONTRACT_ADDRESS_UPPER_BOUND);  // key < 2**251
```

This mirrors the existing pattern used for contract addresses and is consistent with the intent of the sentinel design. Alternatively, prefix revert log entries with an explicit type tag field so that storage entries and class-change entries are structurally distinguishable regardless of the key value.

---

### Proof of Concept

1. Declare and deploy a contract whose `__execute__` function calls `storage_write(key=2**251+1, value=1)` and then panics (to force revert).
2. Submit an `invoke` transaction targeting that contract.
3. Observe the OS revert log: it contains `RevertLogEntry(selector=2**251+1, value=0)`.
4. `revert_contract_changes` hits the `selector == CHANGE_CLASS_ENTRY` branch, sets `class_hash = 0`, and returns without writing a storage revert entry.
5. Assert post-revert state: `class_hash == 0` (corrupted) and `storage[2**251+1] == 1` (not reverted).
6. A correct re-execution produces `class_hash == <original>` and `storage[2**251+1] == 0` — divergent state, chain split.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/revert.cairo (L5-7)
```text
const CONTRACT_ADDRESS_UPPER_BOUND = 2 ** 251;
const CHANGE_CONTRACT_ENTRY = CONTRACT_ADDRESS_UPPER_BOUND;
const CHANGE_CLASS_ENTRY = CHANGE_CONTRACT_ENTRY + 1;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/revert.cairo (L19-24)
```text
struct RevertLogEntry {
    // Either the storage key, CHANGE_CONTRACT_ENTRY or CHANGE_CLASS_ENTRY.
    selector: felt,
    // The relevant (old) value.
    value: felt,
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/revert.cairo (L81-101)
```text
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L660-671)
```text
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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L912-913)
```text
    assert [revert_log] = RevertLogEntry(selector=CHANGE_CLASS_ENTRY, value=state_entry.class_hash);
    let revert_log = &revert_log[1];
```
