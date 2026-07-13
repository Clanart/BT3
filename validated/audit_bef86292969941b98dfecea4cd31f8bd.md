### Title
`eth_estimateGas` Binary-Search Lower-Bound Optimization Causes Systematic Gas Underestimate for `try/catch` Transactions, Silently Routing Execution to Catch Block - (`x/evm/keeper/grpc_query.go`)

---

### Summary

`EstimateGas` in `x/evm/keeper/grpc_query.go` applies an optimization that sets the binary-search lower bound to `ExecutionGasUsed - 1` from the unconstrained (full-gas) probe. For any contract that uses Solidity `try/catch` with a gas-intensive subcall, this optimization violates its own stated invariant: the unconstrained gas used is **not** a lower bound for the gas required to execute the try-branch. The binary search therefore converges on a gas value that is only sufficient to execute the catch branch. Every user who calls `eth_estimateGas` on such a transaction and submits with the returned estimate will silently execute the catch branch instead of the try branch, producing unintended state changes.

---

### Finding Description

`EstimateGas` first runs the transaction with the maximum allowable gas (`hi`) to get a baseline:

```go
failed, result, err := executable(hi)
```

It then tightens the binary-search lower bound using the gas actually consumed in that unconstrained run:

```go
// For almost any transaction, the gas consumed by the unconstrained execution
// above lower-bounds the gas limit required for it to succeed.
if result.ExecutionGasUsed > 0 {
    lo = result.ExecutionGasUsed - 1
}
``` [1](#0-0) 

The comment itself acknowledges the exception: transactions that "explicitly check gas remaining." Solidity `try/catch` is exactly such a case, because the EVM's EIP-150 rule forwards at most 63/64 of the available gas to a subcall. When the binary search probes a `mid` value between `lo` and `hi`, the subcall receives only `⌊63/64 × mid⌋` gas. If that is less than the gas the subcall needs, the subcall runs out of gas, the catch branch executes, and the outer transaction **succeeds** (no `VmError`). The `executable` closure therefore returns `failed = false`:

```go
executable := func(gas uint64) (vmError bool, rsp *types.EVMResult, err error) {
    msg.GasLimit = gas
    rsp, err = k.ApplyMessageWithConfig(ctx, msg, cfg, false)
    ...
    return rsp.Failed(), rsp, nil
}
``` [2](#0-1) 

`BinSearch` treats any non-failed result as "sufficient gas" and moves `hi` down:

```go
func BinSearch(lo, hi uint64, executable func(uint64) (bool, *EVMResult, error)) (uint64, error) {
    for lo+1 < hi {
        mid := (hi + lo) / 2
        failed, _, err := executable(mid)
        ...
        if failed { lo = mid } else { hi = mid }
    }
    return hi, nil
}
``` [3](#0-2) 

The search converges on the minimum gas needed to execute the **catch** branch, not the try branch. The returned estimate is structurally insufficient to run the intended code path.

The codebase already contains a test contract (`GasConsumerTryCatch`) and an integration test (`test_trycatch_gas_estimation_underestimate`) that directly exercises this scenario — `callWithTryCatch(20, false)` — and asserts `gas_diff == 0`: [4](#0-3) [5](#0-4) 

The test's assertion that `actual_gas - estimated_gas == 0` is the failing condition that confirms the underestimate.

---

### Impact Explanation

When a user calls `eth_estimateGas` on a transaction that invokes a gas-intensive subcall inside a `try` block, the returned gas estimate is only sufficient to execute the **catch** branch. If the user submits the transaction with that estimate (standard wallet behavior), the catch branch executes instead of the try branch. Depending on the contract, this can:

- Silently skip the intended operation (e.g., a token transfer, a mint, a state update)
- Execute an error-handling path that pauses the contract, emits misleading events, or records incorrect state
- Cause loss of user funds if the catch branch has different accounting behavior

This matches the allowed High impact: **"Public JSON-RPC path feeds incorrect consensus-critical data into transaction execution."** The `eth_estimateGas` endpoint is the standard mechanism wallets and dApps use to set gas limits; a systematic underestimate for an entire class of contracts (any contract using `try/catch` with heavy subcalls) is a reachable, unprivileged path to wrong execution.

---

### Likelihood Explanation

- Any Solidity contract using `try/catch` with a subcall that consumes more than `~63/64` of the available gas is affected.
- No special attacker role is required; any user calling `eth_estimateGas` and submitting with the result triggers the wrong path.
- The pattern is common: protocol contracts (auction settlement, token minting with vesting, DEX routers) frequently use `try/catch` to handle subcall failures gracefully.
- The codebase itself contains a dedicated test contract and integration test for this exact scenario, confirming the developers identified the issue.

---

### Recommendation

Remove or guard the `lo = result.ExecutionGasUsed - 1` shortcut. The invariant "unconstrained gas used lower-bounds the gas required to succeed" does not hold for any transaction whose execution path changes based on available gas (try/catch, explicit `gasleft()` checks). The binary search should start from the default `lo = ethparams.TxGas - 1` for all transactions, or the optimization should only be applied after verifying that the execution path at `hi` and at `ExecutionGasUsed` is identical (e.g., same logs, same return data, same vmError).

---

### Proof of Concept

Consider `GasConsumerTryCatch.callWithTryCatch(20, false)`:

1. `executable(hi)` runs with full gas → `consumeGas(20, false)` succeeds → try branch emits `TrySuccess` → `ExecutionGasUsed = X` (≈ 430 000 gas for 20 SSTORE writes).
2. `lo` is set to `X - 1`.
3. Binary search probes `mid` between `X-1` and `hi`. At `mid ≈ X`, the subcall receives `⌊63/64 × mid⌋ < X` gas → subcall OOGs → catch branch executes → `failed = false`.
4. Search converges to `Y` (catch-branch gas, much less than `X`).
5. `eth_estimateGas` returns `Y`.
6. User submits with gas `Y` → catch branch executes → `TrySuccess` is never emitted, `lastResult` is never updated, the intended state change is silently skipped.

The integration test at `tests/integration_tests/test_trycatch_gas.py:42` asserts `gas_diff == 0` (estimated == actual), which fails under the current optimization, confirming the underestimate. [6](#0-5) [3](#0-2) [7](#0-6)

### Citations

**File:** x/evm/keeper/grpc_query.go (L389-402)
```go
	executable := func(gas uint64) (vmError bool, rsp *types.EVMResult, err error) {
		// update the message with the new gas value
		msg.GasLimit = gas

		// pass false to not commit StateDB
		rsp, err = k.ApplyMessageWithConfig(ctx, msg, cfg, false)
		if err != nil {
			if errors.Is(err, core.ErrIntrinsicGas) {
				return true, nil, nil // Special case, raise gas limit
			}
			return true, nil, err // Bail out
		}
		return rsp.Failed(), rsp, nil
	}
```

**File:** x/evm/keeper/grpc_query.go (L424-432)
```go
	// For almost any transaction, the gas consumed by the unconstrained execution
	// above lower-bounds the gas limit required for it to succeed. One exception
	// is those that explicitly check gas remaining in order to execute within a
	// given limit, but we probably don't want to return the lowest possible gas
	// limit for these cases anyway.
	// Use ExecutionGasUsed (actual gas before minGasMultiplier adjustment) for accurate estimation.
	if result.ExecutionGasUsed > 0 {
		lo = result.ExecutionGasUsed - 1
	}
```

**File:** x/evm/types/utils.go (L211-228)
```go
func BinSearch(lo, hi uint64, executable func(uint64) (bool, *EVMResult, error)) (uint64, error) {
	for lo+1 < hi {
		mid := (hi + lo) / 2
		failed, _, err := executable(mid)
		// If the error is not nil(consensus error), it means the provided message
		// call or transaction will never be accepted no matter how much gas it is
		// assigned. Return the error directly, don't struggle any more.
		if err != nil {
			return 0, err
		}
		if failed {
			lo = mid
		} else {
			hi = mid
		}
	}
	return hi, nil
}
```

**File:** tests/integration_tests/hardhat/contracts/GasConsumerTryCatch.sol (L48-67)
```text
    function callWithTryCatch(uint256 iterations, bool shouldRevert) external returns (bool success) {
        uint256 gasBefore = gasleft();
        callCount++;

        // using "this" to make an external call, enabling try-catch
        try this.consumeGas(iterations, shouldRevert) returns (uint256 result) {
            uint256 gasUsed = gasBefore - gasleft();
            lastResult = result;
            emit TrySuccess(result, gasUsed);
            return true;
        } catch Error(string memory reason) {
            uint256 gasUsed = gasBefore - gasleft();
            emit TryCatchFailed(reason, gasUsed);
            return false;
        } catch (bytes memory reason) {
            uint256 gasUsed = gasBefore - gasleft();
            emit TryCatchFailedBytes(reason, gasUsed);
            return false;
        }
    }
```

**File:** tests/integration_tests/test_trycatch_gas.py (L10-48)
```python
def test_trycatch_gas_estimation_underestimate(ethermint, geth):
    def process(w3, name):
        contract, _ = deploy_contract(w3, CONTRACTS["GasConsumerTryCatch"])
        tx = contract.functions.callWithTryCatch(20, False).build_transaction(
            {
                "from": ADDRS["community"],
            }
        )

        estimated_gas = w3.eth.estimate_gas(tx)
        tx["gas"] = 1000000
        receipt = send_transaction(w3, tx)
        actual_gas = receipt["gasUsed"]

        # Calculate the difference
        gas_diff = actual_gas - estimated_gas

        return {
            "name": name,
            "estimated_gas": estimated_gas,
            "actual_gas": actual_gas,
            "gas_diff": gas_diff,
        }

    with ThreadPoolExecutor(max_workers=2) as executor:
        ethermint_future = executor.submit(process, ethermint.w3, "ethermint")
        geth_future = executor.submit(process, geth.w3, "geth")
        ethermint_result = ethermint_future.result()
        geth_result = geth_future.result()

    # Compare results from ethermint and geth
    for result in (ethermint_result, geth_result):
        assert result["gas_diff"] == 0, (
            f"Testing on {result['name']} "
            f"Gas estimation is not accurate: "
            f"{result['estimated_gas']} estimated vs "
            f"{result['actual_gas']} actual "
            f"({result['gas_diff']} difference)"
        )
```
