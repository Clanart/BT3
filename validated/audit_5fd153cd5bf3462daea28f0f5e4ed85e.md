## Finding

### Title
Value sent to the distribution precompile's `rewards()` query method is permanently locked — ([File: precompiles/distribution/distribution.go])

### Summary
The Sei EVM distribution precompile at address `0x...1007` allows an unprivileged EVM caller to attach native `msg.value` to a call of the `rewards(address)` query method. Unlike every other query/view method on the same precompile, `rewards()` never validates that `value` is zero and never refunds it, so the attached funds are credited to the precompile's derived Sei account and can never be withdrawn — the same "deposit-with-no-withdraw" bug class described in the external report for `Entropy.sol`/`Executor.sol`.

### Finding Description
The precompile dispatcher `Execute` in `precompiles/distribution/distribution.go` routes calls by method name: [1](#0-0) 

Every other query method (`params`, `validatorOutstandingRewards`, `validatorCommission`, `validatorSlashes`, `delegationRewards`, `delegatorValidators`, `delegatorWithdrawAddress`, `communityPool`) is passed `value` and internally calls `p.validateInput(value, args, N)`, which in turn calls `pcommon.ValidateNonPayable(value)` and reverts if any value is attached: [2](#0-1) [3](#0-2) 

`RewardsMethod`, however, is dispatched as `p.rewards(ctx, method, args)` — `value` is not even passed to the function, and `rewards()` performs no non-payable check anywhere in its body: [4](#0-3) 

This is explicitly called out as a known quirk in the precompile's own integration test suite: [5](#0-4) 

Because the precompile runs inside the normal EVM `CALL` path (not `STATICCALL`), an EVM value transfer to the precompile address is performed by the EVM before/around invoking `Precompile.Run`/`DynamicGasPrecompile.RunAndCalculateGas`: [6](#0-5) 

That value transfer is realized through the StateDB balance hooks, which move `usei`/`wei` into the bank balance of the Sei address associated with the precompile (via `AddBalance`/bank keeper `AddCoins`/`AddWei`): [7](#0-6) 

For every payable-rejecting method, this transfer is undone at the SDK/application level because the executor's `pcommon.HandlePaymentUsei` (used for the payable methods that do accept funds) or the non-payable rejection causes the whole EVM call to revert, rolling back the balance change atomically within the same transaction. `rewards()` neither rejects the value nor refunds it via `HandlePaymentUsei`, so the call succeeds, the state transition is not reverted, and the transferred `usei`/`wei` remains permanently credited to the precompile's derived account. There is no bank message, precompile method, or governance-only withdraw path that lets this be reclaimed — the distribution module account is only debited by keeper logic tied to specific delegator/validator withdrawal flows, none of which apply to an arbitrary EVM caller's stray deposit sitting on the precompile's own associated address.

### Impact Explanation
Any unprivileged EVM transaction sender can call `rewards(address)` on the distribution precompile with a non-zero `value` and have that value silently accepted and stranded, with no mechanism inside the protocol to move it back out. This is a permanent, protocol-level loss of user funds triggerable by a single ordinary transaction — matching the "concrete fund loss / permanent freezing" bar for validity.

### Likelihood Explanation
Trivial to trigger: any address (no association, no special permissions) can send an EVM transaction calling `rewards(address)` with `value > 0` on the known precompile address `0x...1007`. No race conditions, validator cooperation, or governance action is required.

### Recommendation
Add the same non-payable guard used by every other distribution-precompile view method to `RewardsMethod`/`rewards()` — i.e., pass `value` through and call `pcommon.ValidateNonPayable(value)` (or `p.validateInput`) before executing the query, so that a value-bearing call reverts instead of stranding funds. As a defense-in-depth measure, audit all other precompiles for query/view methods that omit the `ValidateNonPayable` check that their sibling methods apply.

### Proof of Concept
1. From any funded, unassociated EOA, submit an EVM transaction calling the distribution precompile at its known address with selector for `rewards(address)` and `value: 1 ether` (or any positive wei/usei amount), e.g. via `ethers.Contract.rewards(anyAddress, { value: ... })`.
2. Observe the transaction succeeds (status 1) rather than reverting, unlike calling any other view method (`params`, `validatorCommission`, etc.) with the same non-zero value, which reverts with "sending funds to a non-payable function".
3. Query the bank balance of the Sei account associated with the distribution precompile address before and after the call — the balance increases by the sent amount and has no corresponding decrease; no subsequent call (withdraw, claim, governance) can move that balance back to any user.

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

**File:** precompiles/distribution/distribution.go (L637-650)
```go
func (p PrecompileExecutor) params(ctx sdk.Context, method *abi.Method, args []interface{}, value *big.Int) (ret []byte, remainingGas uint64, rerr error) {
	defer func() {
		if err := recover(); err != nil {
			ret = nil
			remainingGas = 0
			rerr = fmt.Errorf("%s", err)
			return
		}
	}()

	if err := p.validateInput(value, args, 0); err != nil {
		rerr = err
		return
	}
```

**File:** precompiles/distribution/legacy/v614/distribution.go (L181-191)
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

**File:** integration_test/precompile_tests/precompiles/distribution.spec.ts (L9-11)
```typescript
 * Guard quirk worth knowing: rewards() is the one phase-2 view WITHOUT a
 * non-payable check (a value-bearing call would succeed and strand the funds),
 * so this spec deliberately has no "view rejects value" test.
```

**File:** precompiles/common/precompiles.go (L156-215)
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
}
```

**File:** x/evm/state/balance.go (L64-107)
```go
func (s *DBImpl) AddBalance(evmAddr common.Address, amtUint256 *uint256.Int, reason tracing.BalanceChangeReason) uint256.Int {
	s.k.PrepareReplayedAddr(s.ctx, evmAddr)
	amt := amtUint256.ToBig()
	if amt.Sign() == 0 {
		return *ZeroInt
	}
	if amt.Sign() < 0 {
		return s.SubBalance(evmAddr, new(uint256.Int).Neg(amtUint256), reason)
	}

	ctx := s.ctx
	var oldBalance *uint256.Int
	if s.logger != nil && s.logger.OnBalanceChange != nil {
		oldBalance = s.GetBalance(evmAddr)
	}
	// this avoids emitting cosmos events for ephemeral bookkeeping transfers like send_native
	if s.eventsSuppressed {
		ctx = ctx.WithEventManager(sdk.NewEventManager())
	}

	usei, wei := SplitUseiWeiAmount(amt)
	addr := s.getSeiAddress(evmAddr)
	err := s.k.BankKeeper().AddCoins(ctx, addr, sdk.NewCoins(sdk.NewCoin(s.k.GetBaseDenom(s.ctx), usei)), true)
	if err != nil {
		s.err = err
		return *ZeroInt
	}
	err = s.k.BankKeeper().AddWei(ctx, addr, wei)
	if err != nil {
		s.err = err
		return *ZeroInt
	}

	if s.logger != nil && s.logger.OnBalanceChange != nil && oldBalance != nil {
		newBalance := s.GetBalance(evmAddr).ToBig()
		oldBalance := new(big.Int).Sub(newBalance, amt)
		s.logger.OnBalanceChange(evmAddr, oldBalance, newBalance, reason)
	}

	surplus := sdk.NewIntFromBigInt(amt).Neg()
	s.tempState.surplus = s.tempState.surplus.Add(surplus)
	s.journal = append(s.journal, &surplusChange{delta: surplus})
	return *ZeroInt
}
```
