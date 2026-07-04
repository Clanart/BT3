### Title
Missing Declared Class Hash Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` function in the StarkNet OS Cairo program does not validate that the new class hash supplied to the `replace_class` syscall is actually declared in the system. Any contract can replace its own class with an arbitrary, undeclared class hash. Once committed to state, the contract becomes permanently uncallable, freezing all funds it holds.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function processes the `replace_class` syscall. After deducting gas, it reads the requested `class_hash` directly from the syscall request and immediately writes it into `contract_state_changes` with no check that the hash corresponds to a declared (compiled) class:

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

The developer-acknowledged TODO at line 898 explicitly states the missing guard. The same omission exists in the deprecated path: [2](#0-1) 

The OS validates compiled class facts only for classes **actually executed** during a block (`validate_compiled_class_facts_post_execution`). A class hash written into state via `replace_class` but never executed in the same block passes this post-execution check silently. [3](#0-2) 

The `replace_class` syscall is dispatched without any caller-privilege check — it is available to every Cairo 1 contract: [4](#0-3) 

**Analog to the report:** The external report describes a relayer that can modify critical system addresses (bond manager, dispute manager) without owner-level authorization. Here, the analogous flaw is that *any* contract — with no privileged role — can overwrite its own class pointer with an arbitrary value that the OS never validates against the set of declared classes. The "owner-level" constraint (only declared classes may be used) is simply absent.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once a block containing a `replace_class(undeclared_hash)` call is proven and committed:

1. The contract's `class_hash` field in state is permanently set to a hash that has no corresponding compiled class.
2. Every future call to that contract requires the sequencer to supply the compiled class for that hash in `compiled_class_facts_bundle`. Because the class was never declared, no such compiled class exists.
3. The contract is permanently uncallable. Any ERC-20 tokens, ETH, or other assets held in its storage are irrecoverably frozen.

This satisfies **Critical: Permanent freezing of funds** from the allowed impact scope.

---

### Likelihood Explanation

The attack requires only:
- Deploying a contract (permissionless on StarkNet).
- Calling `replace_class` with any felt value that is not a declared class hash.

No privileged role, leaked key, or operator cooperation is needed. The missing check is explicitly flagged in the source with a TODO dated 2026-01-01, confirming the gap is known and currently unmitigated. The syscall is reachable from any Cairo 1 contract execution context.

---

### Recommendation

Before writing the new class hash into `contract_state_changes`, verify that the hash exists in the `contract_class_changes` dictionary (i.e., it was declared in the current or a prior block). Concretely, perform a `dict_read` on `contract_class_changes` for `class_hash` and assert the result is non-zero before proceeding. Apply the same fix to the deprecated path in `deprecated_execute_syscalls.cairo`.

---

### Proof of Concept

1. Attacker deploys **Contract A** with a legitimate declared class hash. Users deposit funds into Contract A (e.g., it acts as a vault).
2. Attacker calls a function on Contract A that internally invokes `replace_class(0xdeadbeef)`, where `0xdeadbeef` is not declared anywhere.
3. The OS executes `execute_replace_class` in `syscall_impls.cairo`. The TODO check is absent; the function writes `class_hash = 0xdeadbeef` into `contract_state_changes` and records the revert log entry.
4. The block is proven. `validate_compiled_class_facts_post_execution` only checks classes *executed* in the block — `0xdeadbeef` was never executed, so no failure occurs. The proof is accepted.
5. In all subsequent blocks, any call to Contract A requires the sequencer to provide a compiled class for `0xdeadbeef`. No such class exists. The sequencer cannot construct a valid proof for any call to Contract A.
6. Contract A is permanently uncallable. All user funds are frozen.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/os.cairo (L114-120)
```text
    // Validate the guessed compile class facts.
    let compiled_class_facts_bundle = os_global_context.compiled_class_facts_bundle;
    validate_compiled_class_facts_post_execution(
        n_compiled_class_facts=compiled_class_facts_bundle.n_compiled_class_facts,
        compiled_class_facts=compiled_class_facts_bundle.compiled_class_facts,
        builtin_costs=compiled_class_facts_bundle.builtin_costs,
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_syscalls.cairo (L195-203)
```text
    if (selector == REPLACE_CLASS_SELECTOR) {
        execute_replace_class(contract_address=execution_context.execution_info.contract_address);
        %{ OsLoggerExitSyscall %}
        return execute_syscalls(
            block_context=block_context,
            execution_context=execution_context,
            syscall_ptr_end=syscall_ptr_end,
        );
    }
```
