### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Permanent Freezing of Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` function in `syscall_impls.cairo` processes the `replace_class` syscall without verifying that the supplied class hash corresponds to a previously declared contract class. An unprivileged contract can call `replace_class` with an arbitrary, undeclared class hash. The OS accepts the state update unconditionally, permanently rendering the contract non-executable and freezing any funds it holds.

---

### Finding Description

The vulnerability class from the external report is a **missing validation check that should gate a state-transition but is absent**, allowing an invalid state to be committed. The analog here is identical in structure: the OS is supposed to verify that a `replace_class` target is a declared class before committing the new class hash to state, but that check is entirely absent.

In `execute_replace_class` (lines 877–916 of `syscall_impls.cairo`), the function reads the requested class hash from the syscall request and writes it directly into `contract_state_changes` with no validation:

```cairo
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

The explicit `TODO` comment at line 898 is a developer acknowledgment that the check is known to be missing. The `contract_class_changes` dict — which tracks declared classes — is not consulted at all inside `execute_replace_class`. [2](#0-1) 

By contrast, `execute_declare_transaction` correctly enforces that a class can only be declared once by requiring `prev_value=0` in the class-changes dict:

```cairo
assert_not_zero(compiled_class_hash);
dict_update{dict_ptr=contract_class_changes}(
    key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
);
``` [3](#0-2) 

No equivalent guard exists in `execute_replace_class`. The OS will accept any felt value as the new class hash and commit it to the contract state tree.

When a future transaction calls into the affected contract, the OS reads the class hash from state:

```cairo
let (state_entry: StateEntry*) = dict_read{dict_ptr=contract_state_changes}(
    key=contract_address
);
// ...
class_hash=state_entry.class_hash,
``` [4](#0-3) 

If that class hash is undeclared, the OS cannot resolve the class, and all entry-point dispatches to the contract fail permanently. There is no recovery path: `replace_class` can only be called by the contract itself, and the contract is now non-executable.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Any contract that holds ERC-20 token balances, ETH, or other assets and whose class hash is replaced with an undeclared value becomes permanently bricked. The storage (and therefore the funds) remains in the state tree but is unreachable by any transaction. Because the OS proof is generated from the Cairo execution, a block containing such a `replace_class` call produces a valid proof and is accepted on L1, making the freeze irreversible at the protocol level.

---

### Likelihood Explanation

Any deployed contract can issue the `replace_class` syscall — it requires no privileged role. An attacker can:

- Deploy a contract whose constructor or any callable entry point invokes `replace_class` with an arbitrary felt (e.g., `0xdead`).
- Trick a user into calling that entry point (e.g., via a malicious DeFi contract).
- The OS commits the invalid class hash to state without complaint.

The attack requires only a standard `invoke` transaction from an unprivileged account. The TODO comment confirms the developers are aware the check is absent, meaning the window of exploitability is open until the fix is shipped.

---

### Recommendation

Inside `execute_replace_class`, before writing the new class hash to `contract_state_changes`, perform a lookup in `contract_class_changes` to confirm the class hash has a non-zero compiled class hash (i.e., it has been declared). Concretely, add a `dict_read` on `contract_class_changes` keyed by `class_hash` and assert the result is non-zero. This mirrors the invariant already enforced by `execute_declare_transaction`.

---

### Proof of Concept

1. Deploy `MaliciousVault` — a contract that accepts ETH deposits and exposes a `drain_class` entry point.
2. Users deposit funds into `MaliciousVault`.
3. Attacker calls `drain_class`, which internally issues `replace_class(0xdeadbeef)` — a class hash that has never been declared on the network.
4. The OS executes `execute_replace_class`: gas is deducted, the state entry for `MaliciousVault` is updated to `class_hash = 0xdeadbeef`, no validation occurs.
5. The block is proven and accepted on L1. The state root now encodes `MaliciousVault.class_hash = 0xdeadbeef`.
6. Any subsequent `call_contract` or `invoke` targeting `MaliciousVault` reaches `execute_call_contract`, reads `class_hash = 0xdeadbeef` from state, and fails to resolve the class — the call reverts permanently.
7. All deposited funds are frozen with no recovery path.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L193-204)
```text
    let (state_entry: StateEntry*) = dict_read{dict_ptr=contract_state_changes}(
        key=contract_address
    );

    // Prepare execution context.
    // TODO(Yoni, 1/1/2026): change ExecutionContext to hold calldata_start, calldata_end.
    tempvar calldata_start = request.calldata_start;
    tempvar caller_execution_info = caller_execution_context.execution_info;
    tempvar caller_address = caller_execution_info.contract_address;
    tempvar execution_context: ExecutionContext* = new ExecutionContext(
        entry_point_type=ENTRY_POINT_TYPE_EXTERNAL,
        class_hash=state_entry.class_hash,
```

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L816-819)
```text
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```
