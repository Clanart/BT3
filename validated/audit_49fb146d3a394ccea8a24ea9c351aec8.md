Confirmed vulnerability: `rewards()` in the distribution precompile accepts a value-bearing call without a non-payable check, unlike every sibling method.

### Title
Value-bearing calls to distribution precompile's `rewards()` strand native funds - (File: precompiles/distribution/distribution.go)

### Summary
The `rewards` view method of the distribution precompile (address `0x...1007`) is dispatched without any `pcommon.ValidateNonPayable(value)` check, unlike all other query and transaction methods in this precompile and every other payable precompile in the codebase. A caller can attach `msg.value` to a `rewards(...)` call; the call succeeds and returns query data, but the attached native `usei`/`wei` value is never consumed, refunded, or transferred back — it is permanently stranded.

### Finding Description
`Execute` dispatches `RewardsMethod` directly to `p.rewards(ctx, method, args)` with no `value` parameter and no `pcommon.ValidateNonPayable` guard: [1](#0-0) 
Compare this to every other query method in the same file, which explicitly rejects non-zero value via `p.validateInput(value, args, n)` → `pcommon.ValidateNonPayable(value)`: [2](#0-1) [3](#0-2) 
The `rewards` function itself never touches `value` at all — it only queries `DelegationTotalRewards` and packs the output: [4](#0-3) 

In the sei-chain EVM/precompile model, the native value attached to a precompile call is transferred at the StateDB/EVM level from the caller to the precompile's associated account before/along with the `Run`/`Execute` invocation, and the codebase's established pattern (`pcommon.HandlePaymentUsei` / `HandlePaymentUseiWei`) is to immediately refund that value back to the payer's Cosmos account when it is meant to be used for a payment, or to explicitly reject it via `ValidateNonPayable` when a method has no payment semantics: [5](#0-4) [6](#0-5) 
Because `rewards` has neither the reject-guard nor the refund-and-consume logic, any value attached by the caller is credited to the precompile address and never routed back, since a successful (non-reverting) EVM call permanently commits that state change.

This gap is explicitly called out in the repository's own test documentation as a known quirk: [7](#0-6) 

### Impact Explanation
Any unprivileged EVM caller (an EOA transaction sender, or a contract forwarding value through `PrecompileCaller`-style `call{value: ...}`) who mistakenly or intentionally attaches native value to a `rewards(address)` call on the distribution precompile permanently loses that value — it is credited to the precompile's module account with no code path that returns, applies, or accounts for it. This is a direct, irrecoverable fund-loss bug reachable from a single transaction, matching the "Recommendation: return unused funds to caller" class of the referenced Derby report. Because `rewards` is a frequently-called, gas-cheap view method (likely to be invoked by wallets/dApps building UIs, some of which naively forward `msg.value` from wrapper contracts), the practical blast radius per-incident may be limited to accidental/wrapper-forwarded value, but each occurrence is a full, permanent loss of the attached amount.

### Likelihood Explanation
Likelihood is moderate: exploiting this requires a caller (EOA or contract) to attach non-zero `value` to a `rewards` call, which is not the "normal" way to call a view function directly from an EOA transaction, but is plausible via contracts that generically forward `msg.value` to precompile calls (as demonstrated by the repo's own `PrecompileCaller.callTarget` pattern), via wallet integration bugs, or via a griefer intentionally sending residual dust/value to grief a batching/forwarding contract that doesn't strictly zero out value before calling `rewards`. No privileged access, governance, or malicious validator/node behavior is required — a single public JSON-RPC/transaction submission suffices.

### Recommendation
Add the same guard used by every other query method in this precompile:
```go
case RewardsMethod:
    if err = pcommon.ValidateNonPayable(value); err != nil {
        return nil, 0, err
    }
    return p.rewards(ctx, method, args)
```
Alternatively, if `rewards` must remain callable with value forwarded by naive integrators, explicitly refund any attached value to the caller via `pcommon.HandlePaymentUsei`/`HandlePaymentUseiWei` before returning, mirroring the payment-precompile pattern used elsewhere in the codebase.

### Proof of Concept
1. Associate an EVM account with a Sei address and ensure it has a delegation (so `rewards` returns non-trivial data, though this isn't required to trigger the bug).
2. From that account (or via the repo's `PrecompileCaller.callTarget`), call the distribution precompile at `0x0000000000000000000000000000000000001007` with calldata for `rewards(address)` and attach `value: 1_000_000000000000` wei (i.e., non-zero `usei`/`wei`).
3. Observe the call succeeds (returns the rewards struct) and the transaction is included with `status = 1`.
4. Query the caller's post-tx bank/EVM balance: it is reduced by the attached value, and there is no corresponding credit anywhere the caller (or any refund mechanism) can reclaim — the value is now held by the precompile's underlying module account, unlike the `deposit`/`vote`-style methods that either reject value outright or refund-then-consume it as in `precompiles/gov/gov.go:424-458`.

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

**File:** precompiles/common/legacy/v630/precompiles.go (L197-203)
```go

func ValidateNonPayable(value *big.Int) error {
	if value != nil && value.Sign() != 0 {
		return errors.New("sending funds to a non-payable function")
	}

	return nil
```

**File:** precompiles/common/legacy/v614/precompiles.go (L228-247)
```go
}

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

**File:** integration_test/precompile_tests/precompiles/distribution.spec.ts (L9-11)
```typescript
 * Guard quirk worth knowing: rewards() is the one phase-2 view WITHOUT a
 * non-payable check (a value-bearing call would succeed and strand the funds),
 * so this spec deliberately has no "view rejects value" test.
```
