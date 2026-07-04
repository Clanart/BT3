### Title
Constructor Failure in `execute_deploy` Syscall Silently Reports Success, Enabling Permanent Fund Freezing - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_deploy` syscall handler in the StarkNet OS always writes `failure_flag=0` (success) into the response header, regardless of whether the deployed contract's constructor actually succeeded or reverted. A calling contract receives a contract address and a success signal even when the constructor failed and no contract was deployed. Any subsequent fund transfer to that address results in permanently frozen funds.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_deploy` function calls `deploy_contract` and then unconditionally writes a success response:

```cairo
with remaining_gas {
    let (retdata_size, retdata) = deploy_contract(
        block_context=block_context, constructor_execution_context=constructor_execution_context
    );
}

// Write the response header.
// TODO(Yoni, 1/1/2026): support failures.
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=0);
```

The `failure_flag` is hardcoded to `0` (success). The return value of `deploy_contract` is `(retdata_size, retdata)` — there is no `is_reverted` field returned or checked. The developer-authored TODO comment `// TODO(Yoni, 1/1/2026): support failures.` explicitly acknowledges that failure propagation is unimplemented.

Compare this to `contract_call_helper` in the same file, which correctly propagates the revert status:

```cairo
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=is_reverted);
```

The `execute_deploy` path lacks this check entirely.

The analog to the external report is exact: just as `ERC20Helper.transfer` could fail silently while the rest of the transaction proceeded (taking user funds without providing collateral), here `deploy_contract` can fail silently while the OS reports success to the calling contract, which then proceeds to interact with a non-existent contract address.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

When a constructor reverts, the state changes for that contract address are rolled back (no contract is deployed). However, the calling contract receives `failure_flag=0` and a valid-looking `contract_address`. If the calling contract subsequently transfers tokens to that address (a common pattern: deploy a vault, fund it), those tokens are sent to an address with no deployed contract. On StarkNet, ERC-20 `transfer` updates storage at the recipient address regardless of whether a contract exists there. The tokens are permanently frozen because there is no contract code at that address capable of withdrawing them.

---

### Likelihood Explanation

**Medium.** Any contract that uses the `deploy` syscall and then interacts with the returned address is affected. This is a standard pattern in DeFi (factory contracts, vault deployers, proxy deployers). A constructor can fail due to:
- Attacker-controlled constructor arguments that trigger a revert condition
- Race conditions where shared state read by the constructor is modified before the deployment (front-running)
- Any on-chain condition the constructor validates (e.g., checking a price feed, a registry entry, or a nonce)

The entry path requires no privileged access — any unprivileged user can submit a transaction that triggers a `deploy` syscall with a failing constructor.

---

### Recommendation

In `execute_deploy`, capture the revert status from `deploy_contract` and propagate it to the response header, mirroring the pattern used in `contract_call_helper`:

```cairo
// Replace:
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=0);

// With:
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=is_reverted);
```

`deploy_contract` must be updated to return an `is_reverted` flag (analogous to `execute_entry_point`). Additionally, when `is_reverted=1`, the `contract_address` field in `DeployResponse` should be set to `0` or another sentinel to prevent callers from using the address.

---

### Proof of Concept

1. Attacker deploys a factory contract `F` that:
   - Calls `deploy(class_hash=VaultClass, constructor_calldata=[attacker_trigger])` via the `deploy` syscall.
   - On success (per the response), immediately calls `token.transfer(deployed_address, 1_000_000)`.

2. The `VaultClass` constructor reads a storage slot and reverts if it equals `1`. Attacker pre-sets that slot to `1`.

3. OS execution:
   - `execute_deploy` calls `deploy_contract` → constructor reverts → state changes rolled back → no contract at `deployed_address`.
   - `execute_deploy` writes `ResponseHeader(gas=..., failure_flag=0)` — reports success.
   - Factory contract `F` reads `failure_flag=0`, proceeds to call `token.transfer(deployed_address, 1_000_000)`.
   - Transfer succeeds (ERC-20 storage updated at `deployed_address`).
   - `deployed_address` has no contract code. Tokens are permanently frozen.

4. The OS proof is generated and accepted. The state transition is valid from the prover's perspective. The fund loss is irreversible on-chain.

---

**Root cause location:** [1](#0-0) 

**Contrast with correct pattern in the same file:** [2](#0-1)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L428-434)
```text
    let response_header = cast(syscall_ptr, ResponseHeader*);
    let syscall_ptr = syscall_ptr + ResponseHeader.SIZE;

    // Write the response header.
    with_attr error_message("Predicted gas costs are inconsistent with the actual execution.") {
        assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=is_reverted);
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L534-539)
```text
    let response_header = cast(syscall_ptr, ResponseHeader*);
    let syscall_ptr = syscall_ptr + ResponseHeader.SIZE;

    // Write the response header.
    // TODO(Yoni, 1/1/2026): support failures.
    assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=0);
```
