## Finding: Value sent to the `rewards()` distribution precompile view is silently stranded (no non-payable check, no refund)

### Title
Sending native SEI value to the `distribution` precompile's `rewards()` view permanently strands funds - (File: `precompiles/distribution/distribution.go`)

### Summary
The `distribution` precompile at `0x0000000000000000000000000000000000001007` dispatches every state-changing method through `pcommon.ValidateNonPayable(value)` (rejecting non-zero `msg.value`) or through `pcommon.HandlePaymentUsei`/`HandlePaymentUseiWei` (which immediately refunds the payer). The `rewards()` view method is the sole exception: it is dispatched with neither guard, so a call bearing `msg.value` succeeds, the EVM-level value transfer to the precompile's derived account completes, and the funds are never returned.

### Finding Description
In `precompiles/distribution/distribution.go`, `Execute` dispatches `RewardsMethod` directly to `p.rewards(ctx, method, args)` with no `value` argument at all, unlike every other case in the same switch which either calls `pcommon.ValidateNonPayable(value)` before proceeding (e.g. `WithdrawValidatorCommissionMethod`) or funnels `value` through `pcommon.HandlePaymentUsei` (as done by payable precompiles like staking's `delegate`): [1](#0-0) [2](#0-1) 

The `rewards` implementation itself never inspects `value`, never calls `ValidateNonPayable`, and never calls `HandlePaymentUsei`/`HandlePaymentUseiWei` to refund the caller: [3](#0-2) 

This is a known, explicitly documented quirk in the integration test suite: [4](#0-3) 

Every other payment path in the precompile framework either rejects value outright via `ValidateNonPayable`: [5](#0-4) 

or explicitly refunds the payer before proceeding, e.g. `HandlePaymentUsei` sends the coin back to `payer` immediately: [6](#0-5) 

Crucially, the `distribution` precompile address (`0x...1007`) is **not** in the `payablePrecompiles` allowlist used elsewhere in the EVM keeper (only `bank`, `staking`, `gov`, `wasmd` are listed): [7](#0-6) 

That map only controls suppression of ERC20-style transfer events, not whether the underlying `usei`/`wei` balance actually moves — the StateDB-level balance transfer for `CALL` value happens irrespective of this allowlist. Because `rewards()` is a `view`-style function that never routes the payment through `HandlePaymentUsei`, once the value transfer to the precompile's associated Sei account completes, there is no code path anywhere in the module that sweeps or forwards balance held at that address back to a user — the account backing precompile address `0x...1007` has no corresponding private key or admin withdrawal function, mirroring the exact bug class from the reported `Marketplace.sol` issue (payable entry point + no corresponding withdrawal mechanism).

### Impact Explanation
Any unprivileged EVM caller who calls `rewards(address)` on the distribution precompile while attaching non-zero `msg.value` (either directly or by mistake, e.g., a wallet/library that always attaches value to precompile calls) will have that native SEI/wei balance moved to the module-controlled address and permanently frozen, since no other function forwards or refunds balances held by the distribution precompile's backing account. This is a concrete, permanent loss of user funds reachable from a single unprivileged transaction — no special privileges, precompile allowlisting, or protocol misconfiguration required.

### Likelihood Explanation
Likelihood is moderate: it requires the caller to intentionally or accidentally attach value to a call that superficially looks like (and is documented/used as) a read-only view function. Because `rewards()` is exposed as a plain Solidity-ABI function without a `view`/`pure` modifier enforced on-chain, and other similarly-named "getter" functions in the same precompile (e.g. `delegationRewards`) do pass `value` through validation, users/tooling may not expect this one exception, making accidental value attachment plausible (e.g., through generic multicall/batch wrappers that always forward `msg.value`).

### Recommendation
Add the missing guard in `precompiles/distribution/distribution.go`'s `Execute` dispatch for `RewardsMethod`, either:
1. Call `pcommon.ValidateNonPayable(value)` before dispatching to `p.rewards(...)`, matching the pattern used for `WithdrawValidatorCommissionMethod` and other non-payable methods, or
2. Pass `value` into `rewards()` and refund it via `pcommon.HandlePaymentUsei`/`HandlePaymentUseiWei` if non-zero, consistent with the rest of the precompile's payment handling.

### Proof of Concept
1. Deploy/attach to the distribution precompile at `0x0000000000000000000000000000000000001007`.
2. Call `rewards(delegatorAddress)` with a non-zero `value` (e.g., `{value: 1}`), as demonstrated by the direct unit-test invocation pattern in `precompiles/distribution/distribution_test.go` (`RunAndCalculateGas` with a non-nil `value` field, `TestPrecompile_RunAndCalculateGas_Rewards`), and observe that unlike `WithdrawValidatorCommission` (which reverts with `"sending funds to a non-payable function"` per `TestWithdrawValidatorCommission_InputValidation`), no such rejection occurs for `rewards`.
3. Confirm the caller's SEI/wei balance decreased by the sent value and that the distribution precompile's underlying account balance increased with no corresponding `HandlePaymentUsei` refund event, leaving the funds permanently inaccessible. [8](#0-7)

### Citations

**File:** precompiles/distribution/distribution.go (L176-183)
```go
	case WithdrawValidatorCommissionMethod:
		if err = pcommon.ValidateNonPayable(value); err != nil {
			return nil, 0, err
		}
		if readOnly {
			return nil, 0, errors.New("cannot call distr precompile from staticcall")
		}
		return p.withdrawValidatorCommission(ctx, method, caller, evm)
```

**File:** precompiles/distribution/distribution.go (L204-205)
```go
	case RewardsMethod:
		return p.rewards(ctx, method, args)
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

**File:** integration_test/precompile_tests/precompiles/distribution.spec.ts (L9-11)
```typescript
 * Guard quirk worth knowing: rewards() is the one phase-2 view WITHOUT a
 * non-payable check (a value-bearing call would succeed and strand the funds),
 * so this spec deliberately has no "view rejects value" test.
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

**File:** precompiles/common/precompiles.go (L269-279)
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
```

**File:** x/evm/keeper/precompile.go (L13-18)
```go
var payablePrecompiles = map[string]struct{}{
	bank.BankAddress:       {},
	staking.StakingAddress: {},
	gov.GovAddress:         {},
	wasmd.WasmdAddress:     {},
}
```

**File:** precompiles/distribution/distribution_test.go (L1556-1562)
```go
		{
			name:       "sending value to non-payable function should fail",
			validator:  "seivaloper1reedlc9w8p7jrpqfky4c5k90nea4p6dhk5yqgd",
			value:      big.NewInt(1),
			wantError:  true,
			wantErrMsg: "sending funds to a non-payable function",
		},
```
