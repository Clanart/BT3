### Title
Distribution precompile `rewards()` view method omits the non-payable guard, permanently stranding any SEI sent with the call - (File: precompiles/distribution/distribution.go)

### Summary
The `rewards()` view handler in the Distribution precompile (`0x...1007`) is dispatched without calling `pcommon.ValidateNonPayable(value)`, unlike every other read-only method in the same dispatcher (`params`, `validatorOutstandingRewards`, `validatorCommission`, `validatorSlashes`, `delegationRewards`, `delegatorValidators`, `delegatorWithdrawAddress`, `communityPool`, which all forward `value` into a `validateInput`/`ValidateNonPayable` check). Because EVM `CALL` semantics move native value to the callee address before precompile logic runs, and `rewards()` never calls `HandlePaymentUsei` (or any other refund/consumption path) to move that value back to the caller or into a Cosmos-side transfer, any usei/wei sent alongside a `rewards()` call is credited to the precompile's account and never spent, refunded, or otherwise reachable, similar to the BakerFi bug class where deposited funds have no corresponding withdrawal path.

### Finding Description
The `Execute` dispatcher for the distribution precompile handles the `RewardsMethod` case as: [1](#0-0) 
with no non-payable check, while sibling view methods immediately below all pass `value` through and end up calling `validateInput`, which invokes `pcommon.ValidateNonPayable`: [2](#0-1) [3](#0-2) 

This asymmetry is explicitly called out in the repo's own integration test comment: [4](#0-3) 

The pattern used across the codebase to safely accept value on a payable precompile method is `pcommon.HandlePaymentUsei`, which explicitly sends the coin back to the payer (or consumes it via a Cosmos message) so that value transferred to the precompile account during the EVM `CALL` does not remain stuck there: [5](#0-4) 

`rewards()` never calls `HandlePaymentUsei`, never rejects non-zero value, and never issues any bank/staking/distribution message that consumes the transferred coin — it only queries `DelegationTotalRewards` and packs the response: [6](#0-5) 

Because the current (`precompiles/distribution/distribution.go`) `rewards` handler follows the same legacy signature `rewards(ctx, method, args)` (no `value` parameter at all — see the dispatch call above), it structurally cannot perform the guard or the refund even if someone wanted to add it later without changing the function signature.

### Impact Explanation
Any unprivileged EVM caller (contract or EOA) that sends non-zero `msg.value` while calling `rewards(address)` on the distribution precompile has that SEI permanently locked at the precompile's account address. The precompile has no code path that ever spends, refunds, or exposes that balance — there is no "withdraw from precompile" mechanism analogous to Aave's collateral-only withdrawal in the original finding. This constitutes concrete, permanent loss of user funds reachable by a single, ordinary transaction from any public RPC client, satisfying the "concrete fund loss or permanent freezing" impact bar.

### Likelihood Explanation
Likelihood is bounded by the fact that this requires the caller to deliberately (or through a buggy calling contract) attach value to a `staticcall`-safe view function — most tooling and ABIs mark `rewards` as a view, so callers would not naturally send value. However, nothing in the precompile enforces this at the Go level (unlike `staticcall` guards used for state-changing methods), so any calling contract or raw `eth_call`/transaction that supplies `value` with the `rewards` selector will trigger the loss. The bug is reachable from a single crafted EVM transaction with no special privileges, matching the required reachability bar (unprivileged transaction sender / public-RPC client).

### Recommendation
Add `pcommon.ValidateNonPayable(value)` at the top of the `RewardsMethod` case in `Execute` (or inside `rewards`, after threading `value` through the function signature, matching the pattern already used by `delegationRewards`, `communityPool`, etc.), so that a non-zero value attached to `rewards()` reverts instead of being silently accepted and stranded. Apply the fix to the current file and audit whether other legacy versions preserved for historical/tracer replay have the same gap (they do, per the `v605`/`v606`/`v610`/`v614`/`v620`/`v630`/`v640`/`v65`/`v66`/`v67` snippets found), noting that legacy versions used for historical block replay may need to remain unchanged for consensus compatibility, and the fix should target the current active precompile version.

### Proof of Concept
1. Deploy or use any EVM contract (or send a raw transaction) that calls the distribution precompile at `0x0000000000000000000000000000000000001007` with selector for `rewards(address)`, and set `msg.value` (or transaction `value`) to a non-zero usei/wei amount.
2. Observe in `Execute`, the dispatch for `RewardsMethod` (precompiles/distribution/distribution.go:204-205) does not call `ValidateNonPayable` and does not call `HandlePaymentUsei`, unlike neighboring cases (line 206-221) — the call succeeds and returns the rewards data.
3. Query the bank balance of the precompile's associated Sei address after the call: the previously-attached value has been credited there and is not moved anywhere else, and there is no distribution-precompile method that can withdraw an arbitrary balance from the precompile's own account — the funds are permanently stranded, mirroring the BakerFi report's "no withdrawal path" root cause.

### Citations

**File:** precompiles/distribution/distribution.go (L204-205)
```go
	case RewardsMethod:
		return p.rewards(ctx, method, args)
```

**File:** precompiles/distribution/distribution.go (L206-221)
```go
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

**File:** precompiles/common/legacy/v67/precompiles.go (L271-292)
```go
func HandlePaymentUsei(ctx sdk.Context, precompileAddr sdk.AccAddress, payer sdk.AccAddress, value *big.Int, bankKeeper putils.BankKeeper, evmKeeper putils.EVMKeeper, hooks *tracing.Hooks, depth int) (sdk.Coin, error) {
	usei, wei := state.SplitUseiWeiAmount(value)
	if !wei.IsZero() {
		return sdk.Coin{}, fmt.Errorf("selected precompile function does not allow payment with non-zero wei remainder: received %s", value)
	}
	coin := sdk.NewCoin(sdk.MustGetBaseDenom(), usei)
	// refund payer because the following precompile logic will debit the payments from payer's account
	// this creates a new event manager to avoid surfacing these as cosmos events
	if err := bankKeeper.SendCoins(ctx.WithEventManager(sdk.NewEventManager()), precompileAddr, payer, sdk.NewCoins(coin)); err != nil {
		return sdk.Coin{}, err
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
	return coin, nil
}
```

**File:** precompiles/distribution/legacy/v67/distribution.go (L326-362)
```go
	}
	return bz, pcommon.GetRemainingGas(ctx, p.evmKeeper), nil
}

func (p PrecompileExecutor) revokeWithdrawAuthorization(ctx sdk.Context, method *abi.Method, caller common.Address, args []interface{}, value *big.Int) ([]byte, uint64, error) {
	if err := p.validateInput(value, args, 1); err != nil {
		return nil, 0, err
	}

	granter, err := pcommon.GetSeiAddressByEvmAddress(ctx, caller, p.evmKeeper)
	if err != nil {
		return nil, 0, err
	}
	grantee, err := pcommon.GetSeiAddressFromArg(ctx, args[0], p.evmKeeper)
	if err != nil {
		return nil, 0, err
	}
	if err := pcommon.RevokeAuthorizations(
		ctx,
		p.authzMsgServer,
		granter,
		grantee,
		&distrtypes.MsgWithdrawDelegatorReward{},
		&distrtypes.MsgWithdrawValidatorCommission{},
	); err != nil {
		return nil, 0, err
	}

	bz, err := method.Outputs.Pack(true)
	if err != nil {
		return nil, 0, err
	}
	return bz, pcommon.GetRemainingGas(ctx, p.evmKeeper), nil
}

func (p PrecompileExecutor) withdrawDelegationRewards(ctx sdk.Context, method *abi.Method, caller common.Address, args []interface{}, value *big.Int, evm *vm.EVM) (ret []byte, remainingGas uint64, rerr error) {
	if err := p.validateInput(value, args, 1); err != nil {
```
