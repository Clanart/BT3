### Title
Value sent to the distribution precompile's `rewards` view is stranded with no refund path - (File: precompiles/distribution/distribution.go)

### Summary
The distribution precompile at `0x...1007` rejects payable (`value != 0`) calls for every mutating and query method by invoking `pcommon.ValidateNonPayable(value)` — except for the `rewards` query method, which is dispatched without ever receiving or checking `value`. A caller who sends a value-bearing `CALL` to the precompile invoking `rewards(address)` will have that value credited to the precompile's account balance by the EVM's native value-transfer mechanics, but the precompile logic never consumes, forwards, or refunds it. This exactly mirrors the reported Booster bug class: funds transferred to a contract/account that has no mechanism to move them back out, causing permanent loss.

### Finding Description
`Execute` in the distribution precompile dispatches transaction methods with an explicit `pcommon.ValidateNonPayable(value)` guard before invoking their handlers (e.g. `WithdrawValidatorCommissionMethod`) and other query methods forward `value` to their handlers so it can be checked/refunded via `pcommon.HandlePaymentUsei` where relevant: [1](#0-0) 

The `rewards` case, however, calls `p.rewards(ctx, method, args)` — note the absence of `value` in this call, unlike sibling query handlers such as `p.params(ctx, method, args, value)`: [2](#0-1) 

The `rewards` function itself never inspects `value`, never calls `ValidateNonPayable`, and never refunds anything to the caller; it simply looks up delegation rewards and returns them: [3](#0-2) 

This gap is explicitly acknowledged in the integration test suite's own documentation comment, which states that a value-bearing call to `rewards()` would "succeed and strand the funds," and that the test spec deliberately omits a "view rejects value" test for this method because of it: [4](#0-3) 

The same missing-check pattern (no `ValidateNonPayable`/no refund on the `rewards` view path) exists identically across all versioned copies of the precompile (`legacy/v580`, `v606`, `v614`, `v630`, `v640`, `v65`, `v66`, `v67`), so this is not a one-off regression but a systemic gap in this method across the precompile's history.

Because `0x...1007` is a real address in the EVM state (backed by the usei/wei StateDB bridge), a value-bearing `CALL` will transfer `value` into that address's balance via standard EVM value-transfer semantics before the precompile's `Run`/`Execute` logic ever runs, exactly like a normal contract call. Every other stateful/payable-adjacent precompile function in this file either rejects the value up front (`ValidateNonPayable`) or explicitly processes/refunds it (`HandlePaymentUsei`, seen in the wasmd/bank precompiles elsewhere in the codebase). `rewards` does neither.

### Impact Explanation
Any unprivileged EVM caller (an EOA transaction or a contract's internal `CALL`) can send native value to `0x0000000000000000000000000000000000001007` while invoking `rewards(address)`. That value is credited to the precompile's account balance in the StateDB and there is no code path in the distribution precompile (or elsewhere) that ever moves balance out of the precompile's own account back to a user — the precompile only performs `SendCoins`/`WithdrawDelegationRewards` operations on behalf of delegators, never a self-balance sweep. This causes permanent, irrecoverable loss of the transferred usei/wei for the sender, identical in class to the reported Booster issue where `token.safeTransferFrom(user, address(this), couponPrice)` trapped funds forever with no recovery function.

### Likelihood Explanation
Trivial to trigger: any address can call the `rewards` view on the public JSON-RPC/EVM transaction pipeline with a non-zero `value` field, no special permissions, association, or delegation state required (the only precondition is passing `accAddressFromArg` validation, i.e., using an associated `address` argument, or reverting harmlessly at that point without losing value — but reaching the value-transfer step only requires the EVM to route the call to the precompile at all, which happens prior to the argument-parsing logic in `rewards`). This can happen accidentally (e.g., a wallet/dApp mis-populating a `msg.value` field when calling a "view" function) or be induced deliberately against any user tricked into signing such a transaction.

### Recommendation
Add a `pcommon.ValidateNonPayable(value)` check to `rewards` (and audit all other query/view methods in this precompile family to confirm they consistently receive and check `value`), rejecting any call to `rewards` that carries non-zero value, consistent with every other method in `Execute`. Apply the same fix to all legacy precompile versions that ship this same code path.

### Proof of Concept
1. Associate an EVM address with a Sei address and ensure it has at least one delegation (so `rewards` returns successfully — though even a reverting call in `accAddressFromArg` may still strand value depending on EVM value-transfer timing relative to precompile execution/revert semantics; the core issue is the missing guard regardless).
2. From that EVM address, send a transaction to `0x0000000000000000000000000000000000001007` calling `rewards(address)` with `value` set to a non-zero amount (e.g., 1 SEI worth of wei).
3. Observe that the transaction succeeds (per the test-suite's own comment confirming this behavior) and that the caller's balance decreases by `value`, while the precompile's own account balance increases by the same amount.
4. Confirm there is no method on the distribution precompile, or any other contract, that can move funds out of the precompile's own account — the value is permanently stranded, analogous to Booster's unrecoverable `address(this)` transfers.

### Citations

**File:** precompiles/distribution/distribution.go (L176-223)
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
	}
	return
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
