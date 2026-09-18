Confirmed: `rewards()` in `precompiles/distribution/distribution.go` (line 543-579, dispatched at line 204-205) has no `pcommon.ValidateNonPayable(value)` call, unlike every other view method in this file (`params`, `validatorOutstandingRewards`, `validatorCommission`, `validatorSlashes`, `delegationRewards`, `delegatorValidators`, `delegatorWithdrawAddress`, `communityPool` — all call `p.validateInput(value, args, N)` which internally calls `ValidateNonPayable`). This matches the reported bug class: value sent to a target that has no logic to route/refund it, permanently stranding the SEI.

### Title
`rewards()` distribution precompile accepts value with no non-payable guard, permanently stranding sent SEI - (File: precompiles/distribution/distribution.go)

### Summary
The `rewards` view method of the distribution precompile at `0x0000000000000000000000000000000000001007` is dispatched without the `pcommon.ValidateNonPayable(value)` check that every sibling view method in the same file enforces, allowing a caller to attach `msg.value` to a call that performs no bank transfer or refund of that value.

### Finding Description
In `precompiles/distribution/distribution.go`, `Execute` dispatches `RewardsMethod` directly to `p.rewards(ctx, method, args)` without validating `value`: [1](#0-0) . Every other read-only method in the same switch validates non-payability through `p.validateInput`, which calls `pcommon.ValidateNonPayable(value)`: [2](#0-1) . The `rewards` function itself only validates argument length and never touches `value`: [3](#0-2) .

The precompile framework subtracts the transferred value from the caller's EVM balance and credits it to the precompile's address (`0x...1007`) via the standard EVM value-transfer mechanics before dispatching to `Execute`, as is standard for all Sei precompiles that accept value (e.g. `sendNative` in the bank precompile explicitly consumes and forwards `value` via `bankKeeper.SendCoinsAndWei`: [4](#0-3) ). Since `rewards()` neither rejects the value nor forwards/refunds it, any usei sent along with a `rewards(address)` call is transferred into the distribution precompile's associated account balance with no code path in the precompile that ever sweeps, refunds, or otherwise uses that balance — the same "no way to move it out" impact for which the Tokensoft `Sweepable.sol` report was flagged. This precompile has no equivalent of `Sweepable`'s sweep function, so funds sent this way are permanently unrecoverable through the contract's exposed methods.

This is corroborated by the repo's own integration-test comment, which flags this as a known quirk: [5](#0-4) .

### Impact Explanation
Any unprivileged EVM caller (or a contract auto-calling `rewards()` incorrectly, e.g. due to ABI/tooling mistakes) that attaches non-zero `value` to a call to the distribution precompile's `rewards` method permanently loses that value — it is credited to the precompile address with no mechanism in the codebase to withdraw, sweep, or refund it. This is a direct, irreversible loss of user funds triggered by a single externally-submitted transaction, meeting the "permanent freezing"/"concrete fund loss" bar.

### Likelihood Explanation
Likelihood is driven entirely by user/tooling error rather than active exploitation, since there's no direct benefit to an attacker (the funds go to a fixed precompile address with no benefit to anyone, including the caller). However, since every sibling method on the same contract rejects value with a clear revert, developers/wallets may reasonably expect `rewards()` to also reject value and could accidentally attach `msg.value` (e.g. copy-pasting a template that sends value to a similarly named "claim"/"reward" function elsewhere). The bar to trigger is a single standard transaction with no special privileges.

### Recommendation
Add `if err := pcommon.ValidateNonPayable(value); err != nil { return nil, 0, err }` at the top of `rewards()` (or route it through `p.validateInput(value, args, 1)` like the other view methods), so that a value-bearing call to `rewards()` reverts consistently with the rest of the precompile's view surface, matching the pattern already used for `params`, `validatorOutstandingRewards`, `validatorCommission`, `validatorSlashes`, `delegationRewards`, `delegatorValidators`, `delegatorWithdrawAddress`, and `communityPool`.

### Proof of Concept
1. Call `distribution.rewards(delegatorAddress)` at `0x0000000000000000000000000000000000001007` via a standard EVM transaction, attaching `value: 1 SEI` (or any nonzero wei/usei amount) and `gasLimit` sufficient for the query.
2. The transaction succeeds (status = 1), the reward data is returned normally, and the caller's EVM balance is debited by the attached value, exactly as demonstrated for the sibling `params`/`validatorOutstandingRewards`/etc. methods' negative test case in `precompiles/staking/staking_test.go` (lines 767-776), except here no revert occurs.
3. Query the bank/EVM balance of the precompile's associated Sei address afterward — the value is present there permanently, with no `rewards`, `withdraw*`, or other method in `precompiles/distribution/distribution.go` capable of transferring it back out.

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

**File:** precompiles/bank/bank.go (L280-287)
```go
	usei, wei, err := pcommon.HandlePaymentUseiWei(ctx, p.evmKeeper.GetSeiAddressOrDefault(ctx, p.address), senderSeiAddr, value, p.bankKeeper, p.evmKeeper, hooks, evm.GetDepth())
	if err != nil {
		return nil, 0, err
	}

	if err := p.bankKeeper.SendCoinsAndWei(ctx, senderSeiAddr, receiverSeiAddr, usei, wei); err != nil {
		return nil, 0, err
	}
```

**File:** integration_test/precompile_tests/precompiles/distribution.spec.ts (L9-11)
```typescript
 * Guard quirk worth knowing: rewards() is the one phase-2 view WITHOUT a
 * non-payable check (a value-bearing call would succeed and strand the funds),
 * so this spec deliberately has no "view rejects value" test.
```
