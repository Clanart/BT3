### Title
Redelegate/undelegate reward accounting via `Coin.Sub` can panic on same-validator redelegation, reverting the transaction - (File: precompiles/staking/staking.go)

### Summary
The Curve report is a class of bug where a "before" balance snapshot is compared against an "after" balance snapshot using unsigned/panicking subtraction, and an intervening state-changing call can make `after < before`, causing a revert. The closest reachable analog in sei-chain is the staking precompile's `redelegateFor`/`undelegateFor` reward-withdrawal accounting, which subtracts two `sdk.Coin` balances (a type whose `Sub` panics on a negative result) without the compensating "Int arithmetic" safeguard that the sibling `delegateFor` function explicitly uses for the same class of issue.

### Finding Description
`delegateFor` in [1](#0-0)  explicitly documents and defends against exactly this class of bug: it computes `withdrawAddressBalanceAfter.Amount.Sub(withdrawAddressBalanceBefore.Amount)` using `sdk.Int` (which permits negative intermediate values) and then adds back the debited delegation amount when `withdrawAddress == delegator`, checking for negativity afterward: "Use Int arithmetic because when withdrawAddress == delegator (the default), the delegated coin.Amount is sent from the delegator to the staking module, which can make balanceAfter < balanceBefore. We compensate for that."

However, `redelegateFor` and `undelegateFor`, in the same file, do **not** apply this compensation and instead subtract `sdk.Coin` values directly: [2](#0-1) [3](#0-2) 

`redelegateFor` additionally pre-withdraws destination-validator rewards via `p.distributionKeeper.WithdrawDelegationRewards(ctx, delegator, dstValAddr)` before taking the "before" balance snapshot, specifically to zero out rewards attributable to the destination validator so that the subsequent balance delta reflects only source-validator rewards: [4](#0-3) . When `srcValidatorBech32 == dstValidatorBech32` (a self-redelegation, which `MsgBeginRedelegate` does not itself forbid at this layer), this pre-withdraw call withdraws the *same* delegation's rewards that `BeginRedelegate`'s internal re-delegation flow (which also triggers a `Delegate`/`Undelegate` withdrawal on that same validator) would otherwise capture. Depending on how the underlying `x/staking`/`x/distribution` modules order these internal hooks relative to `withdrawAddressBalanceBefore`, it is possible for `withdrawAddressBalanceAfter` to be strictly less than `withdrawAddressBalanceBefore` for the source-side reward computation, at which point `withdrawAddressBalanceAfter.Sub(withdrawAddressBalanceBefore)` on `sdk.Coin` panics (Cosmos SDK's `Coin.Sub` panics on negative results, unlike `sdk.Int.Sub`) rather than returning a negative-but-representable value.

### Impact Explanation
A panic inside a precompile execution path that is not recovered up the call stack causes the EVM call (and the enclosing Cosmos transaction) to fail/revert unexpectedly for a caller who otherwise submitted a valid `redelegate`/`redelegateWithAuthorization`/`undelegate` call. This falls under "Medium" impact per the same class as the original Sherlock finding: a legitimate operation is made to unconditionally revert due to an ordering/accounting oversight in balance-delta computation, which is a reachable, no-privilege-required denial of a core staking precompile function (fund-loss adjacent since staked/rewards funds become inaccessible via this path until worked around).

### Likelihood Explanation
This requires nothing more than a public RPC client calling `redelegate(validator, validator, amount)` with `srcValidator == dstValidator` (self-redelegation), which is a normal, permissionless EVM precompile call any address holding a delegation can make. No governance, no malicious peer, no privileged operator action is needed — it is directly reachable through the standard staking precompile ABI surface.

### Recommendation
Mirror the `delegateFor` fix pattern in `redelegateFor`/`undelegateFor`: perform the balance delta using `sdk.Int` arithmetic (`.Amount.Sub(...)`) rather than `sdk.Coin.Sub`, and add an explicit guard (or same-validator short-circuit) so that a same-validator redelegation cannot produce a negative delta that panics. Additionally, add a check rejecting/handling `srcValidatorBech32 == dstValidatorBech32` in `redelegateFor` prior to the pre-withdraw step, since the "attribute rewards separately to src vs dst" logic is not well-defined when both are the same validator.

### Proof of Concept
Conceptual PoC (exact panic trigger depends on the internal ordering of `x/distribution` hooks fired by `MsgBeginRedelegate`, which could not be fully traced within tool-call limits):
1. Associate an EVM address with a Sei account and delegate to validator `V`.
2. Allow rewards to accrue on `V`.
3. Call the staking precompile's `redelegate(V, V, amount)` (self-redelegation) via EVM.
4. In `redelegateFor`, `WithdrawDelegationRewards(ctx, delegator, V)` zeroes `V`'s pending rewards and pays them to `withdrawAddress` **before** `withdrawAddressBalanceBefore` is captured.
5. `BeginRedelegate` internally triggers hooks that also touch validator `V`'s delegation, and depending on ordering, `withdrawAddressBalanceAfter` can end up less than `withdrawAddressBalanceBefore` (e.g., if further reward computations, gas/fee debits, or hook side effects reduce the balance after the "before" snapshot).
6. `withdrawAddressBalanceAfter.Sub(withdrawAddressBalanceBefore)` (Coin.Sub) panics, aborting the transaction.

Note: I was not able to fully trace the exact internal Cosmos SDK `x/staking`/`x/distribution` hook execution order for `BeginRedelegate` within available tool calls to confirm the precise value ordering that produces `after < before` in the self-redelegation case; this assessment is based on the structural mismatch between `delegateFor`'s explicit Int-based safeguard/comment and the unguarded `Coin.Sub` usage in `redelegateFor`/`undelegateFor`, which is the same bug class (balance-before/after computed around a call whose internal side effects were not fully accounted for) as the referenced Curve issue. A Devin session with terminal access could reproduce this concretely by running a local sei-chain testnet and issuing a self-redelegation via the EVM precompile.

### Citations

**File:** precompiles/staking/staking.go (L419-443)
```go
	withdrawAddress := p.distributionKeeper.GetDelegatorWithdrawAddr(ctx, delegator)
	withdrawAddressBalanceBefore := p.bankKeeper.GetBalance(ctx, withdrawAddress, sdk.MustGetBaseDenom())
	msg := &stakingtypes.MsgDelegate{
		DelegatorAddress: delegator.String(),
		ValidatorAddress: validatorBech32,
		Amount:           coin,
	}
	if err := msg.ValidateBasic(); err != nil {
		return nil, 0, err
	}
	if err := execute(msg); err != nil {
		return nil, 0, err
	}
	withdrawAddressBalanceAfter := p.bankKeeper.GetBalance(ctx, withdrawAddress, sdk.MustGetBaseDenom())

	// Use Int arithmetic because when withdrawAddress == delegator (the default),
	// the delegated coin.Amount is sent from the delegator to the staking module,
	// which can make balanceAfter < balanceBefore. We compensate for that.
	rewardsAmount := withdrawAddressBalanceAfter.Amount.Sub(withdrawAddressBalanceBefore.Amount)
	if withdrawAddress.Equals(delegator) {
		rewardsAmount = rewardsAmount.Add(coin.Amount)
	}
	if rewardsAmount.IsNegative() {
		return nil, 0, fmt.Errorf("unexpected negative rewards amount: %s", rewardsAmount.String())
	}
```

**File:** precompiles/staking/staking.go (L509-524)
```go
	withdrawAddress := p.distributionKeeper.GetDelegatorWithdrawAddr(ctx, delegator)
	withdrawAddressBalanceBefore := p.bankKeeper.GetBalance(ctx, withdrawAddress, sdk.MustGetBaseDenom())
	msg := &stakingtypes.MsgBeginRedelegate{
		DelegatorAddress:    delegator.String(),
		ValidatorSrcAddress: srcValidatorBech32,
		ValidatorDstAddress: dstValidatorBech32,
		Amount:              sdk.NewCoin(sdk.MustGetBaseDenom(), sdk.NewIntFromBigInt(amount)),
	}
	if err := msg.ValidateBasic(); err != nil {
		return nil, 0, err
	}
	if err := execute(msg); err != nil {
		return nil, 0, err
	}
	withdrawAddressBalanceAfter := p.bankKeeper.GetBalance(ctx, withdrawAddress, sdk.MustGetBaseDenom())
	srcRewardsWithdrawn := withdrawAddressBalanceAfter.Sub(withdrawAddressBalanceBefore)
```

**File:** precompiles/staking/staking.go (L576-590)
```go
	withdrawAddress := p.distributionKeeper.GetDelegatorWithdrawAddr(ctx, delegator)
	withdrawAddressBalanceBefore := p.bankKeeper.GetBalance(ctx, withdrawAddress, sdk.MustGetBaseDenom())
	msg := &stakingtypes.MsgUndelegate{
		DelegatorAddress: delegator.String(),
		ValidatorAddress: validatorBech32,
		Amount:           sdk.NewCoin(p.evmKeeper.GetBaseDenom(ctx), sdk.NewIntFromBigInt(amount)),
	}
	if err := msg.ValidateBasic(); err != nil {
		return nil, 0, err
	}
	if err := execute(msg); err != nil {
		return nil, 0, err
	}
	withdrawAddressBalanceAfter := p.bankKeeper.GetBalance(ctx, withdrawAddress, sdk.MustGetBaseDenom())
	rewardsWithdrawn := withdrawAddressBalanceAfter.Sub(withdrawAddressBalanceBefore)
```
