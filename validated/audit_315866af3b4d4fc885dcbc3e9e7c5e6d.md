## Analog Found

### Title
Sending native value with a `DelegationRewards`/`rewards()` call to the distribution precompile permanently strands funds — ([File: precompiles/distribution/distribution.go])

### Summary
The distribution precompile's `Execute` dispatcher forwards the `RewardsMethod` call directly to `p.rewards(ctx, method, args)` without ever validating that `value == 0`, unlike every other query in the same switch statement. Because `rewards()` never receives or checks the `value` parameter, any wei/usei sent alongside the call is neither rejected nor refunded, and becomes permanently unrecoverable — the Solidity-level analog of a contract missing a `receive`/`fallback` handler to deal with unsolicited ETH.

### Finding Description
In `precompiles/distribution/distribution.go`, the `Execute` method-dispatch switch explicitly calls `pcommon.ValidateNonPayable(value)` before invoking state-changing/view handlers such as `WithdrawValidatorCommissionMethod`, and every other query handler (`params`, `validatorOutstandingRewards`, `validatorCommission`, `validatorSlashes`, `delegationRewards`, `delegatorValidators`, `delegatorWithdrawAddress`, `communityPool`) internally calls `p.validateInput(value, args, ...)` which itself calls `pcommon.ValidateNonPayable(value)`: [1](#0-0) 

However, the `RewardsMethod` case is dispatched with only `args`, dropping `value` entirely: [2](#0-1) 

And the `rewards` handler's signature confirms it never receives or inspects `value`: [3](#0-2) 

This same pattern (RewardsMethod dispatched without a value check) is present across the current implementation and essentially every legacy version (v552 through v67), confirming this is a longstanding, systemic gap rather than a one-off regression: [4](#0-3) 

`ValidateNonPayable` is the guard used everywhere else in the precompile framework to reject `value`-bearing calls to non-payable functions: [5](#0-4) 

The test suite itself documents this as a known "guard quirk": `rewards()` is the one phase-2 view without a non-payable check, and a value-bearing call would succeed and strand the funds — which is why the spec deliberately omits a "view rejects value" test for it: [6](#0-5) 

When an EOA or contract sends a transaction to the distribution precompile address (`0x...1007`) calling `rewards(address)` with non-zero `msg.value`, the EVM's native value-transfer semantics move the usei/wei balance from the caller to the precompile's underlying module account as part of normal call execution (the same mechanism used by `bank.sendNative` and other payable precompiles, which explicitly call `HandlePaymentUseiWei`/`HandlePaymentUsei` to refund the payer). Because `rewards()` performs no such refund and the call does not revert (it succeeds and returns the reward query result), the transferred value is left in the distribution module's account with no code path in the precompile to move it back out to the sender.

### Impact Explanation
This is the functional analog of the reported bug (missing `receive`/`fallback` causing native tokens sent to the contract to become permanently stuck): any unprivileged EVM caller who mistakenly (or via a buggy integrating contract) attaches value to a `rewards()` call loses that value irrecoverably through the standard user-facing EVM/precompile call path — a permanent freezing of funds, satisfying the "permanent freezing" criterion.

### Likelihood Explanation
Reachable by any transaction sender or contract issuing a plain EVM call to the distribution precompile's `rewards` method with non-zero value — no special privileges, no governance, no validator/node compromise required. The ABI for `rewards` would need to be checked for whether it is marked `payable`; regardless, the EVM only enforces `payable`/`nonpayable` at the Solidity ABI encoding layer for compiled contracts — direct low-level calls or raw JSON-RPC `eth_sendTransaction` with `value` set bypass that check entirely, and the precompile's own Go-level guard (which every sibling query performs) is the only real enforcement point, and it is missing here.

### Recommendation
Add the same `pcommon.ValidateNonPayable(value)` check to the `RewardsMethod` case in `Execute` (or thread `value` into `rewards()` and validate it there), consistent with every other query handler in `precompiles/distribution/distribution.go`, so that value-bearing calls to `rewards()` revert instead of silently stranding funds.

### Proof of Concept
1. Associate an EVM address with a Sei account and ensure it has a usei balance.
2. Call the distribution precompile at `0x0000000000000000000000000000000000001007`, encoding `rewards(delegatorAddress)`, with `value` set to a non-zero amount (e.g., `1_000_000_000_000` wei / 1 usei).
3. Observe the transaction succeeds and returns the delegator's reward data.
4. Query the sender's bank balance: it has decreased by the sent value, and the distribution module account balance has increased by the same amount, with no way for the sender to reclaim it through any distribution precompile method.

### Citations

**File:** precompiles/distribution/distribution.go (L176-221)
```go
	case WithdrawValidatorCommissionMethod:
		if err = pcommon.ValidateNonPayable(value); err != nil {
			return nil, 0, err
		}
		if readOnly {
			return nil, 0, errors.New("cannot call distr precompile from staticcall")
		}
		return p.withdrawValidatorCommission(ctx, method, caller, evm)
	case GrantWithdrawMethod:
		if readOnly {
			return nil, 0, errors.New("cannot call distr precompile from staticcall")
		}
		return p.grantWithdrawAuthorization(ctx, method, caller, args, value)
	case WithdrawDelegationRewardsWithAuthzMethod:
		if readOnly {
			return nil, 0, errors.New("cannot call distr precompile from staticcall")
		}
		return p.withdrawDelegationRewardsWithAuthorization(ctx, method, caller, args, value, evm)
	case WithdrawValidatorCommissionWithAuthzMethod:
		if readOnly {
			return nil, 0, errors.New("cannot call distr precompile from staticcall")
		}
		return p.withdrawValidatorCommissionWithAuthorization(ctx, method, caller, args, value, evm)
	case RevokeWithdrawMethod:
		if readOnly {
			return nil, 0, errors.New("cannot call distr precompile from staticcall")
		}
		return p.revokeWithdrawAuthorization(ctx, method, caller, args, value)
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

**File:** precompiles/distribution/legacy/v606/distribution.go (L263-299)
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

**File:** precompiles/distribution/legacy/v630/distribution.go (L98-124)
```go
	switch method.Name {
	case SetWithdrawAddressMethod:
		if readOnly {
			return nil, 0, errors.New("cannot call distr precompile from staticcall")
		}
		return p.setWithdrawAddress(ctx, method, caller, args, value, evm)
	case WithdrawDelegationRewardsMethod:
		if readOnly {
			return nil, 0, errors.New("cannot call distr precompile from staticcall")
		}
		return p.withdrawDelegationRewards(ctx, method, caller, args, value, evm)
	case WithdrawMultipleDelegationRewardsMethod:
		if readOnly {
			return nil, 0, errors.New("cannot call distr precompile from staticcall")
		}
		return p.withdrawMultipleDelegationRewards(ctx, method, caller, args, value, evm)
	case WithdrawValidatorCommissionMethod:
		if err = pcommon.ValidateNonPayable(value); err != nil {
			return nil, 0, err
		}
		if readOnly {
			return nil, 0, errors.New("cannot call distr precompile from staticcall")
		}
		return p.withdrawValidatorCommission(ctx, method, caller, evm)
	case RewardsMethod:
		return p.rewards(ctx, method, args)
	}
```

**File:** precompiles/common/legacy/v630/precompiles.go (L198-204)
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
