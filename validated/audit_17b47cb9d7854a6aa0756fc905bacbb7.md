### Title
Pre-Occupation of Deterministic Contract Address Blocks `deploy_account` and Causes Permanent Fund Loss - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deploy_contract.cairo`)

### Summary

`deploy_contract` unconditionally asserts that the target address is uninitialized (`class_hash == 0`, `nonce == 0`). Because `deploy_account` transactions derive their address using `deployer_address=0`, and the `deploy` syscall with `deploy_from_zero=TRUE` uses the same derivation, an unprivileged attacker can pre-occupy any future account address by deploying a malicious contract there first. If the victim has already funded the address (the standard StarkNet onboarding flow), those funds are permanently lost to the attacker's contract.

### Finding Description

`deploy_contract` enforces strict uninitialized-address semantics:

```cairo
local state_entry: StateEntry*;
%{ GetContractAddressStateEntry %}
assert state_entry.class_hash = UNINITIALIZED_CLASS_HASH;
assert state_entry.nonce = 0;
``` [1](#0-0) 

`execute_deploy_account_transaction` computes the account address with `deployer_address=0`:

```cairo
let (contract_address) = get_contract_address(
    salt=contract_address_salt,
    class_hash=class_hash,
    constructor_calldata_size=constructor_calldata_size,
    constructor_calldata=constructor_calldata,
    deployer_address=0,
);
``` [2](#0-1) 

The `execute_deploy` syscall with `deploy_from_zero=TRUE` also resolves to `deployer_address=0`:

```cairo
let deployer_address = (1 - deploy_from_zero) * caller_address;
// ...
let (contract_address) = get_contract_address(
    salt=request.contract_address_salt,
    class_hash=request.class_hash,
    constructor_calldata_size=constructor_calldata_size,
    constructor_calldata=constructor_calldata_start,
    deployer_address=deployer_address,   // == 0 when deploy_from_zero=TRUE
);
``` [3](#0-2) 

Both paths feed into the same `get_contract_address` function: [4](#0-3) 

Because `deployer_address=0` is used in both cases, the address space for `deploy_account` transactions and `deploy(deploy_from_zero=TRUE)` syscalls is **identical**. Any address a user intends to use as their account can be pre-occupied by an attacker who deploys a malicious contract there first using the `deploy` syscall.

### Impact Explanation

**Critical — Direct loss of funds.**

The standard StarkNet account onboarding flow is:
1. User computes their account address from `(salt, class_hash, constructor_calldata)`.
2. User sends ETH/STRK to that address to fund the deployment fee.
3. User submits a `deploy_account` transaction.

Between steps 2 and 3, an attacker who observes the pending `deploy_account` transaction (or predicts the address from a known salt) can submit a `deploy(deploy_from_zero=TRUE, salt=S, class_hash=C, calldata=D)` call from a malicious factory contract. If the attacker's transaction is included first, the address is now occupied by the attacker's malicious contract. The victim's `deploy_account` transaction then fails permanently because `assert state_entry.class_hash = UNINITIALIZED_CLASS_HASH` is violated. The funds already sent to that address are now controlled by the attacker's contract and can be drained.

### Likelihood Explanation

- The attack requires observing the victim's pending `deploy_account` parameters from the public mempool — no privileged access needed.
- The attacker only needs a deployed factory contract that calls `deploy` with `deploy_from_zero=TRUE`.
- In a decentralized sequencer environment (which StarkNet is actively moving toward), the attacker can pay higher fees to ensure their transaction is ordered before the victim's.
- The victim cannot recover: the address is permanently occupied, and the `deploy_contract` assertion has no idempotency path.

### Recommendation

Introduce idempotency semantics analogous to `init_if_needed`. In `deploy_contract`, if the address is already initialized with the **exact same** `class_hash` as the requested deployment, treat it as a no-op rather than a hard assertion failure. Alternatively, include a transaction-unique nonce or the sender's address in the address derivation for `deploy_account` transactions so that the address space cannot be pre-occupied via the `deploy` syscall.

### Proof of Concept

1. Victim broadcasts `deploy_account` with `salt=S`, `class_hash=C`, `calldata=D`. Address `A = hash(0, S, C, D)` is publicly derivable.
2. Victim sends 1 ETH to address `A` to fund deployment.
3. Attacker deploys a malicious factory contract `F` that exposes a function `grief(S, C, D)` which calls `deploy(class_hash=C, salt=S, calldata=D, deploy_from_zero=TRUE)`.
4. Attacker calls `F.grief(S, C, D)` with a higher fee, getting it included before the victim's `deploy_account`.
5. OS executes `deploy_contract` for the attacker's call: `state_entry.class_hash == 0` → passes. Address `A` is now occupied by the attacker's malicious contract.
6. OS attempts to execute victim's `deploy_account`: `assert state_entry.class_hash = UNINITIALIZED_CLASS_HASH` → **fails** (class_hash is now `C`). Victim's transaction is rejected.
7. Attacker's malicious contract at `A` calls `transfer` to drain the 1 ETH the victim deposited. Funds are permanently lost.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deploy_contract.cairo (L51-54)
```text
    local state_entry: StateEntry*;
    %{ GetContractAddressStateEntry %}
    assert state_entry.class_hash = UNINITIALIZED_CLASS_HASH;
    assert state_entry.nonce = 0;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L538-545)
```text
        let (contract_address) = get_contract_address(
            salt=contract_address_salt,
            class_hash=class_hash,
            constructor_calldata_size=constructor_calldata_size,
            constructor_calldata=constructor_calldata,
            deployer_address=0,
        );
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L483-494)
```text
    let deployer_address = (1 - deploy_from_zero) * caller_address;

    let selectable_builtins = &builtin_ptrs.selectable;
    let hash_ptr = selectable_builtins.pedersen;
    with hash_ptr {
        let (contract_address) = get_contract_address(
            salt=request.contract_address_salt,
            class_hash=request.class_hash,
            constructor_calldata_size=constructor_calldata_size,
            constructor_calldata=constructor_calldata_start,
            deployer_address=deployer_address,
        );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/contract_address/contract_address.cairo (L12-35)
```text
func get_contract_address{hash_ptr: HashBuiltin*, range_check_ptr}(
    salt: felt,
    class_hash: felt,
    constructor_calldata_size: felt,
    constructor_calldata: felt*,
    deployer_address: felt,
) -> (contract_address: felt) {
    let (hash_state_ptr) = hash_init();
    let (hash_state_ptr) = hash_update_single(
        hash_state_ptr=hash_state_ptr, item=CONTRACT_ADDRESS_PREFIX
    );
    let (hash_state_ptr) = hash_update_single(hash_state_ptr=hash_state_ptr, item=deployer_address);
    let (hash_state_ptr) = hash_update_single(hash_state_ptr=hash_state_ptr, item=salt);
    let (hash_state_ptr) = hash_update_single(hash_state_ptr=hash_state_ptr, item=class_hash);
    let (hash_state_ptr) = hash_update_with_hashchain(
        hash_state_ptr=hash_state_ptr,
        data_ptr=constructor_calldata,
        data_length=constructor_calldata_size,
    );
    let (contract_address_before_modulo) = hash_finalize(hash_state_ptr=hash_state_ptr);
    let (contract_address) = normalize_address(addr=contract_address_before_modulo);

    return (contract_address=contract_address);
}
```
