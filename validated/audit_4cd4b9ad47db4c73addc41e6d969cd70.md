### Title
`rewards()` view in the distribution precompile accepts msg.value and permanently strands sent ETH/usei - (File: precompiles/distribution/distribution.go)

### Summary
The `distribution` precompile at `0x...1007` dispatches all its exposed methods through `PrecompileExecutor.Execute`. Every other query/view method in that dispatcher (`params`, `validatorOutstandingRewards`, `validatorCommission`, `validatorSlashes`, `delegationRewards`, `delegatorValidators`, `delegatorWithdrawAddress`, `communityPool`) calls `p.validateInput(value, args, n)`, which internally invokes `pcommon.ValidateNonPayable(value)` and rejects any non-zero `value`. The `rewards` case, however, is dispatched directly to `p.rewards(ctx, method, args)` without passing `value` at all, so no non-payable check is performed.

### Finding Description
In `Execute`, the dispatch table is: [1](#0-0) 

Note `RewardsMethod` calls `p.rewards(ctx, method, args)` — no `value` argument — while sibling views (e.g. `ParamsMethod`, `ValidatorOutstandingRewardsMethod`) pass `value` through to functions that call `p.validateInput`, which performs `pcommon.ValidateNonPayable(value)`: [2](#0-1) [3](#0-2) 

The `rewards` function itself never checks `value` and simply queries and returns delegation rewards: [4](#0-3) 

Because a Solidity/EVM `CALL` to `rewards(address)` with a non-zero `value` is accepted (the EVM already debited the caller's account and credited the precompile's account balance in the state transition before `Run`/`Execute` is invoked), and the precompile's business logic never returns or accounts for that value, the usei/wei transferred to the precompile address is not refunded to the caller and is not spent for any state change — it becomes permanently stuck at the precompile's account address. This is directly analogous to the reported InfinityExchange bug class: a function that is unintentionally payable (or fails to validate that `msg.value` should be zero) accepts and silently retains user funds sent by mistake.

This is not a hypothetical: this exact quirk is called out explicitly in the precompile's own integration test file, confirming it is a known, reachable, unpatched behavior: [5](#0-4) 

### Impact Explanation
Any unprivileged EVM caller (contract or EOA) that mistakenly sends `usei`/`wei` value along with a call to `rewards(address)` on the distribution precompile will have those funds transferred to the precompile's account with no code path to reclaim them (unlike `HandlePaymentUsei`/`HandlePaymentUseiWei` used elsewhere, which explicitly refund payers for payable precompile calls). This is a direct, permanent loss of user funds — a concrete fund-freezing bug reachable by any transaction sender or contract that interacts with the distribution precompile, matching the "permanent fund freezing" impact class.

### Likelihood Explanation
Likelihood is conditional on user/contract error (sending value to what looks like a view/query call), similar to the original finding which was also rated Medium for the same reason. However, because `rewards()` is a commonly-used read method for checking delegation rewards, and every other similarly-named view method in the exact same precompile does enforce a non-payable check, callers (especially contract developers building wrapper/aggregator contracts that forward `msg.value` generically) could plausibly call it with value by mistake or by copy-pasting patterns from payable methods like `staking.delegate`. The bug is directly reachable via a single external call with no special privileges.

### Recommendation
Add the same non-payable enforcement used elsewhere in the file. Change:
```go
case RewardsMethod:
    return p.rewards(ctx, method, args)
```
to validate `value` first, e.g.:
```go
case RewardsMethod:
    if err = pcommon.ValidateNonPayable(value); err != nil {
        return nil, 0, err
    }
    return p.rewards(ctx, method, args)
```
or update `p.rewards` to accept `value` and call `p.validateInput(value, args, 1)` like the other view functions do, ensuring parity with `validatorOutstandingRewards`, `validatorCommission`, etc.

### Proof of Concept
1. An EVM account (need not be pre-associated for `rewards` since `accAddressFromArg` requires association only for the delegator argument, not for the caller) calls the distribution precompile at `0x0000000000000000000000000000000000001007` invoking `rewards(address delegator)` via `eth_sendRawTransaction`/`eth_call` equivalent, but attaches `value: 1000000000000000000` (1 SEI in wei) to the transaction.
2. The EVM state transition (prior to precompile execution) debits the caller's balance and credits the precompile contract address's balance by the sent value, per normal EVM value-transfer semantics.
3. `Execute` dispatches to `case RewardsMethod: return p.rewards(ctx, method, args)` — `pcommon.ValidateNonPayable(value)` is never called, so the call succeeds and returns the delegation rewards data normally.
4. The transaction completes successfully; the caller's SEI value is now held at the distribution precompile address balance with no corresponding accounting or code path that spends or refunds it, permanently freezing/losing the sent funds — mirroring the exact "ETH mistakenly sent... will be lost" bug class from the referenced report, confirmed as intentional-but-unguarded behavior by the code comment in `distribution.spec.ts` lines 9-11.

### Citations

**File:** precompiles/distribution/distribution.go (L204-222)
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
	}
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

**File:** precompiles/distribution/distribution.go (L670-683)
```go
func (p PrecompileExecutor) validatorOutstandingRewards(ctx sdk.Context, method *abi.Method, args []interface{}, value *big.Int) (ret []byte, remainingGas uint64, rerr error) {
	defer func() {
		if err := recover(); err != nil {
			ret = nil
			remainingGas = 0
			rerr = fmt.Errorf("%s", err)
			return
		}
	}()

	if err := p.validateInput(value, args, 1); err != nil {
		rerr = err
		return
	}
```

**File:** integration_test/precompile_tests/precompiles/distribution.spec.ts (L9-12)
```typescript
 * Guard quirk worth knowing: rewards() is the one phase-2 view WITHOUT a
 * non-payable check (a value-bearing call would succeed and strand the funds),
 * so this spec deliberately has no "view rejects value" test.
 */
```
