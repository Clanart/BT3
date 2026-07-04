### Title
Hardcoded `failure_flag=0` in `execute_deploy` Syscall Causes Unprovable Block on Constructor Revert — (File: `execution/syscall_impls.cairo`)

### Summary

The `execute_deploy` function in `syscall_impls.cairo` unconditionally writes `failure_flag=0` to the deploy response header, with an explicit TODO comment acknowledging missing failure support. When an unprivileged user triggers a `deploy` syscall whose constructor reverts, the sequencer records `failure_flag=1` for that syscall response, but the OS Cairo program writes `failure_flag=0`. This mismatch makes the block unprovable, halting the network's ability to confirm new transactions.

### Finding Description

In `execute_deploy` at line 539 of `syscall_impls.cairo`, after calling `deploy_contract`, the response header is written with a hardcoded `failure_flag=0`:

```cairo
// Write the response header.
// TODO(Yoni, 1/1/2026): support failures.
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=0);
``` [1](#0-0) 

The TODO comment explicitly confirms that failure handling is not implemented. The `deploy_contract` call returns only `(retdata_size, retdata)` — it does not return an `is_reverted` flag:

```cairo
with remaining_gas {
    let (retdata_size, retdata) = deploy_contract(
        block_context=block_context, constructor_execution_context=constructor_execution_context
    );
}
``` [2](#0-1) 

Compare this to `contract_call_helper`, which correctly propagates `is_reverted` into the response header:

```cairo
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=is_reverted);
``` [3](#0-2) 

The `execute_deploy` function is missing the analogous logic. When the sequencer executes a block containing a `deploy` syscall whose constructor reverts, it records `failure_flag=1` in its execution trace. The OS Cairo program, however, writes `failure_flag=0` unconditionally. The hint `CheckNewDeployResponse` (line 545) then attempts to verify the response against the sequencer's recorded execution, detects the mismatch, and the prover cannot satisfy the constraints. No valid proof can be generated for the block. [4](#0-3) 

### Impact Explanation

If a block contains any transaction that invokes the `deploy` syscall and whose constructor reverts, the OS cannot generate a valid STARK proof for that block. Since StarkNet's L1 finality depends on proof submission, the network cannot confirm new transactions — a total network shutdown matching the **High: Network not being able to confirm new transactions** impact category.

### Likelihood Explanation

StarkNet includes reverted transactions in blocks (they still consume gas and are part of the state diff). A user can trivially deploy a factory contract whose `deploy` syscall targets a class with a constructor that always reverts (e.g., `assert 1 = 0`). This is a zero-privilege, low-cost action. The sequencer will include the outer transaction in a block, triggering the unprovable state.

### Recommendation

Refactor `execute_deploy` to mirror `contract_call_helper`: have `deploy_contract` return an `is_reverted` flag (or use `select_execute_entry_point_func` directly), and propagate it into the response header as `failure_flag=is_reverted`. The TODO comment at line 538 already identifies this gap; it must be resolved before the syscall is exposed to mainnet traffic.

### Proof of Concept

1. User declares a contract class whose constructor contains `assert 1 = 0` (always reverts).
2. User deploys a factory contract that calls the `deploy` syscall targeting that class.
3. Sequencer executes the factory transaction; the inner `deploy` syscall fails; sequencer records `failure_flag=1` for the deploy response.
4. OS proving begins: `execute_deploy` is reached; `deploy_contract` is called; the OS writes `assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=0)`.
5. Hint `CheckNewDeployResponse` compares the OS-written response (`failure_flag=0`) against the sequencer's recorded response (`failure_flag=1`) — mismatch detected; prover aborts.
6. No valid proof is produced for the block; L1 state update stalls; network cannot confirm further transactions.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L432-434)
```text
    with_attr error_message("Predicted gas costs are inconsistent with the actual execution.") {
        assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=is_reverted);
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L527-531)
```text
    with remaining_gas {
        let (retdata_size, retdata) = deploy_contract(
            block_context=block_context, constructor_execution_context=constructor_execution_context
        );
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L534-540)
```text
    let response_header = cast(syscall_ptr, ResponseHeader*);
    let syscall_ptr = syscall_ptr + ResponseHeader.SIZE;

    // Write the response header.
    // TODO(Yoni, 1/1/2026): support failures.
    assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=0);

```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L544-555)
```text

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
