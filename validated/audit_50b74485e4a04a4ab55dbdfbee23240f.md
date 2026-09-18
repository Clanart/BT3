Confirmed: `RewardsMethod` dispatches straight to `p.rewards(ctx, method, args)` without any `pcommon.ValidateNonPayable(value)` call, unlike every other view/transaction handler in this file (`params`, `validatorOutstandingRewards`, `validatorCommission`, `validatorSlashes`, `delegationRewards`, `delegatorValidators`, `delegatorWithdrawAddress`, `communityPool`, `setWithdrawAddress`, `withdrawValidatorCommission`, etc.) which all call `pcommon.ValidateNonPayable(value)` or `p.validateInput(...)` first. [1](#0-0) [2](#0-1) 

Because the distribution precompile address (`0x1007`) is registered as a "payable precompile" that suppresses EVM transfer events to/from it, and because `rewards()` never routes any attached `value` back out (no `HandlePaymentUsei`/`HandlePaymentUseiWei` refund, unlike `sendNative` in the bank precompile), any wei/usei attached to a call to `rewards(address)` is debited from the caller by the EVM value-transfer semantics into the precompile's account, and the precompile never returns or forwards it — it is permanently stranded at `0x1007`, with no `executeTransaction`-equivalent recovery path. [3](#0-2) [4](#0-3) 

This is confirmed by the codebase's own test-suite documentation, which explicitly calls out this gap as a known quirk rather than a fixed bug: `rewards()` is described as "the one phase-2 view WITHOUT a non-payable check (a value-bearing call would succeed and strand the funds)". [5](#0-4) 

### Title
Distribution precompile's `rewards()` view accepts and permanently strands attached value - (File: precompiles/distribution/distribution.go)

### Summary
The `rewards(address)` view method on the distribution precompile (`0x0000000000000000000000000000000000001007`) is the only method in `PrecompileExecutor.Execute` that omits the `pcommon.ValidateNonPayable(value)` guard applied to every sibling view/transaction handler. Any EVM caller who attaches `msg.value` to a call of `rewards()` has that value debited into the precompile's account by native EVM value-transfer semantics, but the precompile logic never reads, refunds, or forwards `value`, so the funds are permanently stuck at the precompile address with no recovery mechanism — directly analogous to ether becoming unrecoverable in `Timelock.sol`'s fallback in the referenced report.

### Finding Description
`Execute` dispatches `RewardsMethod` directly to `p.rewards(ctx, method, args)` without validating that `value` is zero, unlike every other branch in the same switch statement (e.g. `SetWithdrawAddressMethod`, `WithdrawValidatorCommissionMethod`, `ParamsMethod`, `ValidatorOutstandingRewardsMethod`, `ValidatorCommissionMethod`, `ValidatorSlashesMethod`, `DelegationRewardsMethod`, `DelegatorValidatorsMethod`, `DelegatorWithdrawAddressMethod`, `CommunityPoolMethod`), all of which call `pcommon.ValidateNonPayable(value)` (directly or via `p.validateInput`). [6](#0-5) [7](#0-6) 

`0x1007` is registered as a payable precompile in `payablePrecompiles`, which means the EVM engine's normal transfer-event suppression treats value sent to it as a legitimate, intentional payment to the precompile rather than a plain contract call that should revert on value. [8](#0-7) 

The `rewards` function body only reads `args[0]` to build a `QueryDelegationTotalRewardsRequest`, queries `DelegationTotalRewards`, and packs the response — it never inspects or acts on `value` at all, and there is no downstream refund equivalent to `HandlePaymentUsei`/`HandlePaymentUseiWei` used elsewhere in the bank precompile to return payment to the payer. [2](#0-1) [9](#0-8) 

Once the value is debited into the precompile's own account balance during the EVM call (StateDB usei/wei bridge accounting for the precompile address), the only egress method from the distribution module's held balance is via reward/commission withdrawal keyed to specific delegators/validators — there is no generic sweep or refund path for stray value sent to the precompile address itself, mirroring the `Timelock.sol` situation where `executeTransaction` is the sole admin-gated egress and ordinary senders have no recovery route.

### Impact Explanation
Any unprivileged EVM caller (including automated tooling, wallets that default to attaching gas/value, or a malicious front-end) that mistakenly or deliberately sends non-zero `value` while calling `rewards(address)` on `0x1007` permanently loses that usei/wei — it is not refunded, not credited to any withdrawable balance, and not recoverable through any documented precompile method. This constitutes concrete, permanent fund loss/freezing for the caller, matching the High-severity "unrecoverable ether" bug class in the reference report.

### Likelihood Explanation
Exploiting this requires nothing more than a single unprivileged EVM transaction calling `rewards(address)` with non-zero `value` — no association, delegation, or special permission is needed since `rewards` only requires the target `address` argument to be association-resolvable via `accAddressFromArg`. This is trivially reachable by any public JSON-RPC client and is the only view/method among 17 in this file lacking the non-payable check, indicating an accidental omission rather than an intentional design choice (all sibling read-only queries reject value).

### Recommendation
Add `pcommon.ValidateNonPayable(value)` at the top of the `RewardsMethod` case in `Execute` (or inside `rewards`), consistent with every other query method in this precompile, so that calls to `rewards()` with non-zero `value` revert instead of silently stranding funds.

### Proof of Concept
1. Associate an EVM address to a Sei address via `associateViaTx`.
2. From that address, call the distribution precompile's `rewards(delegatorAddress)` with `{ value: ethers.parseEther("1"), gasLimit: ... }`.
3. Observe the transaction succeeds (no revert), the caller's EVM balance decreases by 1 ether-equivalent (usei/wei), and no corresponding credit appears at any withdrawable Sei address — the balance is stranded at the precompile's account with no method available to retrieve it.

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

**File:** giga/deps/xevm/keeper/precompile.go (L11-26)
```go
// add any payable precompiles here
// these will suppress transfer events to/from the precompile address
var payablePrecompiles = map[string]struct{}{
	bank.BankAddress:       {},
	staking.StakingAddress: {},
	gov.GovAddress:         {},
	wasmd.WasmdAddress:     {},
}

func IsPayablePrecompile(addr *common.Address) bool {
	if addr == nil {
		return false
	}
	_, ok := payablePrecompiles[addr.Hex()]
	return ok
}
```

**File:** precompiles/bank/legacy/v67/bank.go (L253-265)
```go
func (p PrecompileExecutor) sendNative(ctx sdk.Context, method *abi.Method, args []interface{}, caller common.Address, callingContract common.Address, value *big.Int, readOnly bool, hooks *tracing.Hooks, evm *vm.EVM) ([]byte, uint64, error) {
	if readOnly {
		return nil, 0, errors.New("cannot call sendNative from staticcall")
	}
	if ctx.EVMPrecompileCalledFromDelegateCall() {
		return nil, 0, errors.New("cannot delegatecall sendNative")
	}
	if err := pcommon.ValidateArgsLength(args, 1); err != nil {
		return nil, 0, err
	}
	if value == nil || value.Sign() == 0 {
		return nil, 0, errors.New("set `value` field to non-zero to send")
	}
```

**File:** integration_test/precompile_tests/precompiles/distribution.spec.ts (L9-11)
```typescript
 * Guard quirk worth knowing: rewards() is the one phase-2 view WITHOUT a
 * non-payable check (a value-bearing call would succeed and strand the funds),
 * so this spec deliberately has no "view rejects value" test.
```

**File:** precompiles/common/legacy/v620/precompiles.go (L230-247)
```go
func HandlePaymentUseiWei(ctx sdk.Context, precompileAddr sdk.AccAddress, payer sdk.AccAddress, value *big.Int, bankKeeper putils.BankKeeper, evmKeeper putils.EVMKeeper, hooks *tracing.Hooks, depth int) (sdk.Int, sdk.Int, error) {
	usei, wei := state.SplitUseiWeiAmount(value)
	// refund payer because the following precompile logic will debit the payments from payer's account
	// this creates a new event manager to avoid surfacing these as cosmos events
	if err := bankKeeper.SendCoinsAndWei(ctx.WithEventManager(sdk.NewEventManager()), precompileAddr, payer, usei, wei); err != nil {
		return sdk.Int{}, sdk.Int{}, err
	}
	if hooks != nil {
		newCtx := ctx.WithGasMeter(sdk.NewInfiniteGasMeterWithMultiplier(ctx))
		if hooks.OnEnter != nil {
			hooks.OnEnter(depth+1, byte(vm.CALL), evmKeeper.GetEVMAddressOrDefault(newCtx, precompileAddr), evmKeeper.GetEVMAddressOrDefault(newCtx, payer), []byte{}, GetRemainingGas(newCtx, evmKeeper), value)
		}
		if hooks.OnExit != nil {
			hooks.OnExit(depth+1, []byte{}, 0, nil, false)
		}
	}
	return usei, wei, nil
}
```
