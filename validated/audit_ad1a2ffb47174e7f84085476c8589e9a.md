The reachable analog in `sei-chain--002` is the `rewards()` view method of the **Distribution precompile**, which is the only phase-2 query in that precompile that omits the non-payable guard that every sibling query enforces.

### Title
Value sent to the Distribution precompile's `rewards()` view function is permanently locked - (File: precompiles/distribution/distribution.go)

### Summary
The Distribution precompile (`0x0000000000000000000000000000000000001007`) dispatches every method through `PrecompileExecutor.Execute`. All other query/view methods (`balance`-style helpers, `params`, `validatorOutstandingRewards`, `validatorCommission`, `validatorSlashes`, `delegationRewards`, `delegatorValidators`, `delegatorWithdrawAddress`, `communityPool`) call `p.validateInput`/`pcommon.ValidateNonPayable(value)` to reject any msg.value attached to the call. `rewards()` is the single exception — it is dispatched directly with no such check.

### Finding Description
In `Execute`, `RewardsMethod` is routed straight to `p.rewards(ctx, method, args)` without a `pcommon.ValidateNonPayable(value)` call, unlike every other query handler in the same switch statement: [1](#0-0) [2](#0-1) 

The `rewards` function itself only performs a `DelegationTotalRewards` query and packs the response; it never touches `value` at all — no refund, no forwarding, no `HandlePaymentUsei`/`HandlePaymentUseiWei` call: [3](#0-2) 

Because the Distribution precompile address is not in the `payablePrecompiles` allow-list used by the EVM keeper (only `bank`, `staking`, `gov`, `wasmd` are listed there), it does not receive the special transfer-event suppression/refund handling that those payable precompiles get for legitimate value transfers: [4](#0-3) 

When an EVM `CALL` carries non-zero value to any address (including a precompile address), the EVM's normal value-transfer semantics move the usei/wei balance to that address as part of executing the call, before/alongside running the precompiled logic. Since `rewards()` accepts value without validation and never refunds or otherwise processes it, any usei/wei sent this way settles into the module account tied to the precompile address with no code path in the precompile that can move it back out — mirroring the reported bug class where a `receive()`/payable path admits funds that the contract has no logic to release.

This exact discrepancy is called out and deliberately worked around in the integration test suite, which skips the "view rejects value" assertion specifically for `rewards()`: [5](#0-4) 

### Impact Explanation
Any unprivileged EVM caller can invoke `IDistr.rewards(delegator)` with a non-zero `value` (e.g. via `{value: X}` in ethers/web3, or raw `eth_sendTransaction`) and the call succeeds instead of reverting. The attached sei is transferred to the precompile's backing account with no reachable precompile function to reclaim it, causing a permanent, irrecoverable loss of the sender's funds — a concrete case of fund freezing/loss reachable from a single transaction by any user.

### Likelihood Explanation
High likelihood of user error (accidentally passing `value` while calling a view method that superficially looks like the other queries, all of which reject value) and trivially exploitable/observable by any wallet or scripted caller, since it requires no special privileges, only a standard EVM call with `value` set.

### Recommendation
Add `pcommon.ValidateNonPayable(value)` at the start of `rewards()` (and the corresponding legacy versions in `precompiles/distribution/legacy/v*/distribution.go`), consistent with every other query method in the same `Execute` switch, so any value-bearing call to this view function reverts instead of stranding funds.

### Proof of Concept
1. From any funded EVM account, call the Distribution precompile at `0x...1007` with function selector for `rewards(address)` and `value` set to a non-zero amount (e.g., 1 SEI in wei units), supplying any valid delegator address as argument.
2. The call succeeds, returns the reward query result, and the transaction's value is deducted from the caller's balance.
3. Observe that the value never appears back on any account under the caller's control, and there is no precompile method (transaction or query) that lets it be withdrawn — the funds are permanently stuck at the module account backing the precompile address.

### Citations

**File:** precompiles/distribution/distribution.go (L204-205)
```go
	case RewardsMethod:
		return p.rewards(ctx, method, args)
```

**File:** precompiles/distribution/distribution.go (L437-447)
```go
func (p PrecompileExecutor) validateInput(value *big.Int, args []interface{}, expectedArgsLength int) error {
	if err := pcommon.ValidateNonPayable(value); err != nil {
		return err
	}

	if err := pcommon.ValidateArgsLength(args, expectedArgsLength); err != nil {
		return err
	}

	return nil
}
```

**File:** precompiles/distribution/distribution.go (L543-579)
```go
func (p PrecompileExecutor) rewards(ctx sdk.Context, method *abi.Method, args []interface{}) (ret []byte, remainingGas uint64, rerr error) {
	defer func() {
		if err := recover(); err != nil {
			ret = nil
			remainingGas = 0
			rerr = fmt.Errorf("%s", err)
			return
		}
	}()

	if err := pcommon.ValidateArgsLength(args, 1); err != nil {
		rerr = err
		return
	}

	seiDelegatorAddress, err := p.accAddressFromArg(ctx, args[0])
	if err != nil {
		rerr = err
		return
	}

	req := &distrtypes.QueryDelegationTotalRewardsRequest{
		DelegatorAddress: seiDelegatorAddress.String(),
	}

	wrappedC := sdk.WrapSDKContext(ctx)
	response, err := p.distrKeeper.DelegationTotalRewards(wrappedC, req)
	if err != nil {
		rerr = err
		return
	}

	rewardsOutput := getResponseOutput(response)
	ret, rerr = method.Outputs.Pack(rewardsOutput)
	remainingGas = pcommon.GetRemainingGas(ctx, p.evmKeeper)
	return
}
```

**File:** x/evm/keeper/precompile.go (L11-18)
```go
// add any payable precompiles here
// these will suppress transfer events to/from the precompile address
var payablePrecompiles = map[string]struct{}{
	bank.BankAddress:       {},
	staking.StakingAddress: {},
	gov.GovAddress:         {},
	wasmd.WasmdAddress:     {},
}
```

**File:** integration_test/precompile_tests/precompiles/distribution.spec.ts (L9-11)
```typescript
 * Guard quirk worth knowing: rewards() is the one phase-2 view WITHOUT a
 * non-payable check (a value-bearing call would succeed and strand the funds),
 * so this spec deliberately has no "view rejects value" test.
```
