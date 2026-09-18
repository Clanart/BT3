### Title
Stranded value sent to `distribution` precompile's `rewards()` view - ([File: precompiles/distribution/distribution.go])

### Summary
The `distribution` precompile at address `0x...1007` dispatches the `rewards()` method without any non-payable guard or payment-refund handling. A caller who attaches `msg.value` to a `rewards()` call has that value debited from their EVM balance and credited to the precompile's associated Sei account, but the precompile never returns, forwards, or otherwise accounts for it — permanently stranding the funds, analogous to the reported `GigaNameNFT` issue where collected ETH has no withdrawal path.

### Finding Description
`PrecompileExecutor.Execute` in the distribution precompile dispatches every state-changing method with either a `pcommon.ValidateNonPayable(value)` check (e.g. `WithdrawValidatorCommissionMethod`) or a `pcommon.HandlePaymentUsei(...)` call that refunds the payer (used by `gov.deposit`, `wasmd.execute`, `staking.delegate`, etc.). The `RewardsMethod` case, however, calls straight into `p.rewards(ctx, method, args)` with no `value` parameter at all: [1](#0-0) 

The `rewards` implementation itself never inspects or validates `value`, and never calls `HandlePaymentUsei` to refund a non-zero payment: [2](#0-1) 

Every other payable precompile method in the codebase (`gov.deposit`, `wasmd.execute`/`instantiate`/`executeBatch`) explicitly reconciles `value` against the coins to be spent and calls `HandlePaymentUsei`, which refunds the payer via `bankKeeper.SendCoins` back from the precompile-associated account: [3](#0-2) 

`rewards()` is the sole exception — this is explicitly documented in the integration test suite as a known guard gap: [4](#0-3) 

Because there is no `ValidateNonPayable` rejection and no `HandlePaymentUsei`/refund path for `rewards()`, any usei/wei sent as `msg.value` on a `rewards()` call is transferred (via the standard EVM value-transfer/usei-wei StateDB bridge) into the balance associated with the distribution precompile address, with no code path anywhere in the module to move it back out. This mirrors the `GigaNameNFT` root cause exactly: value is accepted by a callable entry point, but the contract/precompile provides no mechanism (owner withdrawal, refund, or otherwise) to recover it.

### Impact Explanation
Any funds attached to a `rewards()` call become permanently and irrecoverably locked, since the distribution module's keeper logic that owns this account never expects or accounts for externally-deposited usei/wei, and no precompile method exists to sweep or withdraw it. This is a genuine, if narrow, fund-freezing bug reachable by any unprivileged EVM transaction sender interacting with a Cosmos precompile — squarely in scope as a "permanent freezing of funds" issue.

### Likelihood Explanation
Likelihood is high because `rewards()` is a commonly called, publicly reachable view method on a well-known precompile address (`0x...1007`), reachable from any EVM transaction or `eth_call`/contract call that mistakenly or maliciously attaches value (e.g., a poorly written integrator contract that forwards `msg.value` on every downstream call, or a user fat-fingering a nonzero `value` field). No association, permission, or special privilege is required to trigger it — a single crafted transaction suffices.

### Recommendation
Add a `pcommon.ValidateNonPayable(value)` check to the `RewardsMethod` dispatch branch in `Execute`, matching the pattern already used for `WithdrawValidatorCommissionMethod` and other non-payable branches, so that a non-zero `value` reverts the call instead of silently stranding funds. Audit all other query/view methods dispatched in `Execute` (e.g. `ParamsMethod`, `ValidatorOutstandingRewardsMethod`, etc.) to confirm they either reject payable value or explicitly refund it, since these currently rely on the caller never sending value rather than on an enforced guard.

### Proof of Concept
1. From an EVM account associated to a Sei address with a nonzero usei/wei balance, call the `distribution` precompile (`0x0000000000000000000000000000000000001007`) method `rewards(address)` via a real `CALL` (not `eth_call`), attaching a nonzero `value` (e.g. `1` usei = `10^12` wei).
2. The transaction succeeds and returns the delegator's rewards as normal — unlike `withdrawValidatorCommission` or other guarded methods, no `ValidateNonPayable` error is raised.
3. Observe the caller's EVM balance decreased by `value`, and no corresponding refund transaction/event was emitted by `HandlePaymentUsei` (contrast with `gov.deposit`, which always emits a refund/payment flow).
4. The value now resides in the balance associated with the distribution precompile's mapped Sei account with no exposed method (`withdraw`, `sweep`, etc.) anywhere in `precompiles/distribution/distribution.go` to recover it — the funds are permanently stuck, confirming the behavior called out in `integration_test/precompile_tests/precompiles/distribution.spec.ts:9-11`.

### Citations

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

**File:** integration_test/precompile_tests/precompiles/distribution.spec.ts (L9-11)
```typescript
 * Guard quirk worth knowing: rewards() is the one phase-2 view WITHOUT a
 * non-payable check (a value-bearing call would succeed and strand the funds),
 * so this spec deliberately has no "view rejects value" test.
```
