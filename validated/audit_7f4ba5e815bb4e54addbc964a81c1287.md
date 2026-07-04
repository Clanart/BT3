### Title
Missing Class Hash Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds — (File: `execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS does not verify that the replacement class hash corresponds to a previously declared class. This allows any contract to replace its own class hash with an arbitrary undeclared value, permanently bricking the contract and freezing any funds it holds. The root cause is structurally identical to the external report: a mutable state reference (the contract's class hash) is updated without validating consistency with the downstream lookup (`contract_class_changes`), causing that lookup to silently return an incorrect zero value and breaking all subsequent execution against the contract.

---

### Finding Description

**Vulnerable function:** `execute_replace_class` in `syscall_impls.cairo` (lines 878–916).

The function updates `contract_state_changes[contract_address].class_hash` to the caller-supplied value without checking whether that value exists as a key in `contract_class_changes` (the Sierra-class-hash → compiled-class-hash mapping). The developers themselves flagged this gap:

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

The downstream consumer of this state is `execute_entry_point`, which reads the compiled class hash from `contract_class_changes` using the (now-corrupted) class hash stored in `contract_state_changes`:

```cairo
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=execution_context.class_hash
);
...
let (compiled_class_fact: CompiledClassFact*) = find_element(
    array_ptr=compiled_class_facts_bundle.compiled_class_facts,
    elm_size=CompiledClassFact.SIZE,
    n_elms=compiled_class_facts_bundle.n_compiled_class_facts,
    key=compiled_class_hash,
);
``` [2](#0-1) 

**Attack flow (direct analog to the external report):**

| Step | External Report (Solidity) | StarkNet OS (Cairo) |
|---|---|---|
| 1 | Register vault `V1` in original `vaultManager` | Deploy contract `C` with funds; class hash `H_old` is declared and compiled |
| 2 | Replace `vaultManager` with `vaultManagerV2` | Contract `C` calls `replace_class(H_invalid)` where `H_invalid` is not in `contract_class_changes` |
| 3 | `enabledTime` resets on re-registration | `contract_state_changes[C].class_hash` is now `H_invalid`; block finalizes with this state |
| 4 | Query `getOperatorStake()` for old epoch | Subsequent block: caller invokes contract `C` |
| 5 | `_wasActiveAt()` returns `false`, stake excluded | `dict_read(contract_class_changes, H_invalid)` returns `0` (default); `find_element(..., key=0)` fails; OS execution aborts |

Because `dict_new()` initializes with a default value of `0`, any undeclared class hash silently resolves to compiled-class-hash `0`. The `find_element` call then cannot locate a compiled class with hash `0` in `compiled_class_facts_bundle`, causing the OS to abort. The sequencer cannot include any future call to contract `C`, permanently locking its funds. [3](#0-2) 

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Any contract that holds assets (tokens in storage, ETH, STRK) and calls `replace_class` with an undeclared class hash becomes permanently unexecutable. Because the OS aborts when it cannot resolve the compiled class, the sequencer is forced to exclude all future calls to that contract. There is no recovery path: the state root commits the corrupted class hash, and no subsequent transaction can repair it (the contract cannot be called to fix itself). Funds are irrecoverably frozen.

---

### Likelihood Explanation

**Medium.**

The trigger is reachable by any unprivileged contract deployer or caller:
- No privileged role is required.
- Any contract can invoke `replace_class` on itself via the standard syscall interface.
- A malicious contract can deliberately pass an undeclared felt as the class hash.
- A buggy contract (e.g., one that reads the class hash from user-controlled calldata) can be exploited by an external attacker to trigger this path.

The only prerequisite is that the contract holds funds worth targeting, which is a common condition for vaults, escrows, and DeFi contracts.

---

### Recommendation

Inside `execute_replace_class`, before writing to `contract_state_changes`, verify that the requested class hash exists in `contract_class_changes` (i.e., that it was previously declared). A `dict_read` on `contract_class_changes` with the new class hash should return a non-zero compiled class hash; if it returns `0`, the syscall must fail with an appropriate error response. This directly resolves the acknowledged TODO and closes the gap between the mutable state update and the downstream lookup invariant.

---

### Proof of Concept

```cairo
// Pseudocode illustrating the attack

// Step 1: Deploy a vault contract holding 1000 STRK.
// The vault's class hash H_old is declared and compiled.

// Step 2: Attacker (or buggy vault logic) calls replace_class with an undeclared hash.
// Inside the vault contract:
replace_class(0xdeadbeef_undeclared);
// execute_replace_class writes:
//   contract_state_changes[vault_address].class_hash = 0xdeadbeef_undeclared
// No validation against contract_class_changes occurs.
// Transaction succeeds; block is finalized.

// Step 3: In the next block, anyone tries to call the vault:
call_contract(vault_address, withdraw_selector, ...);
// execute_entry_point reads:
//   class_hash = contract_state_changes[vault_address].class_hash = 0xdeadbeef_undeclared
//   compiled_class_hash = contract_class_changes[0xdeadbeef_undeclared] = 0  (default)
// find_element(compiled_class_facts_bundle, key=0) → ABORT
// OS execution fails; block cannot be proven.
// Sequencer excludes the call; vault is permanently unexecutable.
// 1000 STRK are permanently frozen.
``` [4](#0-3) [5](#0-4)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L878-916)
```text
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L142-170)
```text
func execute_entry_point{
    range_check_ptr,
    remaining_gas: felt,
    builtin_ptrs: BuiltinPointers*,
    contract_state_changes: DictAccess*,
    contract_class_changes: DictAccess*,
    revert_log: RevertLogEntry*,
    outputs: OsCarriedOutputs*,
}(block_context: BlockContext*, execution_context: ExecutionContext*) -> (
    is_reverted: felt, retdata_size: felt, retdata: felt*
) {
    alloc_locals;
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
    let (success, compiled_class_entry_point: CompiledClassEntryPoint*) = get_entry_point(
        compiled_class=compiled_class, execution_context=execution_context
    );
```
