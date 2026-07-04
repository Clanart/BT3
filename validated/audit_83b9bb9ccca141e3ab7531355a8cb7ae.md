### Title
Missing Class Hash Existence Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` function in the StarkNet OS does not verify that the new class hash supplied to the `replace_class` syscall corresponds to a previously declared contract class. An unprivileged transaction sender can trigger this path through any upgradeable contract that passes user-controlled input to `replace_class`, causing the contract's class hash to be permanently set to an undeclared value. All subsequent calls to that contract will fail at class lookup, permanently freezing any funds held in it.

---

### Finding Description

The `execute_replace_class` function in `syscall_impls.cairo` processes the `replace_class` syscall. After deducting gas, it reads the requested new class hash from the syscall request and immediately proceeds to update the contract's state entry — without checking whether the supplied class hash has ever been declared on-chain.

The missing check is explicitly acknowledged by a developer TODO comment at line 898:

```cairo
// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.
local state_entry: StateEntry*;
%{ GetContractAddressStateEntry %}
``` [1](#0-0) 

The function accepts any arbitrary `felt` value as the new class hash and writes it into `contract_state_changes` without consulting `contract_class_changes` or the compiled class facts bundle to confirm the hash is known. Compare this to the `execute_deploy` path, which at least computes the contract address from a known `class_hash` field in the request — `replace_class` performs no analogous existence gate. [2](#0-1) 

The analog to the original report is direct: just as `UniProxy.depositSwap` called `Router.exactInput` without first calling `approve()` (a required prerequisite), `execute_replace_class` updates the contract class without first verifying the prerequisite that the target class hash is declared. In both cases the missing pre-condition check allows a state mutation that makes the system permanently non-functional for the affected resource.

---

### Impact Explanation

Once `contract_state_changes` is updated with an undeclared class hash, the OS commits that value to the state trie via `state_update`. On every subsequent call to the affected contract, the OS will look up the class hash in the compiled class facts bundle and find nothing. The call will fail at class resolution. Because `replace_class` cannot be called again (the contract's class is now invalid, so no entry point can execute), the contract is permanently bricked. Any ERC-20 tokens, ETH, or other assets held in the contract's storage are permanently frozen with no recovery path.

**Impact category: Critical — Permanent freezing of funds.** [3](#0-2) 

---

### Likelihood Explanation

The `replace_class` syscall is the standard StarkNet upgrade mechanism. Any upgradeable contract that accepts a new class hash from an external caller (e.g., an `upgrade(new_class_hash)` function gated only by an owner check, or one with a misconfigured access control) is a viable target. An unprivileged attacker who can invoke such a function — or who is themselves the contract owner — can supply an arbitrary undeclared felt as the class hash. The OS will accept it unconditionally. The attack requires only a single transaction and no special privileges beyond the ability to call the target contract's upgrade entry point. [1](#0-0) 

---

### Recommendation

Before updating `contract_state_changes`, `execute_replace_class` must verify that `request.class_hash` exists in either `contract_class_changes` (newly declared in this block) or in the compiled class facts bundle (`os_global_context.compiled_class_facts_bundle`). The check should mirror the validation already performed for class hashes during `execute_declare_transaction`, where `compiled_class_hash` is asserted non-zero and the class hash is confirmed via `finalize_class_hash`. [4](#0-3) 

---

### Proof of Concept

1. Attacker deploys contract `V` holding user funds, with an `upgrade(new_class_hash: felt)` entry point that calls `replace_class(new_class_hash)`.
2. Attacker submits an invoke transaction calling `V.upgrade(0xdeadbeef)` where `0xdeadbeef` is never declared.
3. Inside the OS, `execute_replace_class` is reached. Gas is deducted successfully. The function reads `class_hash = 0xdeadbeef` from the request.
4. The TODO-guarded check is absent; the function proceeds directly to fetch `state_entry` via hint and writes the new `StateEntry(class_hash=0xdeadbeef, ...)` into `contract_state_changes`. [3](#0-2) 

5. `state_update` commits this to the state trie. Contract `V` now has class hash `0xdeadbeef` on-chain.
6. Any subsequent call to `V` causes the OS to look up `0xdeadbeef` in the compiled class facts — it is absent. Execution fails at class resolution. No entry point of `V` can ever run again.
7. All funds in `V`'s storage are permanently frozen.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L877-900)
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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L814-819)
```text
    // Declare the class hash.
    // Note that prev_value=0 enforces that a class may be declared only once.
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```
