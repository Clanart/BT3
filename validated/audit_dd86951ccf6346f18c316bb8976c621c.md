### Title
Missing Nonce in `meta_tx_v0` Hash Enables Unbounded Replay of Authorized Calls — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_meta_tx_v0` syscall constructs a transaction hash that does not include any nonce or unique identifier. The OS simultaneously skips all nonce management for version-0 transactions. Any attacker who observes a valid `meta_tx_v0` execution on-chain can replay the identical call — with the same hash and signature — an unlimited number of times by wrapping it in a fresh outer invoke transaction.

---

### Finding Description

`execute_meta_tx_v0` in `syscall_impls.cairo` is a syscall available to any contract. It constructs a synthetic version-0 `TxInfo` and executes `__execute__` on a target account contract. The transaction hash it uses is computed by `compute_meta_tx_v0_hash`:

```cairo
func compute_meta_tx_v0_hash{pedersen_ptr: HashBuiltin*}(
    contract_address: felt,
    entry_point_selector: felt,
    calldata: felt*,
    calldata_size: felt,
    chain_id: felt,          // <-- no nonce parameter
) -> felt {
    let (tx_hash) = deprecated_get_transaction_hash{hash_ptr=pedersen_ptr}(
        ...
        additional_data_size=0,          // <-- nonce is NOT included
        additional_data=cast(0, felt*),
    );
``` [1](#0-0) 

The resulting `TxInfo` is constructed with a hardcoded `nonce=0` and `version=0`:

```cairo
tempvar new_tx_info = new TxInfo(
    version=0,
    ...
    nonce=0,
    ...
);
``` [2](#0-1) 

The OS-level nonce enforcement function `check_and_increment_nonce` explicitly skips all nonce management for version-0 transactions:

```cairo
func check_and_increment_nonce{contract_state_changes: DictAccess*}(tx_info: TxInfo*) -> () {
    // Do not handle nonce for version 0.
    if (tx_info.version == 0) {
        return ();
    }
``` [3](#0-2) 

`check_and_increment_nonce` is never called for the meta-tx path at all — the syscall goes directly to `contract_call_helper` after constructing the context: [4](#0-3) 

The combined effect: the hash `H(contract_address, selector, calldata, chain_id)` is identical for every invocation of the same logical call. No on-chain state is updated to mark the call as consumed.

---

### Impact Explanation

**Critical — Direct loss of funds.**

A victim account contract that implements `__execute__` for version-0 transactions verifies the caller's signature against `tx_info.transaction_hash`. Because the hash is deterministic and nonce-free, the same `(hash, signature)` pair is valid for every future replay. An attacker who observes a legitimate `meta_tx_v0` execution (all data is on-chain) can replay it in any subsequent block by wrapping it in a new outer invoke transaction. If the original call transferred tokens or performed any value-moving operation, the attacker can drain the victim's account by replaying the call repeatedly.

---

### Likelihood Explanation

**High.** The attacker requires no privileged access. They only need to:
1. Observe a valid `meta_tx_v0` execution on-chain (calldata and signature are public).
2. Deploy or reuse any contract that invokes the `meta_tx_v0` syscall.
3. Submit a standard invoke transaction (version 3, with their own nonce) wrapping the replayed meta-tx.

The outer transaction's nonce is properly managed, so it passes OS-level validation. The inner meta-tx has no OS-level replay guard. The attack is repeatable across blocks as long as the victim's account holds funds.

---

### Recommendation

Include a nonce in the `meta_tx_v0` hash computation. The nonce should be sourced from the target account contract's on-chain state and incremented by the OS after each successful execution, analogous to how `check_and_increment_nonce` works for version-3 transactions. Specifically:

1. Add a `nonce` parameter to `compute_meta_tx_v0_hash` and pass it as `additional_data` (setting `additional_data_size=1`).
2. Read the target contract's current nonce from `contract_state_changes` before execution and write back an incremented value after execution, within `execute_meta_tx_v0`.
3. Require the caller to supply the expected nonce in the `MetaTxV0Request` struct so the OS can enforce monotonicity.

---

### Proof of Concept

1. Alice holds 1000 tokens. Bob (legitimate) calls `meta_tx_v0` targeting Alice's account with calldata encoding `transfer(Bob, 100)` and Alice's valid signature `S`. The OS computes `H = hash(Alice, __execute__, calldata, chain_id)` and executes Alice's `__execute__`, transferring 100 tokens to Bob.

2. Eve observes the transaction on-chain. She extracts `calldata` and `S`.

3. Eve deploys `EvilContract` with a single function that calls the `meta_tx_v0` syscall with `contract_address=Alice`, `selector=__execute__`, the same `calldata`, and the same signature `S`.

4. Eve submits `invoke(EvilContract.__evil__, nonce=Eve_nonce)`. The OS processes Eve's outer transaction (nonce valid), enters `EvilContract`, which calls `meta_tx_v0`. The OS computes the same `H`, constructs `TxInfo(version=0, nonce=0, transaction_hash=H)`, and calls Alice's `__execute__`. Alice's contract sees the same hash and signature it previously accepted, passes signature verification, and transfers another 100 tokens to Bob (or to Eve if Eve modified the calldata recipient — but the hash would differ; Eve must replay exactly).

5. Eve repeats step 4 with a fresh outer nonce each time. Each iteration drains 100 tokens from Alice. No OS-level guard prevents this.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L295-314)
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L389-393)
```text
    contract_call_helper(
        remaining_gas=remaining_gas,
        block_context=block_context,
        execution_context=execution_context,
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
