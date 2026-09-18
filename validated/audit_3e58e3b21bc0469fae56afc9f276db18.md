### Title
Distribution module sweeps and legitimizes arbitrary attacker-sent tokens via `AllocateTokens` - (File: sei-cosmos/x/distribution/keeper/allocation.go)

### Summary
The `x/distribution` module's `AllocateTokens` function fetches and forwards **all** token balances held by the fee-collector module account, not just the chain's fee denom (`usei`). Because the fee-collector module account has a deterministic, publicly known address, anyone can send arbitrary/malicious tokens (native, IBC, or `tokenfactory` denoms) to it via a simple `MsgSend`. On the next `BeginBlock`, those tokens are automatically swept into the distribution module and split among the block proposer, all bonded validators (as commission/rewards), and the community pool — exactly the "arbitrary token given legitimacy by a trusted distributor" pattern described in the reference report.

### Finding Description
`AllocateTokens` collects the fee collector's balance with `GetAllBalances`, which returns every denom held by the account, and immediately moves it into the distribution module account and accounting structures: [1](#0-0) 

This mirrors the audited `FeeSplitter.distributeFees(_token)` bug exactly: the function operates on `IERC20(_token).balanceOf(address(this))` for *any* caller-supplied token and distributes it to a fixed set of trusted recipients (treasury/reward pool/users). In sei-chain, the analogous "any token that lands in this account gets swept" is `GetAllBalances(ctx, feeCollector.GetAddress())` rather than a denom-filtered query for `usei` alone. There is no check restricting the swept balances to the bonded staking denom.

Since fee-collector is a normal `auth` module account (address deterministically derived from the module name `FeeCollectorName`), it is a valid transfer target for any `MsgSend` originating from any account — no special privilege required. An attacker can:
1. Create/mint a malicious token (native coin via `x/bank`, or a `tokenfactory` denom they control).
2. `MsgSend` some amount of it directly to the fee-collector module account address.
3. Wait for the next block's `BeginBlock`, where `AllocateTokens` runs unconditionally and sweeps *all* balances of the fee-collector account, including the malicious token.
4. The malicious token amount is proportionally allocated as "proposer reward," "voter reward" (to validator `ValidatorOutstandingRewards`/`ValidatorAccumulatedCommission`/`ValidatorCurrentRewards` state), and any remainder goes to the community pool (`FeePool.CommunityPool`), as seen in: [2](#0-1) 

Once recorded, validators/delegators eventually withdraw these balances through `WithdrawValidatorCommission`/`WithdrawDelegatorReward`, and governance can spend community-pool funds via `HandleCommunityPoolSpendProposal`, which sends the swept coins (whatever denom they are, unfiltered) out to real, trusted end recipients: [3](#0-2) [4](#0-3) 

This makes validators, the community pool, and ultimately delegators/users unwitting distributors of an attacker's arbitrary token — the same social-proof/phishing vector as the original report, but reaching validator reward accounting and the on-chain community pool rather than a single contract's fee recipients.

### Impact Explanation
An attacker-controlled token gets automatically absorbed into the protocol's core reward/community-pool accounting every block, without any allow-list or denom filter. This:
- Lends unwarranted legitimacy/social proof to a phishing token, since it appears as validator/community-pool holdings and can be withdrawn by validators to real delegator addresses.
- Permanently pollutes `ValidatorOutstandingRewards`, `ValidatorCurrentRewards`, `ValidatorAccumulatedCommission`, and `FeePool.CommunityPool` state with attacker-chosen denoms indefinitely (the accounting keeps per-denom `DecCoins`, so this can also be repeated to bloat state with many spam denoms).
- Can be repeated at negligible cost (a single `MsgSend`), affecting every validator and the community pool simultaneously each time it is executed.

This matches "Medium" severity: it does not directly cause direct loss of legitimate user funds, but it does enable unauthorized distribution/legitimization of attacker tokens through a trusted, protocol-level distribution mechanism, and creates unbounded low-cost state growth in core consensus accounting.

### Likelihood Explanation
Trivial and always reachable: any unprivileged account can send any coin (including a token created by themselves) to the fee-collector module address, which is a standard, statically-derivable module account address, using a plain bank `MsgSend`. No special permission, precompile, or contract deployment is required, and the sweep executes unconditionally in every block's `BeginBlock` via `AllocateTokens`.

### Recommendation
Restrict `AllocateTokens` to only sweep/allocate the chain's designated fee/staking denom(s) (e.g., `usei`, or the configured `bondDenom`) instead of calling `GetAllBalances`. Any other denom accidentally or maliciously sent to the fee-collector account should either be ignored, refunded, or handled by a separate, denom-restricted governance-controlled process rather than automatically merged into validator rewards and the community pool.

### Proof of Concept
1. Attacker creates a `tokenfactory` denom (e.g., `factory/attacker/PHISH`) or any bank coin they control.
2. Attacker submits `MsgSend` transferring `PHISH` tokens from their account to the fee-collector module account address (`authtypes.NewModuleAddress(authtypes.FeeCollectorName)`).
3. At the next block's `BeginBlock`, `Keeper.AllocateTokens` (`sei-cosmos/x/distribution/keeper/allocation.go:13-105`) runs, calling `k.bankKeeper.GetAllBalances(ctx, feeCollector.GetAddress())`, which now includes the `PHISH` balance alongside `usei`.
4. `SendCoinsFromModuleToModule` moves the entire balance (including `PHISH`) to the distribution module account, and the proposer/voting-power reward loop allocates a fraction of `PHISH` into each active validator's `ValidatorOutstandingRewards`/`ValidatorCurrentRewards`, with the remainder credited to `FeePool.CommunityPool`.
5. Validators subsequently call withdraw operations, or a `CommunityPoolSpendProposal` passes, transferring `PHISH` out to real delegator/recipient addresses — completing the "malicious token given legitimacy by trusted protocol distribution" scenario.

### Citations

**File:** sei-cosmos/x/distribution/keeper/allocation.go (L18-32)
```go
	// fetch and clear the collected fees for distribution, since this is
	// called in BeginBlock, collected fees will be from the previous block
	// (and distributed to the previous proposer)
	feeCollector := k.authKeeper.GetModuleAccount(ctx, k.feeCollectorName)
	feesCollectedInt := k.bankKeeper.GetAllBalances(ctx, feeCollector.GetAddress())
	feesCollected, err := sdk.NewDecCoinsFromCoins(feesCollectedInt...)
	if err != nil {
		panic(err)
	}

	// transfer collected fees to the distribution module account
	err = k.bankKeeper.SendCoinsFromModuleToModule(ctx, k.feeCollectorName, types.ModuleName, feesCollectedInt)
	if err != nil {
		panic(err)
	}
```

**File:** sei-cosmos/x/distribution/keeper/allocation.go (L79-105)
```go
	// calculate fraction allocated to validators
	communityTax := k.GetCommunityTax(ctx)
	voteMultiplier := sdk.OneDec().Sub(proposerMultiplier).Sub(communityTax)
	feeMultiplier := feesCollected.MulDecTruncate(voteMultiplier)

	// allocate tokens proportionally to voting power
	//
	// TODO: Consider parallelizing later
	//
	// Ref: https://github.com/cosmos/cosmos-sdk/pull/3099#discussion_r246276376
	for _, vote := range bondedVotes {
		validator := k.stakingKeeper.ValidatorByConsAddr(ctx, vote.Validator.Address)

		// TODO: Consider micro-slashing for missing votes.
		//
		// Ref: https://github.com/cosmos/cosmos-sdk/issues/2525#issuecomment-430838701
		powerFraction := sdk.NewDec(vote.Validator.Power).QuoTruncate(sdk.NewDec(totalPreviousPower))
		reward := feeMultiplier.MulDecTruncate(powerFraction)

		k.AllocateTokensToValidator(ctx, validator, reward)
		remaining = remaining.Sub(reward)
	}

	// allocate community funding
	feePool.CommunityPool = feePool.CommunityPool.Add(remaining...)
	k.SetFeePool(ctx, feePool)
}
```

**File:** sei-cosmos/x/distribution/keeper/proposal_handler.go (L9-27)
```go
// HandleCommunityPoolSpendProposal is a handler for executing a passed community spend proposal
func HandleCommunityPoolSpendProposal(ctx sdk.Context, k Keeper, p *types.CommunityPoolSpendProposal) error {
	if k.blockedAddrs[p.Recipient] {
		return sdkerrors.Wrapf(sdkerrors.ErrUnauthorized, "%s is not allowed to receive external funds", p.Recipient)
	}

	recipient, err := sdk.AccAddressFromBech32(p.Recipient)
	if err != nil {
		return err
	}

	if err := k.DistributeFromFeePool(ctx, p.Amount, recipient); err != nil {
		return err
	}

	logger.Info("transferred from the community pool to recipient", "amount", p.Amount.String(), "recipient", p.Recipient)

	return nil
}
```

**File:** sei-cosmos/x/distribution/keeper/fee_pool.go (L8-35)
```go
// DistributeFromFeePool distributes funds from the distribution module account to
// a receiver address while updating the community pool
func (k Keeper) DistributeFromFeePool(ctx sdk.Context, amount sdk.Coins, receiveAddr sdk.AccAddress) error {
	feePool := k.GetFeePool(ctx)

	decAmount, err := sdk.NewDecCoinsFromCoins(amount...)
	if err != nil {
		return err
	}

	// NOTE the community pool isn't a module account, however its coins
	// are held in the distribution module account. Thus the community pool
	// must be reduced separately from the SendCoinsFromModuleToAccount call
	newPool, negative := feePool.CommunityPool.SafeSub(decAmount)
	if negative {
		return types.ErrBadDistribution
	}

	feePool.CommunityPool = newPool

	err = k.bankKeeper.SendCoinsFromModuleToAccount(ctx, types.ModuleName, receiveAddr, amount)
	if err != nil {
		return err
	}

	k.SetFeePool(ctx, feePool)
	return nil
}
```
