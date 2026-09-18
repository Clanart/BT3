### Title
Value sent to the non-payable `rewards()` view method of the distribution precompile is silently accepted and permanently stranded - ([File: precompiles/distribution/distribution.go])

### Summary
The distribution precompile (`0x0000000000000000000000000000000000001007`) exposes a `rewards(address)` view method that, unlike every other entry point in the precompile, does not validate that the call is non-payable. A caller can attach a nonzero `msg.value` to a `rewards()` call; the EVM-level value transfer to the precompile's associated account completes, the query succeeds, and the transferred `usei`/`wei` has no code path back to the sender or any recipient, permanently freezing the funds.

### Finding Description
`PrecompileExecutor.Execute` dispatches every payable/transaction method (`setWithdrawAddress`, `withdrawDelegationRewards`, `withdrawMultipleDelegationRewards`, `grantWithdrawAuthorization`, etc.) and enforces `pcommon.ValidateNonPayable(value)` for `WithdrawValidatorCommissionMethod`, and each state-changing handler is expected to reject or explicitly refund attached value via `pcommon.HandlePaymentUsei` [1](#0-0) . However, the `RewardsMethod` branch calls `p.rewards(ctx, method, args)` directly with no `ValidateNonPayable` check and no value handling at all [2](#0-1) .

The integration test suite explicitly documents this as a known quirk: "rewards() is the one phase-2 view WITHOUT a non-payable check (a value-bearing call would succeed and strand the funds)" and deliberately omits a "view rejects value" test for it [3](#0-2) .

Because Go-Ethereum's EVM performs the native value transfer for any `CALL` with nonzero value before invoking the target's code (precompile or otherwise), and the dynamic-gas precompile dispatch (`RunAndCalculateGas` → `Execute`) only reverts on a returned `error` [4](#0-3) , a successful (non-error) `rewards()` call leaves the transferred value credited to the precompile's backing account with no `withdraw`/`sweep` mechanism to recover it — directly analogous to the reported AeraVault issue where funds land in a contract (`MerkleOrchard`) with no implemented claim path, remaining stuck indefinitely.

### Impact Explanation
Any unprivileged EVM caller (an ordinary tx sender or another smart contract) that sends `usei`/`wei` value alongside a call to `rewards(address)` on the distribution precompile permanently loses those funds — they are neither returned to the sender nor available for withdrawal by anyone, and no protocol code path exists that later sweeps or attributes this balance. This is a permanent freezing/loss of funds triggered by a normal, unprivileged transaction, satisfying the "concrete fund loss or permanent freezing" bar.

### Likelihood Explanation
The bug is trivially reachable: it requires only crafting an EVM call to a well-known, fixed precompile address (`0x1007`) with the `rewards(address)` selector and a nonzero `value` field — no special privileges, contract deployment, or wasm interaction needed. It could also be triggered accidentally by a poorly-written client/wallet/contract that always forwards `msg.value` to any external call.

### Recommendation
Add `pcommon.ValidateNonPayable(value)` at the top of the `RewardsMethod` case in `Execute` (mirroring `WithdrawValidatorCommissionMethod` and other view/query methods), or route it through the same value-guard used elsewhere, so a value-bearing call to `rewards()` reverts and the automatic EVM value transfer is rolled back instead of stranding funds. Audit all other read-only methods in `Execute` (`params`, `validatorOutstandingRewards`, `validatorCommission`, `validatorSlashes`, `delegationRewards`, `delegatorValidators`, `delegatorWithdrawAddress`, `communityPool`) for the same missing guard.

### Proof of Concept
1. Construct an EVM transaction to `0x0000000000000000000000000000000000001007` calling `rewards(address)` with a valid associated `delegatorAddress` argument.
2. Attach a nonzero `value` (e.g. 1 SEI in wei) to the call.
3. Observe the transaction succeeds (returns the rewards data) rather than reverting.
4. Query the precompile's underlying account balance and confirm the sent value is now held there with no subsequent transaction able to withdraw or reclaim it (no such function exists in `IDistr`) [5](#0-4) .

### Citations

**File:** precompiles/distribution/distribution.go (L176-220)
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

**File:** precompiles/distribution/Distribution.sol (L79-83)
```text
    /// @notice Gets all pending rewards for a delegator
    /// @dev Returns rewards from all validators the address has delegated to
    /// @param delegatorAddress The EVM address of the delegator
    /// @return rewards Structured data containing all pending rewards
    function rewards(address delegatorAddress) external view returns (Rewards memory rewards);
```
