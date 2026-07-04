### Title
Missing Declared Class Validation in `execute_replace_class` Allows Permanent Contract Bricking — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS does not verify that the new class hash supplied by a contract corresponds to a previously declared class before committing the state update. This is directly analogous to the reported one-step ownership transfer flaw: just as ownership can be irrevocably transferred to an uncontrolled address, a contract's class can be irrevocably replaced with an arbitrary, undeclared hash — permanently bricking the contract and freezing any funds it holds.

---

### Finding Description

In `execute_replace_class`, the OS reads `request.class_hash` from the syscall segment and immediately writes it into `contract_state_changes` with no check that the hash exists in `contract_class_changes` (the declared-class registry):

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

The TODO comment at line 898 is an explicit, in-code acknowledgement that this validation is absent. [1](#0-0) 

The OS is the component that is ZK-proven and whose output is accepted by the L1 verifier. Because the OS does not enforce that the replacement class hash is declared, a valid proof can be generated for a state transition in which a contract's class is set to any arbitrary felt — including one with no corresponding compiled class. Once that proof is submitted to L1, the state is final and irreversible.

For comparison, `execute_declare_transaction` enforces that a declared class hash is the result of a proper Sierra class hash calculation before writing to `contract_class_changes`: [2](#0-1) 

No equivalent guard exists in `execute_replace_class`.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

After `replace_class` is called with an undeclared hash:

1. The contract's `class_hash` field in `contract_state_changes` is set to the arbitrary value.
2. Every subsequent call to that contract address will attempt to look up the compiled class for that hash. No compiled class exists, so execution fails unconditionally.
3. All ERC-20 balances, NFTs, or other assets stored in the contract's storage become permanently inaccessible — there is no entry point that can be called to move them.
4. Because the OS produces a valid proof for this state transition, the L1 verifier accepts it, making the state change final on-chain.

---

### Likelihood Explanation

**Medium.**

The `replace_class` syscall is a standard, documented StarkNet syscall callable by any contract. Realistic paths to exploitation include:

- **Buggy upgrade logic**: A contract with a flawed `upgrade` function that does not validate the new class hash before calling `replace_class` can be triggered by any user who can invoke that function.
- **Malicious contract**: An attacker deploys a contract that intentionally calls `replace_class(0xdeadbeef)` to freeze funds deposited by victims (e.g., a fake vault or pool).
- **Reentrancy / callback abuse**: A contract that calls `replace_class` inside a callback or hook that an external party can trigger.

No privileged role, leaked key, or operator cooperation is required. Any unprivileged transaction sender who can invoke a contract's entry point is a potential trigger.

---

### Recommendation

Inside `execute_replace_class`, before writing the new class hash to `contract_state_changes`, add a lookup into `contract_class_changes` (or the equivalent squashed class dict) to assert that `request.class_hash` maps to a non-zero compiled class hash. This mirrors the existing guard in `deploy_contract`, which asserts `state_entry.class_hash = UNINITIALIZED_CLASS_HASH` before writing a new entry, and the guard in `execute_declare_transaction`, which verifies the Sierra class hash pre-image before registering a class. [3](#0-2) 

---

### Proof of Concept

1. **Deploy a vault contract** that accepts ERC-20 deposits and exposes an `upgrade(new_class_hash)` entry point that calls `replace_class(new_class_hash)` without validating the argument.
2. **Deposit funds** into the vault (e.g., 1 000 000 STRK).
3. **Send an invoke transaction** calling `upgrade(0x1)` — an arbitrary felt that is not a declared class hash.
4. The OS executes `execute_replace_class`: it reads `class_hash = 0x1`, skips the missing declared-class check, and writes `StateEntry(class_hash=0x1, ...)` into `contract_state_changes`. [4](#0-3) 
5. The OS produces a valid proof; the L1 verifier accepts the new state root.
6. All subsequent calls to the vault address fail — no compiled class for hash `0x1` exists. The 1 000 000 STRK are permanently frozen.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L896-915)
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

    return ();
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L738-743)
```text
        let expected_class_hash = finalize_class_hash(
            contract_class_component_hashes=contract_class_component_hashes
        );
        with_attr error_message("Invalid class hash pre-image.") {
            assert [class_hash_ptr] = expected_class_hash;
        }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deploy_contract.cairo (L51-54)
```text
    local state_entry: StateEntry*;
    %{ GetContractAddressStateEntry %}
    assert state_entry.class_hash = UNINITIALIZED_CLASS_HASH;
    assert state_entry.nonce = 0;
```
