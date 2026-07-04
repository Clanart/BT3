### Title
Missing Validation of `class_hash` in `execute_replace_class` Allows Permanent Contract Bricking — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS program accepts an arbitrary attacker-controlled `class_hash` and writes it directly to the contract state without verifying that the hash corresponds to a previously declared contract class. This is the exact same vulnerability class as the NEAR report: missing input validation before a critical state-transition. An unprivileged contract deployer can permanently brick any contract (including one holding funds) by replacing its class with an undeclared hash, making the contract permanently unexecutable and freezing all funds it holds.

---

### Finding Description

In `syscall_impls.cairo`, `execute_replace_class` reads `class_hash` directly from the syscall request and writes it to `contract_state_changes` with no check that the hash is a declared class:

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
```

The `// TODO` comment at line 898 explicitly acknowledges the missing check. The same flaw exists in the deprecated path in `deprecated_execute_syscalls.cairo` at lines 307–329, where `execute_replace_class` also performs no validation of `class_hash`.

After the state is updated with an undeclared `class_hash`, any subsequent call to that contract will cause the OS prover to fail: it cannot locate a compiled class for the hash, making the block unprovable or the contract permanently unexecutable.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once a contract's class hash is replaced with an undeclared value:
- The contract's `class_hash` field in `contract_state_changes` is committed to the state trie.
- Any future transaction calling that contract will cause the OS to attempt to look up the compiled class for the undeclared hash. Since no such class exists in the compiled class facts, the OS proof cannot be generated for any block containing such a call.
- All funds (ETH, ERC20 tokens, or any assets) held by the bricked contract are permanently inaccessible — there is no recovery path because the OS itself cannot execute the contract.

---

### Likelihood Explanation

**High.**

- Any unprivileged contract deployer can deploy a contract that calls `replace_class` with `class_hash = 0` or any arbitrary felt value not in the declared class set.
- The OS enforces no constraint on the value — the TODO comment confirms this is a known, unimplemented check.
- The attack requires only a single transaction from an unprivileged sender.
- The deprecated syscall path (`deprecated_execute_syscalls.cairo`) is equally vulnerable, widening the attack surface to legacy contracts.

---

### Recommendation

Before updating `contract_state_changes` in `execute_replace_class`, assert that the provided `class_hash` exists in `contract_class_changes` (i.e., it was previously declared). Concretely, perform a `dict_read` on `contract_class_changes` keyed by `class_hash` and assert the returned compiled class hash is non-zero:

```cairo
// Verify the class hash is declared.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
assert_not_zero(compiled_class_hash);
```

This mirrors the protection already present in `execute_declare_transaction` where `assert_not_zero(compiled_class_hash)` is enforced before writing to `contract_class_changes`.

---

### Proof of Concept

1. Attacker deploys contract `C` that holds user funds and whose `__execute__` calls `replace_class(0x1337_undeclared)`.
2. Attacker (or any user) sends an invoke transaction to `C`.
3. The OS processes the `REPLACE_CLASS` syscall via `execute_replace_class` in `syscall_impls.cairo` (line 896–915). No validation is performed; `contract_state_changes` is updated with `class_hash = 0x1337_undeclared`.
4. The block is sequenced and committed. The state trie now records `C.class_hash = 0x1337_undeclared`.
5. Any subsequent transaction calling `C` causes the OS to invoke `get_entry_point` (in `execute_entry_point.cairo` line 91–120) for `class_hash = 0x1337_undeclared`. The compiled class does not exist; the prover cannot produce a valid proof.
6. All funds in `C` are permanently frozen.

**Root cause lines:** [1](#0-0) [2](#0-1) 

**Deprecated path (same flaw):** [3](#0-2) 

**Entry point lookup that fails for undeclared hash:** [4](#0-3)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L896-898)
```text
    let class_hash = request.class_hash;

    // TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L902-910)
```text
    tempvar new_state_entry = new StateEntry(
        class_hash=class_hash, storage_ptr=state_entry.storage_ptr, nonce=state_entry.nonce
    );

    dict_update{dict_ptr=contract_state_changes}(
        key=contract_address,
        prev_value=cast(state_entry, felt),
        new_value=cast(new_state_entry, felt),
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deprecated_execute_syscalls.cairo (L307-329)
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
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L91-116)
```text
func get_entry_point{range_check_ptr}(
    compiled_class: CompiledClass*, execution_context: ExecutionContext*
) -> (success: felt, entry_point: CompiledClassEntryPoint*) {
    alloc_locals;
    // Get the entry points corresponding to the transaction's type.
    local entry_points: CompiledClassEntryPoint*;
    local n_entry_points: felt;

    tempvar entry_point_type = execution_context.entry_point_type;
    if (entry_point_type == ENTRY_POINT_TYPE_L1_HANDLER) {
        entry_points = compiled_class.l1_handlers;
        n_entry_points = compiled_class.n_l1_handlers;
    } else {
        if (entry_point_type == ENTRY_POINT_TYPE_EXTERNAL) {
            entry_points = compiled_class.external_functions;
            n_entry_points = compiled_class.n_external_functions;
        } else {
            assert entry_point_type = ENTRY_POINT_TYPE_CONSTRUCTOR;
            entry_points = compiled_class.constructors;
            n_entry_points = compiled_class.n_constructors;

            if (n_entry_points == 0) {
                return (success=1, entry_point=cast(0, CompiledClassEntryPoint*));
            }
        }
    }
```
