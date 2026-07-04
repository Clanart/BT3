### Title
Missing Constructor Revert Handling in `deploy_contract` / `execute_deploy` Syscall - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deploy_contract.cairo`)

---

### Summary

The `deploy_contract` function, called by the `execute_deploy` syscall implementation, invokes a contract constructor via `select_execute_entry_point_func` but unconditionally asserts the constructor did not revert (`assert is_reverted = 0`). Simultaneously, `execute_deploy` in `syscall_impls.cairo` always writes `failure_flag=0` in the response header. If a constructor reverts, the OS execution trace becomes invalid and no valid proof can be generated for the block, causing a network halt.

---

### Finding Description

In `deploy_contract.cairo`, after invoking the constructor:

```cairo
let (is_reverted, retdata_size, retdata, _is_deprecated) = select_execute_entry_point_func(
    block_context=block_context, execution_context=constructor_execution_context
);
// The deprecated deploy syscalls do not support reverts.
assert is_reverted = 0;
``` [1](#0-0) 

The `select_execute_entry_point_func` call can legitimately return `is_reverted=1` when the constructor panics or runs out of gas. Instead of propagating this failure (reverting state changes and writing a failure response), the code hard-asserts success. A failing assertion in Cairo means the prover cannot produce a valid execution trace, so the block cannot be proven.

In `syscall_impls.cairo`, the `execute_deploy` function compounds this by always writing `failure_flag=0` in the response header, with an explicit TODO acknowledging the gap:

```cairo
// Write the response header.
// TODO(Yoni, 1/1/2026): support failures.
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=0);
``` [2](#0-1) 

The `deploy_contract` function's own header also carries a matching TODO:

```cairo
// TODO(Yoni, 1/1/2027): handle failures.
func deploy_contract{
``` [3](#0-2) 

By contrast, the `contract_call_helper` function (used for `call_contract` and `library_call`) correctly handles the reverted case by propagating `is_reverted` and appending `ERROR_ENTRY_POINT_FAILED` to the retdata:

```cairo
if (is_reverted != FALSE) {
    assert retdata[retdata_size] = ERROR_ENTRY_POINT_FAILED;
    ...
}
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=is_reverted);
``` [4](#0-3) 

The `deploy` syscall path is the only nested-call path that lacks this handling.

---

### Impact Explanation

If a block is sequenced that contains a transaction in which a contract calls the `deploy` syscall and the deployed contract's constructor reverts (e.g., due to a panic, an assertion failure, or running out of gas), the OS hits `assert is_reverted = 0`. This assertion failure makes the execution trace unprovable. The block cannot be finalized on L1, halting the network's ability to confirm new transactions.

**Impact: High — Network not being able to confirm new transactions (total network shutdown).**

---

### Likelihood Explanation

Any unprivileged user can:
1. Declare a contract class whose constructor always reverts (e.g., `assert 1 = 0`).
2. Deploy a thin wrapper contract that calls the `deploy` syscall with that class hash.
3. Submit a transaction invoking the wrapper.

If the sequencer (blockifier) includes this transaction in a block — which is consistent with StarkNet's model of including reverted transactions and charging fees — the OS proof for that block will fail. The blockifier and OS are known to diverge here: the blockifier handles constructor reverts as failed syscalls, while the OS asserts they cannot occur. This divergence is the exploitable gap.

---

### Recommendation

Mirror the pattern used in `contract_call_helper`: after calling `select_execute_entry_point_func` for the constructor, check `is_reverted`. If `is_reverted != 0`, invoke `handle_revert` to undo state changes (class hash assignment, storage writes), write a failure response header with `failure_flag=1`, and return the constructor's error retdata. Remove the `assert is_reverted = 0` line and the hardcoded `failure_flag=0` in `execute_deploy`.

---

### Proof of Concept

1. Attacker declares class `RevertingConstructor` whose `constructor` entry point executes `assert 1 = 0` (always reverts).
2. Attacker declares class `Deployer` whose external function calls `deploy(class_hash=RevertingConstructor, ...)`.
3. Attacker submits an `invoke` transaction calling `Deployer.__execute__`.
4. The blockifier executes the transaction off-chain: the `deploy` syscall fails (constructor reverted), the blockifier records a failed syscall response and includes the transaction in the block as a reverted call.
5. The OS processes the block: `execute_deploy` → `deploy_contract` → `select_execute_entry_point_func` returns `is_reverted=1` → `assert is_reverted = 0` **fails**.
6. The prover cannot generate a valid proof for this block. The block is stuck; no further blocks can be finalized. Network halt. [1](#0-0) [5](#0-4)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deploy_contract.cairo (L28-29)
```text
// TODO(Yoni, 1/1/2027): handle failures.
func deploy_contract{
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deploy_contract.cairo (L82-92)
```text
    let (is_reverted, retdata_size, retdata, _is_deprecated) = select_execute_entry_point_func(
        block_context=block_context, execution_context=constructor_execution_context
    );

    // Entries before this point belong to the deployed contract.
    assert [revert_log] = RevertLogEntry(selector=CHANGE_CONTRACT_ENTRY, value=contract_address);
    let revert_log = &revert_log[1];

    // The deprecated deploy syscalls do not support reverts.
    assert is_reverted = 0;
    return (retdata_size=retdata_size, retdata=retdata);
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L419-433)
```text
    if (is_reverted != FALSE) {
        // Append `ERROR_ENTRY_POINT_FAILED` to the retdata.
        assert retdata[retdata_size] = ERROR_ENTRY_POINT_FAILED;
        tempvar retdata_size = retdata_size + 1;
    } else {
        ap += 2;  // Align the stack to avoid revoked references.
        tempvar retdata_size = retdata_size;
    }

    let response_header = cast(syscall_ptr, ResponseHeader*);
    let syscall_ptr = syscall_ptr + ResponseHeader.SIZE;

    // Write the response header.
    with_attr error_message("Predicted gas costs are inconsistent with the actual execution.") {
        assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=is_reverted);
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L527-555)
```text
    with remaining_gas {
        let (retdata_size, retdata) = deploy_contract(
            block_context=block_context, constructor_execution_context=constructor_execution_context
        );
    }

    // TODO(Yoni, 1/1/2026): consider sharing code with call_contract_helper.
    let response_header = cast(syscall_ptr, ResponseHeader*);
    let syscall_ptr = syscall_ptr + ResponseHeader.SIZE;

    // Write the response header.
    // TODO(Yoni, 1/1/2026): support failures.
    assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=0);

    let response = cast(syscall_ptr, DeployResponse*);
    // Advance syscall pointer to the next syscall.
    let syscall_ptr = syscall_ptr + DeployResponse.SIZE;

    %{ CheckNewDeployResponse %}

    // Write the response.
    relocate_segment(src_ptr=response.constructor_retdata_start, dest_ptr=retdata);
    assert [response] = DeployResponse(
        contract_address=contract_address,
        constructor_retdata_start=retdata,
        constructor_retdata_end=retdata + retdata_size,
    );

    return ();
```
