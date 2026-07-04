### Title
Missing Class Hash Validation in `execute_replace_class` Allows Permanent Contract Bricking and Fund Freezing - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS does not verify that the caller-supplied class hash corresponds to a previously declared contract class. Any contract can replace its own class with an arbitrary, undeclared felt value. The OS will commit this invalid class hash to state, permanently bricking the contract and freezing any funds it holds. The codebase itself acknowledges this gap with an explicit TODO comment at the exact location of the missing check.

---

### Finding Description

In `execute_replace_class` (`syscall_impls.cairo`, lines 877–916), after deducting gas, the function reads `class_hash` directly from the caller-supplied request and writes it unconditionally into `contract_state_changes`:

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

There is no lookup into `contract_class_changes` to confirm that `class_hash` was ever declared. The analog to the reported "token whitelisting" vulnerability is exact: just as `depositToken` accepted any ERC20 address without a whitelist check, `execute_replace_class` accepts any felt as a class hash without a declared-class check.

Compare this with `execute_declare_transaction` in `transaction_impls.cairo` (lines 816–818), which enforces `prev_value=0` to guarantee a class is declared at most once, and with `deploy_contract` in `deploy_contract.cairo` (lines 44–53), which validates the target address against a set of reserved addresses. No equivalent guard exists for `replace_class`.

---

### Impact Explanation

**Impact: Critical — Permanent freezing of funds.**

Once `contract_state_changes` is committed with an undeclared class hash for a contract address, every future call to that contract will fail at class resolution time because no bytecode exists for the hash. The contract's storage — including any token balances, locked collateral, or user deposits — becomes permanently inaccessible. There is no recovery path: the OS has no mechanism to revert a committed state root, and the class hash cannot be corrected without a valid `replace_class` call, which itself requires the contract to be callable.

---

### Likelihood Explanation

The `replace_class` syscall is callable by any contract on itself. Realistic attack paths include:

1. **Malicious rug-pull contract**: An attacker deploys a contract that accepts user deposits and exposes a privileged `replace_class` entry point. After accumulating funds, the attacker calls `replace_class` with an arbitrary undeclared hash (e.g., `0x1`), permanently bricking the contract and freezing all deposited funds.

2. **Vulnerable upgradeable contract**: A legitimate upgradeable contract that passes an attacker-controlled value to `replace_class` (e.g., via a governance exploit or input validation bug at the contract level) can be bricked because the OS provides no backstop validation.

The entry path requires no privileged role — any unprivileged transaction sender can deploy a contract and call `replace_class` on it.

---

### Recommendation

Before committing the new class hash to `contract_state_changes`, verify that `class_hash` exists as a key in `contract_class_changes` with a non-zero value. This is the direct analog of the recommended token whitelist: only class hashes that have been legitimately declared (i.e., appear in `contract_class_changes`) should be accepted. The codebase already tracks this mapping; the check simply needs to be added where the TODO comment currently sits.

---

### Proof of Concept

1. Attacker deploys `MaliciousVault` — a contract that accepts ERC-20 deposits and exposes an `owner_upgrade(new_hash: felt)` entry point that calls `replace_class(new_hash)`.
2. Users deposit funds; `MaliciousVault`'s storage now holds balances.
3. Attacker calls `owner_upgrade(0xdeadbeef)`. The OS executes `execute_replace_class`:
   - Gas is deducted successfully.
   - `class_hash = 0xdeadbeef` is read from the request.
   - **No check against `contract_class_changes` is performed** (the TODO is unimplemented).
   - `contract_state_changes` is updated: `MaliciousVault`'s class hash is now `0xdeadbeef`.
4. The block is proven and the state root is committed on-chain with `MaliciousVault.class_hash = 0xdeadbeef`.
5. Any subsequent call to `MaliciousVault` (e.g., `withdraw`) fails at class resolution — no bytecode exists for `0xdeadbeef`.
6. All user funds in `MaliciousVault`'s storage are permanently frozen.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L877-910)
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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deploy_contract.cairo (L44-53)
```text
    // Assert that we don't deploy to one of the reserved addresses.
    assert_not_zero(
        (contract_address - ORIGIN_ADDRESS) * (contract_address - BLOCK_HASH_CONTRACT_ADDRESS) * (
            contract_address - ALIAS_CONTRACT_ADDRESS
        ) * (contract_address - RESERVED_CONTRACT_ADDRESS),
    );

    local state_entry: StateEntry*;
    %{ GetContractAddressStateEntry %}
    assert state_entry.class_hash = UNINITIALIZED_CLASS_HASH;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L815-819)
```text
    // Note that prev_value=0 enforces that a class may be declared only once.
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```
