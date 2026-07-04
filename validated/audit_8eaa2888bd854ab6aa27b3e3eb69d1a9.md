### Title
Missing Declared Class Hash Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS program does not verify that the new class hash supplied by a contract is actually a declared class. This is an explicit acknowledged gap (marked with a `TODO` comment). Any contract can replace its own class hash with an arbitrary undeclared felt value, rendering the contract permanently inaccessible and freezing all funds it holds.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function processes the `replace_class` syscall. After deducting gas, it reads the requested new class hash directly from the syscall request and writes it into `contract_state_changes` without any check that the hash corresponds to a class that has been declared on-chain:

```cairo
// execute_replace_class (syscall_impls.cairo ~line 878-916)
func execute_replace_class{...}(contract_address: felt) {
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
```

The `TODO` comment at line 898 explicitly acknowledges the missing validation. The OS accepts any arbitrary felt as the new class hash and commits it to the state diff. Once committed, any future transaction that calls this contract will require the prover/sequencer to supply a compiled class fact for the new hash. Since no such class exists, the contract becomes permanently unexecutable.

This is the direct analog of the external report's pattern: just as `_authorizeUpgrade()` with no access control lets anyone swap in a malicious implementation that can `selfdestruct` the proxy, `execute_replace_class` with no declared-class validation lets any contract swap in an invalid class hash that permanently bricks itself.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once a contract's class hash is updated to an undeclared value and the state diff is committed to the chain:

- The sequencer cannot produce a valid execution trace for any future call to that contract (no compiled class fact exists for the hash).
- The prover cannot generate a valid proof for such a block.
- The contract is permanently inaccessible on-chain.
- All ERC-20 balances, LP positions, or any other assets stored in or controlled by that contract are permanently frozen with no recovery path.

---

### Likelihood Explanation

**High.** The `replace_class` syscall is a standard, publicly documented StarkNet syscall callable by any contract from within its own execution. No privileged role is required. An attacker can:

1. Deploy a contract that appears to be a legitimate protocol (token vault, DEX, lending pool).
2. Attract user deposits.
3. At any chosen moment, invoke an internal function that issues `replace_class(0xdeadbeef)` (any undeclared felt).
4. The OS commits the invalid class hash to state.
5. The contract is permanently bricked; all deposited funds are frozen.

The attack requires only a standard `INVOKE_FUNCTION` transaction from the attacker's own account — no privileged access, no leaked keys, no operator cooperation.

---

### Recommendation

Before writing the new class hash into `contract_state_changes`, the OS must verify that the hash corresponds to a class that has been declared in `contract_class_changes` (or in the pre-existing state). Concretely, `execute_replace_class` should perform a lookup in `contract_class_changes` to confirm `class_hash` maps to a non-zero compiled class hash, and revert with an error if it does not. The existing `TODO` comment already identifies this exact fix.

---

### Proof of Concept

**Step 1 — Deploy malicious vault contract (simplified pseudocode):**
```cairo
#[starknet::contract]
mod MaliciousVault {
    // Accepts ETH/STRK deposits from users.
    fn deposit(amount: u256) { ... }

    // Hidden kill-switch: replaces class with undeclared hash.
    fn freeze_all() {
        starknet::replace_class_syscall(0xdeadbeef_undeclared_hash).unwrap();
    }
}
```

**Step 2 — Attacker calls `freeze_all()`:**
- Issues an `INVOKE_FUNCTION` transaction targeting `MaliciousVault::freeze_all`.
- The OS executes `execute_replace_class` with `class_hash = 0xdeadbeef`.
- No declared-class check is performed (the `TODO` gap).
- `contract_state_changes` is updated: `MaliciousVault.class_hash ← 0xdeadbeef`.

**Step 3 — State is committed:**
- The block is proven and accepted on L1.
- `MaliciousVault`'s class hash is now `0xdeadbeef` in the canonical state.

**Step 4 — Contract is permanently inaccessible:**
- Any future call to `MaliciousVault` requires the sequencer to supply a compiled class fact for `0xdeadbeef`.
- No such fact exists; the sequencer cannot include such transactions.
- All user deposits are permanently frozen.

**Relevant code location:** [1](#0-0) 

The `TODO` acknowledgment of the missing check: [2](#0-1)

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
