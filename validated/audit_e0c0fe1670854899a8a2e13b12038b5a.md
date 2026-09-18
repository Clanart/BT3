## Title
Sending SEI value to the `rewards()` view method of the distribution precompile permanently strands funds - (File: `precompiles/distribution/distribution.go`)

### Summary
The distribution precompile's `rewards(address)` query method is missing the non-payable check (`pcommon.ValidateNonPayable`) that every other view/query method in the same file enforces. Because EVM `CALL` semantics transfer `msg.value` from the caller to the target address (here the fixed precompile address `0x1007`) before the precompile logic even runs, any user who calls `rewards()` with a non-zero `value` has their usei/wei balance moved to the precompile address, where it is never retrievable — there is no code path anywhere that spends or refunds a balance held at a precompile address.

### Finding Description
In `precompiles/distribution/distribution.go`, the dispatcher `Execute` routes `RewardsMethod` directly to `p.rewards(ctx, method, args)` without a value check: [1](#0-0) 

Contrast this with every sibling view method (`params`, `validatorOutstandingRewards`, `validatorCommission`, `validatorSlashes`, `delegationRewards`, `delegatorValidators`, `delegatorWithdrawAddress`, `communityPool`), all of which call `p.validateInput(value, args, N)`, and `validateInput` explicitly rejects non-zero `value` via `pcommon.ValidateNonPayable`: [2](#0-1) 

The `rewards` implementation itself only validates argument length, never the value, then just performs a read-only query and returns: [3](#0-2) 

This is not merely a theoretical audit-style concern: the integration test suite for the distribution precompile explicitly documents this exact gap as a known quirk: [4](#0-3) 

This is the direct analog of the reported bug class — a `payable`-reachable entry point with no corresponding withdrawal mechanism, causing any value sent to it to become permanently stuck. Here the "receive() external payable" equivalent is a precompile method invoked over a standard EVM `CALL` with non-zero value; the Sei EVM's `Precompile.Run` / `DynamicGasPrecompile.RunAndCalculateGas` wrappers pass `value` through to `Execute` unconditionally when the specific method omits the check: [5](#0-4) 

Since `0x0000000000000000000000000000000000001007` is a precompile address, not a contract with withdraw logic, any usei/wei balance credited to it via the EVM's native value-transfer step during the `CALL` can never be moved back out by any user, admin, or governance action — there is no `withdraw`, `sweep`, or refund path targeting precompile addresses anywhere in the module.

### Impact Explanation
Any unprivileged EVM transaction sender who (mistakenly, via a buggy front-end/wallet integration, or by fat-fingering a non-zero `value` field) calls `rewards(address)` on the distribution precompile with `value > 0` will have that value permanently and irrecoverably locked at the precompile address. This is a genuine, concrete permanent freezing of user funds reachable by a single ordinary transaction, matching the Medium severity fund-freezing criteria.

### Likelihood Explanation
`rewards()` is a public, unauthenticated view-style method on a well-known, documented precompile address that any dApp or wallet integration might call while also attaching value (e.g. due to a copy-pasted call template that includes `{value: ...}`, or a user manually specifying `value` in `eth_call`/`eth_sendTransaction`). Because every sibling method on the same precompile rejects non-zero value, users/integrators have no reason to expect this one method behaves differently, increasing the chance of accidental value-bearing calls slipping through.

### Recommendation
Add the missing `pcommon.ValidateNonPayable(value)` check to `rewards()` (or route it through `p.validateInput` like its siblings) so that a value-bearing call reverts before any EVM-level balance transfer occurs, consistent with the rest of the distribution precompile's view methods. As a defense-in-depth measure, consider auditing all other precompiles (staking, gov, bank, slashing, addr, pointer) for the same asymmetric-payable-check pattern to ensure no other view/query method omits the non-payable guard.

### Proof of Concept
1. Associate an EVM account with a Sei address and fund it with usei.
2. Call the distribution precompile at `0x0000000000000000000000000000000000001007`, encoding `rewards(address)` with the caller's own address as argument, and attach `value` (e.g. `1000000000000` wei / `1 usei`) to the transaction.
3. Observe the transaction succeeds (status = 1) and the reward data is returned normally, because `rewards()` performs no `ValidateNonPayable` check unlike other view methods (compare to `params`/`validatorCommission`/etc., which revert on `value != 0`).
4. Query the caller's EVM balance: it has decreased by the sent value. There is no bank query, precompile method, or governance action that can move funds out of address `0x1007`, so the sent value is permanently unrecoverable.

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

**File:** precompiles/common/precompiles.go (L64-90)
```go
func (p Precompile) Run(evm *vm.EVM, caller common.Address, callingContract common.Address, input []byte, value *big.Int, readOnly bool, isFromDelegateCall bool, hooks *tracing.Hooks) (bz []byte, err error) {
	operation := fmt.Sprintf("%s_unknown", p.name)
	defer func() {
		HandlePrecompileError(err, evm, operation)
		if err != nil {
			err = vm.ErrExecutionReverted
		}
	}()
	ctx, method, args, err := p.Prepare(evm, input)
	if err != nil {
		return nil, err
	}

	operation = method.Name
	em := ctx.EventManager()
	ctx = ctx.WithEventManager(sdk.NewEventManager())
	ctx = ctx.WithEVMPrecompileCalledFromDelegateCall(isFromDelegateCall)
	bz, err = p.executor.Execute(ctx, method, caller, callingContract, args, value, readOnly, evm, hooks)
	if err != nil {
		return bz, err
	}
	events := ctx.EventManager().Events()
	if len(events) > 0 {
		em.EmitEvents(ctx.EventManager().Events())
	}
	return bz, err
}
```
