Confirmed: `rewards()` in the distribution precompile is called via `p.rewards(ctx, method, args)` at [1](#0-0)  with no `value` parameter accepted at all and, critically, no call to `pcommon.ValidateNonPayable(value)` inside `rewards`, unlike every other query method in the same file (`params`, `validatorOutstandingRewards`, `validatorCommission`, `validatorSlashes`, `delegationRewards`, `delegatorValidators`, `delegatorWithdrawAddress`, `communityPool`) which all call `p.validateInput(value, args, n)` → `pcommon.ValidateNonPayable(value)` at [2](#0-1)  and [3](#0-2) .

### Title
Distribution precompile `rewards()` view lacks non-payable guard, permanently stranding native SEI sent with the call - (File: `precompiles/distribution/distribution.go`)

### Summary
The `rewards` method of the distribution precompile (`0x0000000000000000000000000000000000001007`) is declared payable in the ABI but its Go handler never validates or consumes `msg.value`, and never refunds it. Any `wei`/`usei` sent alongside a call to `rewards()` is transferred to the precompile's account by the EVM value-transfer semantics but is never routed back to the caller, permanently locking those funds — the same root-cause pattern as the reported Allo `_allocate` bug (unconditionally accepting/forwarding value with no validation for functions that don't expect it).

### Finding Description
`Execute` dispatches `RewardsMethod` directly to `p.rewards(ctx, method, args)` without passing `value` and without any non-payable check, unlike every sibling query method: [4](#0-3) .

Compare to `params`, `validatorOutstandingRewards`, etc., which all funnel through `validateInput`, which explicitly rejects non-zero value via `pcommon.ValidateNonPayable`: [2](#0-1) . `ValidateNonPayable` is the standard guard used across all Sei precompiles to reject stray `msg.value` on functions that are not designed to take payment: [5](#0-4) .

`rewards` itself just queries `DelegationTotalRewards` and packs the response with no handling of `value` whatsoever: [3](#0-2) .

Because the top-level EVM call mechanics transfer `msg.value` into the precompile's account balance before/independent of the Go executor logic (as happens generically for any precompile call carrying value), and `rewards()` neither validates nor refunds it, any wei sent lands in the precompile account with no code path to reclaim it. This is exactly the class of bug described in the external report: a function that is reachable with `msg.value > 0` but has no logic to account for, forward, or reject that value, resulting in permanently stranded funds. The repo's own integration test suite documents this exact gap in a comment: "rewards() is the one phase-2 view WITHOUT a non-payable check (a value-bearing call would succeed and strand the funds)" — see [6](#0-5) .

### Impact Explanation
Any unprivileged EVM caller (EOA or contract) who mistakenly (or is tricked into, e.g. via a wrapper/router contract) sending native SEI value along with a `rewards()` call to the distribution precompile will have those funds irretrievably stuck at the precompile's account address. There is no withdraw/sweep function reachable by users for that balance, so this is a permanent freezing of user funds — a direct fund-loss scenario matching the "permanent freezing of funds" acceptance criterion.

### Likelihood Explanation
Likelihood is moderate: `rewards` is a commonly called view function (used by wallets, dashboards, and dApp integrations) so accidental value-attachment via a poorly-configured caller contract or SDK misuse is plausible, and any such call irreversibly loses funds with no revert to signal the mistake (since it's a pure view that succeeds regardless of `value`). This mirrors the original Sherlock finding's severity classification of Medium for the identical bug class.

### Recommendation
Add a `pcommon.ValidateNonPayable(value)` check at the start of `rewards` (mirroring `validateInput` used by all other query methods), and update the dispatcher to pass `value` into `p.rewards(...)` so the check is enforced, causing the call to revert if `msg.value != 0` instead of silently stranding the funds.

### Proof of Concept
1. Call the distribution precompile at `0x...1007` with function selector for `rewards(address)` and a non-zero `value` (e.g. `distribution.rewards(delegatorAddr, {value: ethers.parseEther("1")})`).
2. The call succeeds and returns valid reward data (since `rewards` never checks `value`).
3. Query the precompile's on-chain/module balance — the sent `usei`/`wei` remains in the precompile account with no method to reclaim it, unlike other query methods (e.g. `params`) which would instead revert with "sending funds to a non-payable function".

### Citations

**File:** precompiles/distribution/distribution.go (L204-221)
```go
	case RewardsMethod:
		return p.rewards(ctx, method, args)
	case ParamsMethod:
		return p.params(ctx, method, args, value)
	case ValidatorOutstandingRewardsMethod:
		return p.validatorOutstandingRewards(ctx, method, args, value)
	case ValidatorCommissionMethod:
		return p.validatorCommission(ctx, method, args, value)
	case ValidatorSlashesMethod:
		return p.validatorSlashes(ctx, method, args, value)
	case DelegationRewardsMethod:
		return p.delegationRewards(ctx, method, args, value)
	case DelegatorValidatorsMethod:
		return p.delegatorValidators(ctx, method, args, value)
	case DelegatorWithdrawAddressMethod:
		return p.delegatorWithdrawAddress(ctx, method, args, value)
	case CommunityPoolMethod:
		return p.communityPool(ctx, method, args, value)
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

**File:** precompiles/common/precompiles.go (L261-267)
```go
func ValidateNonPayable(value *big.Int) error {
	if value != nil && value.Sign() != 0 {
		return errors.New("sending funds to a non-payable function")
	}

	return nil
}
```

**File:** integration_test/precompile_tests/precompiles/distribution.spec.ts (L1-12)
```typescript
/**
 * distribution precompile (0x…1007) — end-to-end semantics against a live Sei chain.
 *
 * Fixture: a pool account delegates via the staking precompile, then rewards
 * accrue per block. Reward amounts can never be asserted as exact equality
 * across blocks — the withdrawal test instead pins the bank-balance delta to
 * the amount decoded from the tx's own DelegationRewardsWithdrawn log.
 *
 * Guard quirk worth knowing: rewards() is the one phase-2 view WITHOUT a
 * non-payable check (a value-bearing call would succeed and strand the funds),
 * so this spec deliberately has no "view rejects value" test.
 */
```
