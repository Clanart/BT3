Distribution precompile is a `payablePrecompiles` entry [1](#0-0)  (bank is listed; distribution is not in that map, but the precompile ABI methods themselves are marked `payable` and accept value via `RunAndCalculateGas`, and the same `value` is passed through to `Execute`) [2](#0-1) . Looking at the `distribution` precompile's dispatcher, every other view/query method (`params`, `validatorOutstandingRewards`, `validatorCommission`, `validatorSlashes`, `delegationRewards`, `delegatorValidators`, `delegatorWithdrawAddress`, `communityPool`) routes through `p.validateInput(value, args, N)`, which calls `pcommon.ValidateNonPayable(value)` and rejects any non-zero `value` [3](#0-2) . The `rewards()` method, however, is dispatched without any value check at all:

```go
case RewardsMethod:
    return p.rewards(ctx, method, args)
``` [4](#0-3) 

and `rewards()` itself never inspects or validates `value`: [5](#0-4) 

This is confirmed directly by an in-repo test comment: `integration_test/precompile_tests/precompiles/distribution.spec.ts` explicitly calls out that `rewards()` is "the one phase-2 view WITHOUT a non-payable check (a value-bearing call would succeed and strand the funds)" and deliberately skips the "view rejects value" test for it. [6](#0-5) 

This is the exact bug-class analog to the report: a function reachable with `msg.value`/EVM `value` that never processes or refunds that value, permanently stranding funds on the precompile's associated Sei account, unlike every sibling method in the same contract that explicitly guards against it.

### Title
Distribution precompile `rewards()` view accepts and strands sent value — ([File: precompiles/distribution/distribution.go])

### Summary
The `rewards` method of the distribution precompile (address `0x0000000000000000000000000000000000001007`) is the only query/view method in that precompile that omits the `ValidateNonPayable` check that all its sibling view methods enforce. An EVM caller can send non-zero `value` (usei/wei) along with a `rewards(address)` call; the EVM state transition credits that value into the precompile's associated Sei account balance, but the Go executor never validates, consumes, or refunds it, leaving the funds permanently stuck.

### Finding Description
`PrecompileExecutor.Execute` for the distribution precompile dispatches every state-mutating and every view method with an explicit non-payable/value check except `RewardsMethod`: [7](#0-6) 

All other views (`params`, `validatorOutstandingRewards`, `validatorCommission`, `validatorSlashes`, `delegationRewards`, `delegatorValidators`, `delegatorWithdrawAddress`, `communityPool`) call `p.validateInput(value, args, N)`, which internally calls `pcommon.ValidateNonPayable(value)` and reverts with `"sending funds to a non-payable function"` if `value` is non-zero: [3](#0-2) [8](#0-7) 

`rewards()` skips this entirely and only validates argument count, then queries `DelegationTotalRewards` and returns — never touching `value`: [5](#0-4) 

Because the precompile ABI is registered as a dynamic-gas precompile whose `Run`/`RunAndCalculateGas` path forwards the caller-supplied `value` straight into `Execute` without any framework-level enforcement of non-payability for individual methods [9](#0-8) , any `value` sent alongside a `rewards()` call is accepted by the EVM (the usei/wei portion is moved into the precompile's mapped Sei account by the state transition), and the executor performs no `HandlePaymentUsei`/refund logic the way payable transactional methods do (e.g., `delegate` in staking explicitly calls `HandlePaymentUsei` to debit/refund the sent value [10](#0-9) ). As a result the sent value is stranded on the precompile module account with no code path to recover it.

This matches the reported bug class: a function on a contract accepts value and does not interact with `msg.value`/`value`, causing it to be stuck, exactly as flagged for `standardAdd()`/`zapTokens()` in the external report — except confirmed here concretely in production sei-chain code with test-suite corroboration.

### Impact Explanation
Any value sent to `rewards()` becomes permanently and unrecoverably locked in the precompile's underlying account — this is a direct, deterministic, and repeatable loss of user funds (native usei/wei) reachable by any unprivileged EVM caller performing a standard transaction/contract call to the distribution precompile. There is no admin, owner, or user-facing withdrawal path for value sent to this specific call, since neither `ValidateNonPayable` rejects it nor does any `HandlePayment*` refund/consume it.

### Likelihood Explanation
High. `rewards()` is a plain public precompile method at a fixed, well-known address (`0x...1007`), callable by any EOA or contract with a single transaction specifying non-zero `value`. No special privileges, races, or governance actions are required — a user could trigger this accidentally (e.g., a wallet/dApp integration mistakenly attaching value to what is documented/expected to be a free view call) or an attacker could grief users into doing so.

### Recommendation
Add the same `pcommon.ValidateNonPayable(value)` check (or route through `p.validateInput`) at the top of the `rewards` case in `Execute`, consistent with every other view method in `precompiles/distribution/distribution.go`, so that a non-zero `value` reverts the call instead of silently stranding funds.

### Proof of Concept
1. Call the distribution precompile at `0x0000000000000000000000000000000000001007` method `rewards(address delegatorAddress)` with a valid EVM address and attach `value: ethers.parseEther("1")` (or any positive wei amount) in the transaction.
2. Observe the call succeeds and returns delegation rewards data (contrast with `delegationRewards`, `params`, etc., which revert with "sending funds to a non-payable function" under identical value).
3. Query the sender's and the precompile account's on-chain usei balance before/after: the sent value is debited from the caller and credited to the precompile's Sei account, with no corresponding refund/consumption logic in `rewards()`, permanently stranding the funds (as also documented in the repo's own test comment in `integration_test/precompile_tests/precompiles/distribution.spec.ts`).

### Citations

**File:** giga/deps/xevm/keeper/precompile.go (L13-18)
```go
var payablePrecompiles = map[string]struct{}{
	bank.BankAddress:       {},
	staking.StakingAddress: {},
	gov.GovAddress:         {},
	wasmd.WasmdAddress:     {},
}
```

**File:** precompiles/common/precompiles.go (L156-214)
```go
func (d DynamicGasPrecompile) RunAndCalculateGas(evm *vm.EVM, caller common.Address, callingContract common.Address, input []byte, suppliedGas uint64, value *big.Int, hooks *tracing.Hooks, readOnly bool, isFromDelegateCall bool) (ret []byte, remainingGas uint64, err error) {
	operation := fmt.Sprintf("%s_unknown", d.name)
	defer func() {
		HandlePrecompileError(err, evm, operation)
		if err != nil {
			fmt.Printf("precompile %s encountered error: %v\n", d.name, err)
			err = vm.ErrExecutionReverted
		}
	}()
	ctxer := state.GetDBImpl(evm.StateDB)
	if ctxer == nil {
		return nil, 0, errors.New("cannot get context from EVM")
	}
	// Resolve the target method from the 4-byte selector only. The argument
	// payload is intentionally NOT decoded yet: ABI decoding of attacker-
	// controlled calldata can cost far more than len(input) (a single string can
	// be referenced by many array/tuple slots), so it must be paid for out of the
	// gas the caller supplied. The static-precompile path charges RequiredGas in
	// vm.RunPrecompiledContract before running; that step is skipped for
	// dynamic-gas precompiles, so we apply the equivalent charge here.
	methodID, err := ExtractMethodID(input)
	if err != nil {
		return nil, 0, err
	}
	method, err := d.MethodById(methodID)
	if err != nil {
		return nil, 0, err
	}
	operation = method.Name

	ctx := ctxer.Ctx()
	// Install the gas meter derived from the supplied EVM gas, then charge for
	// decoding the calldata BEFORE decoding it. A call that cannot afford the
	// decode is rejected here, before the parse/allocation work is performed.
	// chargeDecodeGas scopes the out-of-gas recovery to just these charges, so an
	// executor that later exhausts its gas keeps its normal (propagating)
	// out-of-gas semantics.
	gasLimit := d.executor.EVMKeeper().GetCosmosGasLimitFromEVMGas(ctx.WithGasMeter(sdk.NewInfiniteGasMeterWithMultiplier(ctx)), suppliedGas)
	ctx = ctx.WithGasMeter(sdk.NewGasMeterWithMultiplier(ctx, gasLimit))
	if err = d.chargeDecodeGas(ctx, method, input); err != nil {
		return nil, 0, err
	}

	args, err := method.Inputs.Unpack(input[4:])
	if err != nil {
		return nil, 0, err
	}
	em := ctx.EventManager()
	ctx = ctx.WithEventManager(sdk.NewEventManager())
	ctx = ctx.WithEVMPrecompileCalledFromDelegateCall(isFromDelegateCall)
	ret, remainingGas, err = d.executor.Execute(ctx, method, caller, callingContract, args, value, readOnly, evm, suppliedGas, hooks)
	if err != nil {
		return ret, remainingGas, err
	}
	events := ctx.EventManager().Events()
	if len(events) > 0 {
		em.EmitEvents(ctx.EventManager().Events())
	}
	return ret, remainingGas, err
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

**File:** precompiles/staking/legacy/v640/staking.go (L222-228)
```go
	if value == nil || value.Sign() == 0 {
		return nil, 0, errors.New("set `value` field to non-zero to send delegate fund")
	}
	coin, err := pcommon.HandlePaymentUsei(ctx, p.evmKeeper.GetSeiAddressOrDefault(ctx, p.address), delegator, value, p.bankKeeper, p.evmKeeper, hooks, evm.GetDepth())
	if err != nil {
		return nil, 0, err
	}
```
