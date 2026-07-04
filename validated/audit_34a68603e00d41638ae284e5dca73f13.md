### Title
Deploy Syscall Unconditionally Reports Success Regardless of Constructor Revert, Enabling Permanent Fund Freeze — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_deploy` function in `syscall_impls.cairo` hardcodes `failure_flag=0` in the deploy syscall response, unconditionally reporting success to the calling contract even when the deployed contract's constructor reverts. Because the OS silently rolls back the constructor's state changes via the revert log but still tells the caller "deploy succeeded," any calling contract that holds funds and relies on the deployed contract being live can have those funds permanently frozen.

---

### Finding Description

In `execute_deploy` (lines 527–554 of `syscall_impls.cairo`), after calling `deploy_contract`, the response header is written with an unconditional `failure_flag=0`:

```cairo
// TODO(Yoni, 1/1/2026): support failures.
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=0);
```

The `deploy_contract` function (in `deploy_contract.cairo`) internally calls `select_execute_entry_point_func`, which fully supports constructor reverts and returns an `is_reverted` flag:

```cairo
let (is_reverted, retdata_size, retdata, _is_deprecated) = select_execute_entry_point_func(
    block_context=block_context, execution_context=constructor_execution_context
);
```

When the constructor reverts, `deploy_contract` rolls back the constructor's state changes through the revert log (the contract address is never written into `contract_state_changes` with a valid class hash). However, `deploy_contract` returns only `(retdata_size, retdata)` to `execute_deploy` — the `is_reverted` flag is consumed internally and never surfaced. `execute_deploy` therefore has no way to detect the failure and unconditionally writes `failure_flag=0`.

The calling contract receives:
- A valid-looking contract address.
- A success response (`failure_flag=0`).

But the contract at that address was never actually deployed — its `class_hash` remains `UNINITIALIZED_CLASS_HASH` in the state.

This is the direct analog of the ComplexRewarder pattern: a child operation (constructor) fails, the failure is not propagated to the parent (deploy syscall), and the parent silently reports success, leaving the system in an inconsistent state that can freeze funds.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

A calling contract that:
1. Invokes the `deploy` syscall with a class whose constructor can revert.
2. Receives the success response and stores the returned contract address.
3. Transfers ERC-20 tokens (or other assets) to that address, or locks its own funds contingent on the deployed contract being functional.

…will have those funds permanently frozen. The "deployed" address has no code (`class_hash = UNINITIALIZED_CLASS_HASH`), so no entry point can ever be called on it to recover the funds. Because the OS proof is valid (the OS correctly rolled back state), the sequencer will include the block, and the frozen state becomes final on L1.

---

### Likelihood Explanation

**Medium.**

- Any unprivileged transaction sender can invoke the `deploy` syscall from a contract they control.
- Factory-pattern contracts (common in DeFi) routinely deploy child contracts and immediately interact with them or transfer assets to them.
- A malicious class declarer can publish a class whose constructor reverts under attacker-chosen conditions (e.g., after a specific storage value is set), then trick a victim factory contract into deploying it.
- The TODO comment (`// TODO(Yoni, 1/1/2026): support failures.`) confirms the developers are aware this path is unimplemented, meaning no defensive check exists anywhere in the call chain.

---

### Recommendation

Propagate the constructor's `is_reverted` result from `deploy_contract` back to `execute_deploy` and use it to set `failure_flag` correctly:

1. Change `deploy_contract`'s return signature to include `is_reverted`.
2. In `execute_deploy`, replace the hardcoded `failure_flag=0` with `failure_flag=is_reverted`.
3. When `is_reverted=1`, write a `FailureReason` segment (consistent with how `contract_call_helper` handles reverts) so the calling contract can inspect the error.

---

### Proof of Concept

**Root cause — unconditional success flag:** [1](#0-0) 

**Constructor revert is supported but not surfaced:** [2](#0-1) 

**Attacker entry path — `deploy` syscall dispatch:** [3](#0-2) 

**Attack scenario:**

1. Attacker declares a class `MaliciousChild` whose constructor reverts when `storage[0] == 1`.
2. Attacker calls a victim factory contract `VaultFactory` (which holds user funds) and triggers `deploy(MaliciousChild, ...)`.
3. The OS executes the constructor; it reverts. The OS rolls back the constructor's state changes. The contract address has `class_hash = UNINITIALIZED_CLASS_HASH`.
4. `execute_deploy` writes `ResponseHeader(gas=remaining_gas, failure_flag=0)` — success.
5. `VaultFactory` reads the success flag, stores the returned address as `child_vault`, and transfers user funds to `child_vault`.
6. `child_vault` has no code. No entry point can ever be called. Funds are permanently frozen.
7. The OS proof is valid; the block is accepted on L1. The freeze is irreversible.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L527-539)
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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deploy_contract.cairo (L82-90)
```text
    let (is_reverted, retdata_size, retdata, _is_deprecated) = select_execute_entry_point_func(
        block_context=block_context, execution_context=constructor_execution_context
    );

    // Entries before this point belong to the deployed contract.
    assert [revert_log] = RevertLogEntry(selector=CHANGE_CONTRACT_ENTRY, value=contract_address);
    let revert_log = &revert_log[1];

    // The deprecated deploy syscalls do not support reverts.
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_syscalls.cairo (L165-173)
```text
    if (selector == DEPLOY_SELECTOR) {
        execute_deploy(block_context=block_context, caller_execution_context=execution_context);
        %{ OsLoggerExitSyscall %}
        return execute_syscalls(
            block_context=block_context,
            execution_context=execution_context,
            syscall_ptr_end=syscall_ptr_end,
        );
    }
```
