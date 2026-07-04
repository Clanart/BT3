### Title
Missing Declared-Class Validation in `execute_replace_class` Enables Permanent Freezing of Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS does not validate that the replacement class hash corresponds to a previously declared contract class. A malicious contract deployer can exploit this to replace a contract's class with an arbitrary undeclared hash, permanently rendering the contract un-executable and freezing any funds held within it. The OS itself acknowledges this gap with a TODO comment at the exact vulnerable line.

---

### Finding Description

In `execute_replace_class`, the OS reads the requested new class hash from the syscall request and immediately writes it into the contract's `StateEntry` without checking whether that hash exists in the declared-class set (`contract_class_changes`):

```cairo
func execute_replace_class{
    range_check_ptr,
    syscall_ptr: felt*,
    contract_state_changes: DictAccess*,
    revert_log: RevertLogEntry*,
}(contract_address: felt) {
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
    ...
}
``` [1](#0-0) 

The `contract_class_changes` dictionary (which tracks declared classes) is not an implicit argument of `execute_replace_class` and is never consulted. The OS therefore accepts any arbitrary felt as a valid class hash.

Compare this with `execute_declare_transaction`, which correctly enforces `prev_value=0` to ensure a class is declared exactly once:

```cairo
dict_update{dict_ptr=contract_class_changes}(
    key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
);
``` [2](#0-1) 

No analogous membership check exists in `execute_replace_class`.

---

### Impact Explanation

Once a contract's `class_hash` field in `contract_state_changes` is set to an undeclared hash, the OS can never generate a valid proof for any future transaction that invokes that contract. The prover hint that loads compiled class facts (`guess_compiled_class_facts`) will have no entry for the undeclared hash, making proof generation impossible. The contract is permanently un-callable. [3](#0-2) 

Any ERC-20 tokens, ETH, or other assets held in the contract's storage are permanently frozen. This satisfies the **Critical: Permanent freezing of funds** impact criterion.

---

### Likelihood Explanation

The attack path is fully reachable by an unprivileged user:

1. **Attacker declares a class** — standard `declare` transaction, no privilege required.
2. **Attacker deploys a contract** — the contract's logic includes a `replace_class` call that can be triggered by the deployer (e.g., via an owner-only function or a time-locked trigger).
3. **Victims deposit funds** — the contract presents a legitimate interface (vault, escrow, token bridge).
4. **Attacker triggers `replace_class`** — passes any felt that is not a declared class hash (e.g., `1`).
5. **Contract becomes permanently un-executable** — all deposited funds are frozen.

The `replace_class` syscall is dispatched directly from `execute_syscalls` with no additional access control at the OS level: [4](#0-3) 

The only gate is that the contract itself must issue the syscall — which the attacker controls by writing the contract's code.

---

### Recommendation

Add a membership check inside `execute_replace_class` that verifies the requested `class_hash` exists in `contract_class_changes` (i.e., has a non-zero compiled class hash entry). The function signature must be extended to receive `contract_class_changes` as an implicit argument, mirroring how `execute_declare_transaction` uses it:

```cairo
func execute_replace_class{
    range_check_ptr,
    syscall_ptr: felt*,
    contract_state_changes: DictAccess*,
    contract_class_changes: DictAccess*,   // <-- add this
    revert_log: RevertLogEntry*,
}(contract_address: felt) {
    ...
    let class_hash = request.class_hash;

    // Verify the class has been declared.
    let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
    with_attr error_message("Class hash is not declared.") {
        assert_not_zero(compiled_class_hash);
    }
    ...
}
```

---

### Proof of Concept

**Analogy mapping to the external report:**

| External Report (Beedle) | StarkNet OS Analog |
|---|---|
| Lender calls `giveLoan()` to reset auction, then `buyLoan()` to assign max-interest pool | Contract deployer calls `replace_class` with undeclared hash after attracting depositors |
| Missing check: lender cannot set same pool as loan | Missing check: new class hash must be declared |
| Impact: borrower pays 1000% interest, loses collateral | Impact: depositors' funds permanently frozen |
| Root cause: `giveLoan()` does not validate pool ≠ current loan pool | Root cause: `execute_replace_class` does not validate class hash ∈ declared classes |

**Concrete attack sequence:**

1. Attacker declares class `C` (legitimate-looking vault contract with a hidden `replace_class` backdoor).
2. Attacker deploys contract at address `A` using class `C`.
3. Users deposit 1,000,000 STRK into contract `A`.
4. Attacker sends an invoke transaction calling the backdoor function, which internally executes:
   ```
   replace_class(class_hash=0xdeadbeef)  // 0xdeadbeef is not declared
   ```
5. The OS writes `StateEntry(class_hash=0xdeadbeef, ...)` for address `A` into `contract_state_changes`.
6. State root is updated with the undeclared class hash committed on-chain.
7. All future transactions targeting `A` fail at proof generation — no compiled class exists for `0xdeadbeef`.
8. The 1,000,000 STRK are permanently frozen. [5](#0-4)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L816-819)
```text
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
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
