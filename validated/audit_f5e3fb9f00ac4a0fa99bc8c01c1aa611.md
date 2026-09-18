### Title
Value sent to the distribution precompile's `rewards()` view function is stranded and never refunded - ([File: precompiles/distribution/distribution.go])

### Summary
The distribution precompile's `Execute` dispatcher fails to reject non-zero `msg.value` for the `RewardsMethod` case, unlike every other guarded method on the same precompile. Because the EVM/usei-wei StateDB bridge unconditionally moves the caller's usei/wei to the precompile's associated Sei address as part of standard EVM value-transfer semantics on any payable call, and because `rewards()` never calls `pcommon.HandlePaymentUsei`/`HandlePaymentUseiWei` to refund that value (as delegate/send/execute precompile methods do), any usei sent along with a `rewards()` call becomes permanently stranded at the precompile's backing account. This is directly analogous to the wfCash report: a payable code path that should always either reject or refund incoming value instead silently accepts and never returns it, causing a permanent loss of user funds.

### Finding Description
In `precompiles/distribution/distribution.go`, `PrecompileExecutor.Execute` explicitly calls `pcommon.ValidateNonPayable(value)` before dispatching to `setWithdrawAddress`, `withdrawValidatorCommission`, and other view/tx methods, but the `RewardsMethod` case dispatches straight to `p.rewards(ctx, method, args)` with no such check: [1](#0-0) [2](#0-1) 

This asymmetry is explicitly called out in the precompile's own integration test suite as a known quirk: [3](#0-2) 

All other payable precompile methods (bank `sendNative`, staking `delegate`, wasmd `instantiate`/`execute`) use `pcommon.HandlePaymentUsei`/`HandlePaymentUseiWei` to immediately refund the caller for the usei debited from them by the EVM value-transfer machinery, since "the following precompile logic will debit the payments from payer's account": [4](#0-3) [5](#0-4) 

`rewards()` performs none of this refund logic, so any usei/wei attached to the call remains credited to the precompile's associated address rather than being wrapped back to the caller or the recipient, mirroring the wfCash bug where `returnExcessWrapped=false` caused residual ETH to be sent to the wrapper contract itself instead of the user. Compounding this, the distribution precompile is also absent from the `payablePrecompiles` allow-list used elsewhere in the EVM keeper to specially handle payable-precompile transfer semantics, showing that distribution generally was not designed to be an intentional value-receiving destination for its view methods: [6](#0-5) 

### Impact Explanation
Any unprivileged EVM transaction sender or contract that calls `rewards(...)` on the distribution precompile (address `0x1007`) with a non-zero `value` will have that usei/wei permanently debited from their EVM balance and never returned, since the precompile neither rejects the payment nor refunds it. This is a direct, unprivileged, single-transaction path to permanent loss of user funds — matching the required "concrete fund loss or permanent freezing" impact bar.

### Likelihood Explanation
The bug is trivially triggerable by any externally owned account or contract with an associated Sei address by simply attaching `value` to a call to the `rewards` selector on the distribution precompile. No special privileges, governance, or validator collusion are required, and the code path is reachable directly from the public EVM JSON-RPC transaction-submission surface.

### Recommendation
Add `if err := pcommon.ValidateNonPayable(value); err != nil { return nil, 0, err }` to the `RewardsMethod` case in `PrecompileExecutor.Execute` (and any other view methods on this and other precompiles that currently omit the check), consistent with every other query/method on the same precompile. Alternatively, if `rewards()` is intended to ever accept payment, it must call `pcommon.HandlePaymentUsei`/`HandlePaymentUseiWei` to refund the caller before returning, exactly as delegate/sendNative/instantiate/execute already do.

### Proof of Concept
1. Associate an EVM account with a Sei address (e.g., via a 0-value self-send).
2. Call the distribution precompile (`0x1007`) method `rewards(delegator, validator)` (or any `RewardsMethod` selector) with `value: 1000000000000` wei (1 usei) attached.
3. The transaction succeeds; the caller's EVM balance is reduced by 1 usei worth of wei.
4. Inspect the caller's Cosmos `usei` balance and the distribution precompile's associated Sei account balance — the 1 usei is now held by the precompile's account and is never returned to the caller, unlike `delegate()` or `sendNative()` which visibly refund the payer via `HandlePaymentUsei`/`HandlePaymentUseiWei` before debiting the intended amount.

### Citations

**File:** precompiles/distribution/distribution.go (L204-205)
```go
	case RewardsMethod:
		return p.rewards(ctx, method, args)
```

**File:** precompiles/distribution/distribution.go (L242-254)
```go
func (p PrecompileExecutor) setWithdrawAddress(ctx sdk.Context, method *abi.Method, caller common.Address, args []interface{}, value *big.Int, evm *vm.EVM) (ret []byte, remainingGas uint64, rerr error) {
	defer func() {
		if err := recover(); err != nil {
			ret = nil
			remainingGas = 0
			rerr = fmt.Errorf("%s", err)
			return
		}
	}()
	if err := pcommon.ValidateNonPayable(value); err != nil {
		rerr = err
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

**File:** precompiles/common/precompiles.go (L292-309)
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

**File:** precompiles/staking/staking.go (L410-417)
```go
func (p PrecompileExecutor) delegateFor(ctx sdk.Context, method *abi.Method, delegator sdk.AccAddress, delegatorEVM common.Address, validatorBech32 string, value *big.Int, hooks *tracing.Hooks, evm *vm.EVM, execute stakingMessageExecutor) ([]byte, uint64, error) {
	if value == nil || value.Sign() == 0 {
		return nil, 0, errors.New("set `value` field to non-zero to send delegate fund")
	}
	coin, err := pcommon.HandlePaymentUsei(ctx, p.evmKeeper.GetSeiAddressOrDefault(ctx, p.address), delegator, value, p.bankKeeper, p.evmKeeper, hooks, evm.GetDepth())
	if err != nil {
		return nil, 0, err
	}
```

**File:** x/evm/keeper/precompile.go (L11-26)
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
