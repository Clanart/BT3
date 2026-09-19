Found it. The `distribution` precompile's `rewards` view method is documented in-repo as accepting `msg.value` without any non-payable check, matching the exact bug class from the report: a payable-capable call path that doesn't require/reject `msg.value`, so a caller who mistakenly attaches ETH (wei) to the call loses those funds since `rewards()` is a pure query that never consumes or refunds the sent value.### Title
`rewards()` view in the distribution precompile accepts `msg.value` without a non-payable check, silently stranding attached usei/wei - ([File: precompiles/distribution/distribution.go])

### Summary
The distribution precompile at `0x0000000000000000000000000000000000001007` dispatches the `RewardsMethod` ("rewards") directly to `p.rewards(ctx, method, args)` with no `pcommon.ValidateNonPayable(value)` call, unlike every other entry point in the same `Execute` switch (`SetWithdrawAddressMethod`, `WithdrawDelegationRewardsMethod`, `WithdrawValidatorCommissionMethod`, etc.), which either validate the value is zero or explicitly consume it via `HandlePaymentUsei`. [1](#0-0) [2](#0-1) 

### Finding Description
This mirrors the reported 1inch `ClipperRouter` bug class: a function that neither requires `msg.value` for its operation nor rejects a non-zero value, so a caller who attaches ETH/native funds to a call that doesn't need it (a swap/read path) loses those funds. In sei-chain's EVM precompile framework, when a payable EVM call reaches a precompile with a non-zero `value`, that value is minted/tracked by the usei/wei StateDB bridge as sent to the precompile address, and it is only returned to the caller when the specific handler explicitly calls `pcommon.HandlePaymentUsei`/`HandlePaymentUseiWei` (which debits the precompile and refunds/uses it) or explicitly rejects it via `pcommon.ValidateNonPayable`. [3](#0-2) 

The `rewards()` handler is a pure read-only query (`DelegationTotalRewards`) that packs and returns output — it never inspects, spends, or refunds `value`: [4](#0-3) 

The project's own integration-test documentation explicitly calls this out as an intentional/known gap: "rewards() is the one phase-2 view WITHOUT a non-payable check (a value-bearing call would succeed and strand the funds)," and the corresponding test suite deliberately omits a "view rejects value" assertion for this method. [5](#0-4) [6](#0-5) 

Since the `rewards` dispatch path (unlike `WithdrawValidatorCommissionMethod`, which does call `ValidateNonPayable`) has no such guard, and `rewards()` itself performs no `HandlePaymentUsei`/refund logic, any usei/wei value attached to a `rewards(...)` call is accepted by the precompile and never returned to the caller. [7](#0-6) 

### Impact Explanation
Any unprivileged EVM transaction sender or contract that calls `rewards(address)` on the distribution precompile with a non-zero `value` field permanently loses that value — it is neither used for any state-changing purpose nor refunded, resulting in a direct, irrecoverable loss of user funds. Because `rewards` is a commonly-used read query (e.g., wallets/dApps checking delegator rewards), a wallet or integrator bug that mistakenly forwards `msg.value` on this call (analogous to the 1inch report's WETH/ERC20 case) silently destroys user funds with no revert to signal the mistake.

### Likelihood Explanation
Likelihood is moderate: it requires a calling contract/dApp/wallet to mistakenly attach `value` to a `rewards()` call. Given the precompile ABI marks state-mutating distribution methods as payable and only `rewards` diverges by omitting the payable-rejection guard, integrators are unlikely to specifically special-case this one query method, making an accidental non-zero-value call plausible in real integrations (e.g., fee-forwarding wrappers, multi-call batchers, or generic "send value with every EVM call" utility wrappers).

### Recommendation
Add a `pcommon.ValidateNonPayable(value)` check at the start of the `RewardsMethod` case in `Execute` (matching the pattern already used for `WithdrawValidatorCommissionMethod`), so any non-zero value sent to `rewards()` causes the call to revert instead of being silently retained by the precompile:
```go
case RewardsMethod:
    if err = pcommon.ValidateNonPayable(value); err != nil {
        return nil, 0, err
    }
    return p.rewards(ctx, method, args)
```
Apply the same fix to all historical/legacy versions of `precompiles/distribution/legacy/*/distribution.go` that share this same dispatch gap.

### Proof of Concept
1. Call the distribution precompile at `0x0000000000000000000000000000000000001007` with method `rewards(address delegator)` from an associated EVM account, attaching a non-zero `value` (e.g., 1 usei worth of wei) in the transaction.
2. Observe the call succeeds and returns the rewards query result exactly as it would with `value = 0`.
3. Check the caller's on-chain usei balance before and after the transaction (minus gas): the attached value has been deducted from the caller and is not present in the precompile's or caller's post-call balance — it is stranded, since `rewards()` never calls `HandlePaymentUsei`/`HandlePaymentUseiWei` to route it back. [1](#0-0) [4](#0-3)

### Citations

**File:** precompiles/distribution/distribution.go (L176-205)
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
```

**File:** precompiles/distribution/distribution.go (L543-578)
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
```

**File:** precompiles/common/legacy/v65/precompiles.go (L197-227)
```go

func ValidateNonPayable(value *big.Int) error {
	if value != nil && value.Sign() != 0 {
		return errors.New("sending funds to a non-payable function")
	}

	return nil
}

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

**File:** integration_test/precompile_tests/README.md (L77-80)
```markdown
- **Guard tables differ per precompile** — e.g. json and pointerview accept
  DELEGATECALL, staking/gov/distribution/pointer reject it precompile-wide,
  and `distribution.rewards` accepts value (no non-payable check). Don't
  generalize dispatch tests; copy the per-method guards from the Go source.
```
