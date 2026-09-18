Based on the investigation, the underlying bug class (a value-bearing precompile method that never accounts for the received value, resulting in stranded/lost native funds) has a strong analog in the `distribution` precompile's `rewards()` view method.

### Title
`rewards()` in the distribution precompile has no non-payable guard, letting sent value be stranded - (File: precompiles/distribution/distribution.go)

### Summary
Every other query/view method in the distribution precompile validates that no value is attached before executing (`pcommon.ValidateNonPayable(value)`), and every value-accepting transaction method either forwards the value through Cosmos bank logic or explicitly refunds it via `pcommon.HandlePaymentUsei`. The `rewards()` dispatch case is the sole exception: it is dispatched without a non-payable check and its handler never inspects or refunds `value` at all.

### Finding Description
In `precompiles/distribution/distribution.go`, the `Execute` dispatcher rejects value for other query methods implicitly via consistent guard patterns elsewhere in the codebase (e.g. `WithdrawValidatorCommissionMethod` explicitly calls `pcommon.ValidateNonPayable(value)`), but the `RewardsMethod` case simply calls `p.rewards(ctx, method, args)` without passing or validating `value`: [1](#0-0) 

The handler itself, `rewards()`, only takes `ctx`, `method`, and `args` — it has no parameter for `value` and performs no `HandlePaymentUsei`/refund logic: [2](#0-1) 

This pattern is confirmed to be intentional-but-dangerous by the integration test suite's own comment: [3](#0-2) 

At the EVM/StateDB layer, when a caller sends nonzero `value` to any contract address (including a precompile), the EVM transfers that value into the target address's balance via `AddBalance`, which is backed by real Cosmos bank state (`usei`/`wei`) credited to the precompile's Sei account: [4](#0-3) 

Other precompile methods that intend to accept payment (e.g., `bank.sendNative`, `wasmd.execute`) explicitly call `pcommon.HandlePaymentUsei`/`HandlePaymentUseiWei` to refund the payer out of the precompile's module account before applying the intended debit — this is the codebase's established mechanism for reconciling "value transferred to precompile address" with actual accounting: [5](#0-4) [6](#0-5) 

`rewards()` does none of this. Additionally, `distribution` is not registered in the `payablePrecompiles` allow-list that is used elsewhere to explicitly reason about payable precompile addresses: [7](#0-6) 

This means any caller (EOA transaction or contract) who calls `rewards(address)` on the distribution precompile (`0x...1007`) with nonzero `msg.value` will have that value permanently credited to the distribution precompile's Sei bank account, with no code path to refund, sweep, or otherwise recover it for the sender.

### Impact Explanation
Any unprivileged EVM transaction sender or contract can call the distribution precompile's `rewards()` method with attached `usei`/`wei` value. Because the handler neither rejects the payment (no `ValidateNonPayable`) nor credits/refunds it (no `HandlePaymentUsei`), the sent funds are irrecoverably absorbed into the precompile's account balance and permanently lost to the caller — matching the "flash() should not be payable" bug class of value silently absorbed by a payable function that doesn't account for it. This is a direct, permanent loss of user funds reachable from a single transaction, satisfying the Medium severity bar for fund loss.

### Likelihood Explanation
The Solidity interface declares `rewards()` as `view` (non-payable) so a normal call through the generated bindings would not attach value, but nothing on-chain prevents a raw transaction/contract call from setting nonzero `value` on this method selector — Solidity's `view`/non-payable modifier is not enforced at the ABI-call level for external low-level calls or manually crafted EVM transactions. Any user submitting a raw call (`eth_sendRawTransaction` or a `call{value: x}` from another contract) directly to the precompile address with this method's selector reaches the vulnerable path, requiring no special privileges.

### Recommendation
Add `pcommon.ValidateNonPayable(value)` to the `RewardsMethod` case in `precompiles/distribution/distribution.go`'s `Execute` dispatcher (mirroring the guard already used for `WithdrawValidatorCommissionMethod` and consistent with the pattern applied to sibling view methods), so that any nonzero value attached to a `rewards()` call causes the transaction to revert instead of silently stranding funds. Apply the same audit to confirm no other dispatch case in this or other precompiles omits both the non-payable check and payment-handling logic.

### Proof of Concept
1. An EOA associates its Sei/EVM address and constructs a raw EVM transaction calling the distribution precompile at its address with `data` = ABI-encoded `rewards(address)` selector + a valid delegator address, and `value` = nonzero amount (e.g., 1 SEI in wei units).
2. Submit the transaction. The EVM's `AddBalance` mechanism credits the value to the precompile's Sei bank account before `Run`/`Execute` is invoked.
3. `Execute` dispatches to `RewardsMethod` → `p.rewards(ctx, method, args)`, which ignores `value` entirely, executes the query, and returns rewards data successfully with `err == nil`.
4. The transaction succeeds; the caller's balance is permanently reduced by the sent value, and no compensating transfer occurs — the funds remain stuck in the precompile's account, unlike other payable precompile flows which explicitly refund via `HandlePaymentUsei`.

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

**File:** integration_test/precompile_tests/precompiles/distribution.spec.ts (L9-11)
```typescript
 * Guard quirk worth knowing: rewards() is the one phase-2 view WITHOUT a
 * non-payable check (a value-bearing call would succeed and strand the funds),
 * so this spec deliberately has no "view rejects value" test.
```

**File:** x/evm/state/balance.go (L64-95)
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
```

**File:** precompiles/bank/legacy/v600/bank.go (L190-193)
```go
	usei, wei, err := pcommon.HandlePaymentUseiWei(ctx, p.evmKeeper.GetSeiAddressOrDefault(ctx, p.address), senderSeiAddr, value, p.bankKeeper, p.evmKeeper, hooks, evm.GetDepth())
	if err != nil {
		return nil, err
	}
```

**File:** precompiles/common/legacy/v552/precompiles.go (L103-120)
```go
func HandlePaymentUseiWei(ctx sdk.Context, precompileAddr sdk.AccAddress, payer sdk.AccAddress, value *big.Int, bankKeeper utils.BankKeeper, evmKeeper utils.EVMKeeper, hooks *tracing.Hooks, depth int) (sdk.Int, sdk.Int, error) {
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

**File:** x/evm/keeper/precompile.go (L13-18)
```go
var payablePrecompiles = map[string]struct{}{
	bank.BankAddress:       {},
	staking.StakingAddress: {},
	gov.GovAddress:         {},
	wasmd.WasmdAddress:     {},
}
```
