## Title
Unchecked `Coin.Sub` on withdraw-address balance delta panics `redelegate`/`undelegate` in the staking precompile, permanently DoS'ing those calls for accounts with a diverging withdraw address - (File: `precompiles/staking/staking.go`)

### Summary
The staking precompile's `redelegateFor` and `undelegateFor` compute "rewards withdrawn" by snapshotting the withdraw address's bank balance before and after executing the underlying staking message, then subtracting the two `sdk.Coin` values directly. Unlike the sibling `delegateFor` function, these two paths perform no negative-delta guard before the subtraction, so any bank-balance decrease of the withdraw address that occurs as a side effect of message execution causes a panic instead of a graceful error, exactly mirroring the JPEG'd `currentBalance < previousBalance` root cause.

### Finding Description
`delegateFor` explicitly acknowledges that the withdraw-address balance is not guaranteed to be monotonically increasing and defends against it: [1](#0-0) 

It converts to `sdk.Int` arithmetic, compensates for the known case where `withdrawAddress == delegator` (since the delegated stake itself leaves the bank balance), and returns an explicit error if the result is still negative rather than letting the runtime panic.

`redelegateFor` and `undelegateFor`, by contrast, use `sdk.Coin.Sub` directly on the before/after balances with no such guard: [2](#0-1) [3](#0-2) 

`sdk.Coin.Sub` panics whenever the result would be negative: [4](#0-3) 

This is the same architectural flaw the JPEG'd report describes: code assumes a "before/after" balance comparison is always non-decreasing and uses unchecked subtraction, so any legitimate reason for the balance to decrease (a different reward-accounting path, a slashing/jailing side effect processed synchronously inside `BeginRedelegate`/`Undelegate`, a blocked/module withdraw address whose balance is drained by an unrelated hook in the same message, or a future change to distribution/staking behavior that debits the withdraw address) turns a normal call into a panic. The `delegate` precompile method was already patched for exactly this class of issue (note the comment referencing the delegated-amount leaving the bank balance), but `redelegate` and `undelegate` were left with the unguarded subtraction, i.e., this is a known-and-partially-fixed bug class that was not applied consistently across all three staking precompile entry points.

Because a delegator fully controls whether they call `setWithdrawAddress` to point at any account (including their own EVM-associated account, a contract account, or another account they also otherwise interact with in the same call graph via `authorizedStakingExecutor`), and because the vulnerable code path is reached unconditionally on every `redelegate`/`undelegate`/`redelegateWithAuthorization`/`undelegateWithAuthorization` precompile call, this is directly reachable by any unprivileged EVM transaction sender.

### Impact Explanation
If a delegator's configured withdraw address ends up with a lower balance after the `BeginRedelegate`/`Undelegate` staking-keeper call than before it (for any of the enumerated reasons), `withdrawAddressBalanceAfter.Sub(withdrawAddressBalanceBefore)` panics. Because this happens after the underlying `stakingtypes.MsgBeginRedelegate` / `MsgUndelegate` has already been executed and applied to state (shares moved, unbonding entries created), a panic at this point either aborts the whole EVM message (reverting state via the EVM/Cosmos SDK panic-recovery in `baseapp`), which just wastes gas, or — more importantly — if the same withdraw-address condition recurs deterministically on every subsequent redelegate/undelegate attempt for that delegator (e.g., a permanently configured withdraw address such as a module account or a contract with drain behavior), it permanently blocks the delegator from ever completing a `redelegate` or `undelegate` call through the precompile, freezing their ability to move or unbond their stake via this path. This matches the accepted impact class of permanent freezing of user funds/functionality caused by an unguarded balance-delta subtraction, the same bug class validated as High/Medium in the original JPEG'd finding.

### Likelihood Explanation
The vulnerable arithmetic executes on every call to `redelegate`, `redelegateWithAuthorization`, `undelegate`, and `undelegateWithAuthorization` in the staking precompile — a surface directly reachable by any EVM transaction sender with an associated Sei address. The exact conditions needed to make `withdrawAddressBalanceAfter < withdrawAddressBalanceBefore` deterministically (e.g., specific withdraw-address configurations interacting with concurrent distribution/slashing bookkeeping) were not concretely reproducible in the available code paths in this pass, and the sei-cosmos `BeginRedelegate`/`Undelegate` keeper implementations were not fully inspected for bank-balance-decreasing side effects. Given that `delegateFor` needed an explicit guard/comment for exactly this direction-of-balance-change concern, it is likely that `redelegateFor`/`undelegateFor` share at least one reachable trigger, but full confirmation would require verifying the sei-cosmos staking keeper's redelegate/undelegate implementations for any bank debit against the withdraw address (this could not be completed within this session due to index/time limits).

### Recommendation
Apply the same guard used in `delegateFor` to `redelegateFor` and `undelegateFor`: compute the balance delta in `sdk.Int` arithmetic, and if the result is negative, return an explicit error (`fmt.Errorf("unexpected negative rewards amount: %s", ...)`) instead of calling `sdk.Coin.Sub`, which panics on a negative result.

### Proof of Concept
Concrete, deterministic reproduction of a withdraw-address balance decrease inside `BeginRedelegate`/`Undelegate` could not be fully constructed with the available context (this session did not confirm a bank-balance-debiting side effect inside the sei-cosmos staking keeper's `BeginRedelegate`/`Undelegate`). The code-level root cause — `redelegateFor`/`undelegateFor` using unguarded `sdk.Coin.Sub` on a before/after balance snapshot while the sibling `delegateFor` explicitly guards against exactly this negative-delta case — is directly cited above and is the exact bug class (unguarded "currentBalance < previousBalance" subtraction) described in the external report.

### Citations

**File:** precompiles/staking/staking.go (L432-443)
```go
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

**File:** sei-cosmos/types/coin.go (L105-118)
```go
// Sub subtracts amounts of two coins with same denom. If the coins differ in denom
// then it panics.
func (coin Coin) Sub(coinB Coin) Coin {
	if coin.Denom != coinB.Denom {
		panic(fmt.Sprintf("invalid coin denominations; %s, %s", coin.Denom, coinB.Denom))
	}

	res := Coin{coin.Denom, coin.Amount.Sub(coinB.Amount)}
	if res.IsNegative() {
		panic("negative coin amount")
	}

	return res
}
```
