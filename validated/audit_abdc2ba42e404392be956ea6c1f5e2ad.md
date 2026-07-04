### Title
User-Controlled Salt in `deploy_account` Enables Front-Running Grief of Account Deployment, Permanently Freezing Pre-Funded Assets - (File: `execution/deploy_contract.cairo`, `execution/transaction_impls.cairo`, `execution/syscall_impls.cairo`)

---

### Summary

The `deploy_account` transaction computes its contract address using a user-controlled `salt` with a hardcoded `deployer_address=0`. The `deploy` syscall with `deploy_from_zero=TRUE` uses the **identical** address formula. An unprivileged attacker who observes a pending `deploy_account` transaction can front-run it by deploying any contract to the same address first, causing the victim's `deploy_account` to fail at the OS-level uninitialized-address assertion. If the victim pre-funded the expected address — a common pattern — those assets are permanently frozen.

---

### Finding Description

**Root cause 1 — `deploy_account` uses `deployer_address=0`:**

In `transaction_impls.cairo`, `prepare_constructor_execution_context` computes the account address with `deployer_address` hardcoded to `0`:

```cairo
// transaction_impls.cairo lines 538–544
let (contract_address) = get_contract_address(
    salt=contract_address_salt,
    class_hash=class_hash,
    constructor_calldata_size=constructor_calldata_size,
    constructor_calldata=constructor_calldata,
    deployer_address=0,          // <-- hardcoded, not tied to any sender
);
``` [1](#0-0) 

**Root cause 2 — `deploy` syscall with `deploy_from_zero=TRUE` produces the same address:**

In `syscall_impls.cairo`, `execute_deploy` sets `deployer_address=0` whenever `deploy_from_zero=1`:

```cairo
// syscall_impls.cairo lines 480–483
tempvar deploy_from_zero = request.deploy_from_zero;
assert deploy_from_zero * (deploy_from_zero - 1) = 0;
// Set deployer_address to 0 if request.deploy_from_zero is TRUE.
let deployer_address = (1 - deploy_from_zero) * caller_address;
``` [2](#0-1) 

Both paths feed into the same `get_contract_address` function:

```cairo
// contract_address.cairo — address = hash(PREFIX, deployer_address, salt, class_hash, hash(calldata))
``` [3](#0-2) 

For the same `(salt, class_hash, constructor_calldata)` tuple, both paths produce **identical addresses**.

**Root cause 3 — OS enforces address must be uninitialized:**

In `deploy_contract.cairo`, the OS asserts the target address has never been deployed to:

```cairo
// deploy_contract.cairo lines 53–54
assert state_entry.class_hash = UNINITIALIZED_CLASS_HASH;
assert state_entry.nonce = 0;
``` [4](#0-3) 

If an attacker deploys **any** contract to the target address before the victim's `deploy_account` is processed, the victim's transaction fails unconditionally at this assertion. There is no retry or recovery path — the address is permanently occupied.

---

### Impact Explanation

Pre-funding an account address before deploying is a standard pattern: a user computes their deterministic account address, receives tokens at that address (e.g., from an exchange or another user), then submits `deploy_account`. If an attacker griefs the deployment by occupying the address first with a different contract, the pre-funded STRK/ETH is permanently frozen. The user cannot access the funds because the deployed contract is not their account, and the address can never be redeployed (the `UNINITIALIZED_CLASS_HASH` assertion is permanent). This constitutes **permanent freezing of funds — Critical impact**.

---

### Likelihood Explanation

The attack requires:
1. Observing a pending `deploy_account` transaction (possible via public mempool or network monitoring).
2. Submitting a transaction from any deployed contract that calls `deploy(salt=same, class_hash=any, deploy_from_zero=TRUE)` before the victim's transaction is processed.
3. The victim having pre-funded the address.

No privileged access is required. The attacker is an ordinary contract deployer. Pre-funding an account address before deployment is a well-established pattern across EVM and non-EVM chains. As StarkNet moves toward a decentralized sequencer with a public mempool, front-running becomes trivially achievable. Even under the current centralized sequencer, an attacker who submits their transaction first in the same block window can succeed.

---

### Recommendation

1. **Separate address spaces**: Introduce a distinct prefix or type discriminator for `deploy_account` addresses versus `deploy`-syscall addresses, so the two cannot collide. For example, use a `DEPLOY_ACCOUNT_PREFIX` constant distinct from `CONTRACT_ADDRESS_PREFIX` when computing the address in `prepare_constructor_execution_context`.
2. **Bind `deploy_account` address to sender**: Instead of `deployer_address=0`, use the transaction sender's public key or a sequencer-assigned nonce as part of the address derivation, making the address non-predictable by third parties.
3. **Remove `deploy_from_zero`**: If the `deploy_from_zero` flag is not strictly necessary, removing it eliminates the collision surface entirely.

---

### Proof of Concept

```
1. User computes expected account address:
   addr = hash("STARKNET_CONTRACT_ADDRESS", 0, salt, class_hash, hash(calldata))

2. User sends 1000 STRK to `addr` (pre-funding before account is live).

3. User broadcasts deploy_account(salt=S, class_hash=C, calldata=D).

4. Attacker observes the pending tx. Attacker's contract calls:
   deploy(class_hash=EVIL_CLASS, salt=S, constructor_calldata=D, deploy_from_zero=TRUE)
   → produces the same `addr` because deployer_address=0 in both paths.

5. Attacker's tx is processed first (same block, earlier in ordering).
   deploy_contract() succeeds: addr now has class_hash=EVIL_CLASS.

6. User's deploy_account is processed next.
   deploy_contract.cairo line 53: assert state_entry.class_hash = UNINITIALIZED_CLASS_HASH
   → FAILS. Transaction reverts.

7. User's 1000 STRK is permanently locked in the attacker-controlled contract at `addr`.
   The address can never be redeployed; funds are irrecoverable.
```

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L536-545)
```text
    let hash_ptr = builtin_ptrs.selectable.pedersen;
    with hash_ptr {
        let (contract_address) = get_contract_address(
            salt=contract_address_salt,
            class_hash=class_hash,
            constructor_calldata_size=constructor_calldata_size,
            constructor_calldata=constructor_calldata,
            deployer_address=0,
        );
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L479-494)
```text
    // Verify deploy_from_zero is either 0 (FALSE) or 1 (TRUE).
    tempvar deploy_from_zero = request.deploy_from_zero;
    assert deploy_from_zero * (deploy_from_zero - 1) = 0;
    // Set deployer_address to 0 if request.deploy_from_zero is TRUE.
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/contract_address/contract_address.cairo (L12-34)
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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deploy_contract.cairo (L51-54)
```text
    local state_entry: StateEntry*;
    %{ GetContractAddressStateEntry %}
    assert state_entry.class_hash = UNINITIALIZED_CLASS_HASH;
    assert state_entry.nonce = 0;
```
