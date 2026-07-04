### Title
Missing Class Hash Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS does not verify that the replacement class hash corresponds to a previously declared class. An unprivileged contract deployer can exploit this to permanently freeze funds held by any contract they control, by replacing the contract's class with an arbitrary undeclared hash, rendering the contract permanently uncallable.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function accepts the caller-supplied `class_hash` from the syscall request and writes it directly into the contract's state entry without any validation that the hash corresponds to a declared class:

```cairo
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
``` [1](#0-0) 

The TODO comment at line 898 explicitly acknowledges this missing check. The OS accepts any felt value as the new class hash with no membership proof against the set of declared classes.

When a subsequent transaction invokes the affected contract, the OS reads the now-invalid `class_hash` from state in `get_invoke_tx_execution_context`:

```cairo
let (state_entry: StateEntry*) = dict_read{dict_ptr=contract_state_changes}(
    key=contract_address
);
...
class_hash=state_entry.class_hash,
``` [2](#0-1) 

The OS then attempts to dispatch execution using this undeclared class hash. Because no compiled class exists for it, every future call to the contract fails at the class lookup stage. The contract's storage (including any token balances or locked assets) remains in state but is permanently inaccessible.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once `replace_class` is called with an undeclared hash, the contract's `class_hash` field in `contract_state_changes` is permanently set to an invalid value. All future invocations of the contract will fail because the OS cannot resolve the class. Any ERC-20 balances, ETH, or other assets stored in the contract's storage slots are permanently frozen with no recovery path, since the contract's own withdrawal logic is also unreachable.

---

### Likelihood Explanation

**Medium.**

The attack path is straightforward for a malicious contract deployer:

1. Deploy a contract containing a `replace_class` call reachable via a public entry point (e.g., disguised as an "upgrade" function).
2. Attract user deposits into the contract (e.g., present it as a vault, AMM, or staking contract).
3. Call the entry point that triggers `replace_class` with an arbitrary felt (e.g., `0x1`) as the new class hash.
4. The contract's class is permanently set to an undeclared hash; all funds are frozen.

No special privileges, leaked keys, or sequencer cooperation are required. Any unprivileged account that can deploy a contract and submit transactions can execute this attack.

---

### Recommendation

Before writing the new class hash into `contract_state_changes`, the OS must verify that the hash exists in the set of declared classes. Concretely, `execute_replace_class` should perform a lookup in `contract_class_changes` (or the equivalent declared-class registry) and assert that the entry is non-zero:

```cairo
// Assert the new class hash is declared.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
assert_not_zero(compiled_class_hash);
```

This mirrors the existing pattern used in `execute_declare_transaction`, which asserts `assert_not_zero(compiled_class_hash)` before writing to `contract_class_changes`. [3](#0-2) 

---

### Proof of Concept

1. Attacker deploys `MaliciousVault` contract containing:
   ```
   fn freeze_vault() {
       replace_class(class_hash: 0xdeadbeef);  // undeclared hash
   }
   ```
2. Users deposit 1000 STRK into `MaliciousVault`.
3. Attacker calls `freeze_vault()`.
4. The OS executes `execute_replace_class` with `class_hash = 0xdeadbeef`.
5. No validation occurs at line 898 of `syscall_impls.cairo`; the state entry is updated unconditionally.
6. `MaliciousVault`'s `class_hash` in state is now `0xdeadbeef`.
7. Any subsequent invoke transaction targeting `MaliciousVault` reads `class_hash = 0xdeadbeef` at line 473 of `transaction_impls.cairo`, fails to resolve a compiled class, and reverts.
8. The 1000 STRK in storage is permanently inaccessible. [4](#0-3)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L463-473)
```text
    let (state_entry: StateEntry*) = dict_read{dict_ptr=contract_state_changes}(
        key=contract_address
    );
    let (tx_info_ptr: TxInfo*) = alloc();
    let (deprecated_tx_info_ptr: DeprecatedTxInfo*) = alloc();
    local calldata_size;
    local calldata: felt*;
    %{ TxCalldata %}
    local tx_execution_context: ExecutionContext* = new ExecutionContext(
        entry_point_type=entry_point_type,
        class_hash=state_entry.class_hash,
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L816-819)
```text
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```
