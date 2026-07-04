### Title
Missing Declared Class Hash Validation in `execute_replace_class` Allows Permanent Contract Freezing - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS accepts any arbitrary class hash without verifying that the hash corresponds to a previously declared contract class. This is the direct analog of the ERC20 report's root cause: performing a state-mutating operation against an unvalidated target without confirming the target actually exists. A malicious contract deployer can exploit this to permanently freeze user funds by replacing a contract's class hash with an undeclared value, rendering the contract permanently uncallable.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function processes the `replace_class` syscall by reading the requested `class_hash` from the syscall request and unconditionally writing it into `contract_state_changes`:

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

The developer-inserted TODO comment at line 898 explicitly acknowledges the missing check. No assertion or lookup is performed against `contract_class_changes` (the dict that maps declared class hashes to their compiled class hashes) to confirm the new class hash was ever declared. [1](#0-0) 

Contrast this with the `execute_declare_transaction` path, which enforces `prev_value=0` and requires a valid `compiled_class_hash` before writing to `contract_class_changes`: [2](#0-1) 

When `execute_entry_point` later attempts to execute a call against the contract with the replaced (undeclared) class hash, it performs a `dict_read` on `contract_class_changes` for the new class hash. Since the hash was never declared, this returns `0`. The subsequent `find_element` call then searches for a `CompiledClassFact` with hash `0`, which does not exist in the `compiled_class_facts` bundle, causing the OS execution to fail irrecoverably for any transaction targeting that contract: [3](#0-2) 

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

A malicious contract deployer can:
1. Deploy a contract that accepts user deposits (e.g., a fake vault or staking contract).
2. After accumulating user funds, call `replace_class` with an arbitrary, undeclared class hash (e.g., `0xdeadbeef`).
3. The OS unconditionally writes this invalid class hash into the contract's `StateEntry` in `contract_state_changes`.
4. The state commitment is finalized with this invalid class hash as the canonical on-chain state.
5. All subsequent calls to the contract — including user withdrawal attempts — fail at the OS level because no compiled class exists for the invalid hash.
6. User funds are permanently locked with no recovery path.

---

### Likelihood Explanation

**Low.** The attack requires a malicious contract deployer who intentionally constructs a contract to lure user deposits before executing the freeze. It is not exploitable by a random transaction sender against an arbitrary contract. However, the attack is fully permissionless (no privileged role required beyond deploying a contract), requires no leaked keys, and is deterministic once executed. The explicit TODO comment confirms the developers are aware of the gap.

---

### Recommendation

In `execute_replace_class`, before writing the new class hash to `contract_state_changes`, verify that the class hash exists in `contract_class_changes` by performing a lookup and asserting the result is non-zero:

```cairo
// Verify the new class hash has been declared.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
assert_not_zero(compiled_class_hash);
```

This mirrors the validation already enforced during `execute_declare_transaction` and is consistent with the OpenZeppelin pattern cited in the external report: confirm the target exists before trusting the operation succeeds. [4](#0-3) 

---

### Proof of Concept

**Setup:**
- Deploy `MaliciousVault` contract (class hash `A`, legitimately declared).
- `MaliciousVault` exposes a `deposit()` entry point that accepts user funds and a `freeze()` entry point that calls `replace_class(class_hash=0xdeadbeef)`.

**Steps:**
1. Alice calls `MaliciousVault.deposit()` and transfers 1000 STRK into the contract.
2. Bob calls `MaliciousVault.deposit()` and transfers 500 STRK.
3. Attacker (deployer) calls `MaliciousVault.freeze()`.
4. OS executes `execute_replace_class`: reads `class_hash = 0xdeadbeef` from the syscall request, skips any declared-class check, and writes `StateEntry(class_hash=0xdeadbeef, ...)` into `contract_state_changes`.
5. Block is finalized. The contract's on-chain class hash is now `0xdeadbeef`.
6. Alice calls `MaliciousVault.withdraw()`. The OS reads class hash `0xdeadbeef`, performs `dict_read` on `contract_class_changes` → returns `0`, then `find_element` fails to locate a `CompiledClassFact` with hash `0`. OS execution aborts. Alice's 1000 STRK is permanently frozen.
7. Bob's 500 STRK is equally unrecoverable. [5](#0-4) [6](#0-5)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L877-916)
```text
// Replaces the class.
func execute_replace_class{
    range_check_ptr,
    syscall_ptr: felt*,
    contract_state_changes: DictAccess*,
    revert_log: RevertLogEntry*,
}(contract_address: felt) {
    alloc_locals;
    let request = cast(syscall_ptr + RequestHeader.SIZE, ReplaceClassRequest*);

    // Reduce gas.
    let success = reduce_syscall_gas_and_write_response_header(
        total_gas_cost=REPLACE_CLASS_GAS_COST, request_struct_size=ReplaceClassRequest.SIZE
    );
    if (success == FALSE) {
        // Not enough gas to execute the syscall.
        return ();
    }

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L815-819)
```text
    // Note that prev_value=0 enforces that a class may be declared only once.
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L154-167)
```text
    let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
        key=execution_context.class_hash
    );

    // The key must be at offset 0.
    static_assert CompiledClassFact.hash == 0;
    let compiled_class_facts_bundle = block_context.os_global_context.compiled_class_facts_bundle;
    let (compiled_class_fact: CompiledClassFact*) = find_element(
        array_ptr=compiled_class_facts_bundle.compiled_class_facts,
        elm_size=CompiledClassFact.SIZE,
        n_elms=compiled_class_facts_bundle.n_compiled_class_facts,
        key=compiled_class_hash,
    );
    local compiled_class: CompiledClass* = compiled_class_fact.compiled_class;
```
