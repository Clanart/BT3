### Title
Meta-Transaction V0 Hash Omits Nonce, Enabling Unbounded Signature Replay — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo`)

---

### Summary

`compute_meta_tx_v0_hash` constructs the signed digest for the `meta_tx_v0` syscall without including any nonce or replay-protection field. Because the hash is fully determined by `(contract_address, selector, calldata, chain_id)`, a single valid signature authorises the same action an unlimited number of times. Any unprivileged actor who observes a legitimate meta-tx v0 can re-submit it in subsequent outer transactions and drain the target account.

---

### Finding Description

**Vulnerable function — hash construction:**

`compute_meta_tx_v0_hash` in `transaction_hash/transaction_hash.cairo` (lines 295–315) delegates to `deprecated_get_transaction_hash` with `additional_data_size=0`:

```cairo
func compute_meta_tx_v0_hash{pedersen_ptr: HashBuiltin*}(
    contract_address: felt,
    entry_point_selector: felt,
    calldata: felt*,
    calldata_size: felt,
    chain_id: felt,
) -> felt {
    let (tx_hash) = deprecated_get_transaction_hash{hash_ptr=pedersen_ptr}(
        tx_hash_prefix=INVOKE_HASH_PREFIX,
        version=0,
        contract_address=contract_address,
        entry_point_selector=entry_point_selector,
        calldata_size=calldata_size,
        calldata=calldata,
        max_fee=0,
        chain_id=chain_id,
        additional_data_size=0,          // ← no nonce
        additional_data=cast(0, felt*),  // ← no nonce
    );
    return tx_hash;
}
``` [1](#0-0) 

Contrast this with `compute_l1_handler_transaction_hash`, which passes `additional_data_size=1, additional_data=&nonce` to bind the hash to a single use: [2](#0-1) 

**Vulnerable function — execution, no nonce check:**

`execute_meta_tx_v0` in `syscall_impls.cairo` (lines 286–399) hard-codes `nonce=0` in the synthesised `TxInfo` and never calls `check_and_increment_nonce`:

```cairo
tempvar new_tx_info = new TxInfo(
    version=0,
    ...
    nonce=0,   // ← always zero, never incremented
    ...
);
``` [3](#0-2) 

**Why the nonce guard is bypassed:**

`check_and_increment_nonce` explicitly skips version-0 transactions:

```cairo
func check_and_increment_nonce{contract_state_changes: DictAccess*}(tx_info: TxInfo*) -> () {
    // Do not handle nonce for version 0.
    if (tx_info.version == 0) {
        return ();
    }
    ...
}
``` [4](#0-3) 

Because `execute_meta_tx_v0` sets `version=0`, even if `check_and_increment_nonce` were called it would be a no-op. The OS therefore provides zero replay protection for meta-tx v0 at the protocol level.

---

### Impact Explanation

**Critical — Direct loss of funds.**

The hash `H = Pedersen(INVOKE_PREFIX, 0, contract_address, __execute__, calldata_hash, 0, chain_id)` is a pure function of public, static inputs. Once a user's account contract has accepted a signature over `H` (via `__validate__`), any party can re-submit the identical `(contract_address, selector, calldata, signature)` tuple inside any future outer transaction. The account's `__validate__` will compute the same `H`, verify the same signature, and authorise the same execution — repeatedly and without bound. A single intercepted or observed meta-tx v0 authorising a token transfer is sufficient to drain the account.

---

### Likelihood Explanation

The `meta_tx_v0` syscall is callable by any contract from within any outer transaction. No privileged role is required. A relayer or mempool observer who sees one valid meta-tx v0 broadcast can immediately replay it in the same or subsequent blocks. The attack requires only standard transaction submission capability, making it reachable by any unprivileged actor.

---

### Recommendation

Include a per-account, monotonically-increasing nonce in `compute_meta_tx_v0_hash` — analogous to how `compute_l1_handler_transaction_hash` passes `additional_data_size=1, additional_data=&nonce`. The OS must then read, verify, and increment that nonce inside `execute_meta_tx_v0`, just as `check_and_increment_nonce` does for regular account transactions. This binds each meta-tx v0 signature to exactly one execution.

---

### Proof of Concept

1. Alice's account contract is deployed at address `A`. Alice signs a meta-tx v0 authorising `calldata = [transfer(Bob, 100_STRK)]`. The outer relayer transaction executes it; Alice loses 100 STRK as intended.

2. The hash committed to is:
   ```
   H = Pedersen(INVOKE_PREFIX, 0, A, __execute__, hash([transfer(Bob,100)]), 0, chain_id)
   ```
   This value is identical in every future block because no nonce is mixed in.

3. Attacker Eve constructs a new outer transaction (with her own nonce and fee) that calls the `meta_tx_v0` syscall with the same `(contract_address=A, selector=__execute__, calldata, signature)` that Alice originally produced.

4. Inside `execute_meta_tx_v0`, the OS recomputes `H` (same inputs → same result), sets `nonce=0`, and invokes Alice's `__validate__`. The account verifies the signature against `H` — it matches — and returns `VALIDATED`.

5. `__execute__` runs again, transferring another 100 STRK to Bob. Eve repeats until Alice's balance is zero.

The root cause is entirely within the scoped OS files:
- Missing nonce field: `transaction_hash/transaction_hash.cairo` lines 311–312 [5](#0-4) 
- No nonce enforcement: `execution/syscall_impls.cairo` lines 343–363 [3](#0-2) 
- Version-0 nonce bypass: `execution/execute_transaction_utils.cairo` lines 63–67 [4](#0-3)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L220-238)
```text
func compute_l1_handler_transaction_hash{pedersen_ptr: HashBuiltin*}(
    execution_context: ExecutionContext*, chain_id: felt, nonce: felt
) -> felt {
    let (__fp__, _) = get_fp_and_pc();
    let (transaction_hash) = deprecated_get_transaction_hash{hash_ptr=pedersen_ptr}(
        tx_hash_prefix=L1_HANDLER_HASH_PREFIX,
        version=L1_HANDLER_VERSION,
        contract_address=execution_context.execution_info.contract_address,
        entry_point_selector=execution_context.execution_info.selector,
        calldata_size=execution_context.calldata_size,
        calldata=execution_context.calldata,
        max_fee=0,
        chain_id=chain_id,
        additional_data_size=1,
        additional_data=&nonce,
    );

    return transaction_hash;
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L295-315)
```text
func compute_meta_tx_v0_hash{pedersen_ptr: HashBuiltin*}(
    contract_address: felt,
    entry_point_selector: felt,
    calldata: felt*,
    calldata_size: felt,
    chain_id: felt,
) -> felt {
    let (tx_hash) = deprecated_get_transaction_hash{hash_ptr=pedersen_ptr}(
        tx_hash_prefix=INVOKE_HASH_PREFIX,
        version=0,
        contract_address=contract_address,
        entry_point_selector=entry_point_selector,
        calldata_size=calldata_size,
        calldata=calldata,
        max_fee=0,
        chain_id=chain_id,
        additional_data_size=0,
        additional_data=cast(0, felt*),
    );
    return tx_hash;
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L343-363)
```text
    tempvar new_tx_info = new TxInfo(
        version=0,
        account_contract_address=contract_address,
        max_fee=0,
        signature_start=request.signature_start,
        signature_end=request.signature_end,
        transaction_hash=meta_tx_hash,
        chain_id=old_tx_info.chain_id,
        nonce=0,
        resource_bounds_start=cast(0, ResourceBounds*),
        resource_bounds_end=cast(0, ResourceBounds*),
        tip=0,
        paymaster_data_start=cast(0, felt*),
        paymaster_data_end=cast(0, felt*),
        nonce_data_availability_mode=0,
        fee_data_availability_mode=0,
        account_deployment_data_start=cast(0, felt*),
        account_deployment_data_end=cast(0, felt*),
        proof_facts_start=cast(0, felt*),
        proof_facts_end=cast(0, felt*),
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transaction_utils.cairo (L63-67)
```text
func check_and_increment_nonce{contract_state_changes: DictAccess*}(tx_info: TxInfo*) -> () {
    // Do not handle nonce for version 0.
    if (tx_info.version == 0) {
        return ();
    }
```
