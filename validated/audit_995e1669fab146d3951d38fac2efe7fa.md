### Title
Tokenfactory `MsgUpdateDenom` allow-list lets a denom admin permanently freeze another account's already-held tokens - ([File: x/tokenfactory/keeper/msg_server.go])

### Summary
The Lavarage report warns that SPL collateral tokens can carry a mint-level `freeze_authority` that lets the token issuer unilaterally freeze any holder's account, DoSing borrow/repay flows and permanently locking funds. sei-chain's tokenfactory module has a functionally equivalent primitive: the denom admin can call `MsgUpdateDenom` at any time after tokens have already been minted and distributed, to attach a bank `AllowList` to the denom. Once set, `x/bank`'s `IsInDenomAllowList` check blocks any address not in the list from sending that denom — exactly like an SPL "freeze" — and this restriction is enforced retroactively against balances any account (including CosmWasm contracts, module escrow accounts, or precompile-backed pools) already holds.

### Finding Description
`MsgUpdateDenom` is handled by `msgServer.UpdateDenom`, which only requires that `msg.Sender` equal the denom's current admin (`authorityMetadata.GetAdmin()`), then calls `server.bankKeeper.SetDenomAllowList(ctx, denom, *msg.AllowList)` with no restriction on the size/membership relative to existing holders. [1](#0-0) 

`validateUpdateDenom`/`validateAllowList` only check bech32-formatting and list size — they never check whether existing token holders (accounts that already hold a balance of the denom from before the allow-list was applied) are included in the new list. [2](#0-1) 

The enforcement point is `BaseSendKeeper.IsInDenomAllowList`, which is consulted on `bank` sends for any `factory/...` denom: if an allow list exists for the denom and the sender address isn't in it, the send is rejected outright. [3](#0-2) 

Because a tokenfactory denom is a first-class `sdk.Coin` denom, it can be held by:
- CosmWasm contracts (e.g., an escrow/lending/AMM pool contract that accepts the tokenfactory denom as collateral or LP tokens),
- CW20↔bank "pointer" pathways and module accounts,
- any regular Cosmos or EVM-associated address that received the denom via `bank.Send`.

If the admin (the original tokenfactory denom creator) later calls `MsgUpdateDenom` with an allow-list that omits one of these already-funded addresses (a pool contract's account, a user, etc.), that address becomes permanently unable to move its balance of the denom — the exact "freeze" primitive described in the report, except triggered via a native Cosmos message rather than an SPL `FreezeAccount` instruction. The finding is analogous but not identical to the original SPL bug: the original issue is that *any* SPL token with freeze authority is accepted as collateral without checking for that authority; here, the tokenfactory module itself grants an update-denom capability with no protections for pre-existing balances, meaning any downstream Sei module/contract that trusts tokenfactory-denominated assets (uses them as collateral, LP shares, escrow, etc.) inherits the same freeze risk with no mitigation path — there is no equivalent of "renouncing" the allow-list capability once minted tokens are in circulation (an admin can re-add an empty list later, but nothing stops them from reintroducing restrictions, and in the interim funds are stuck).

### Impact Explanation
Any account — user wallet, CosmWasm contract holding escrowed/collateral tokenfactory coins, or a Cosmos module account interacting with a tokenfactory denom — can have its balance of that denom permanently frozen by the denom's admin via a single `MsgUpdateDenom` transaction that adds a restrictive allow-list excluding that address. This causes concrete, permanent loss/lock of funds for innocent holders who have no way to reach or unfreeze their balance (mirroring the judge's rationale in the original report: rare but avoidable and results in real losses to innocent users). Because this is enforced at the `x/bank` send layer, it affects every module and contract that composes with tokenfactory assets — DeFi protocols building on Sei that accept tokenfactory coins as collateral inherit this systemic freeze risk without any on-chain signal (there's no way to check "is this denom freezable/has an allow list already been exercised" before accepting a deposit) — Medium severity, consistent with the original finding's assessed severity.

### Likelihood Explanation
The tokenfactory module is fully permissionless — anyone can create a denom via `MsgCreateDenom` (no `allow_list` needed initially) and distribute it, then later call `MsgUpdateDenom` to add an allow-list. This requires only the admin's own signature (no governance, no multi-party consent), making it trivially and cheaply reachable by any unprivileged user/contract that created the denom. This is a normal, single-account-driven and always-available code path, not a rare edge condition — likelihood is comparable to or higher than the original SPL report's (which relied on a token's issuer never renouncing freeze authority — here it's a first-class supported message with no additional cost).

### Recommendation
Introduce protections analogous to the report's "no active freeze authority" mitigation:
- Disallow `MsgUpdateDenom` from excluding addresses that currently hold a nonzero balance of the denom (or require the update to fail/require an explicit unlock mechanism for existing balances), or
- Make allow-list restrictions apply only prospectively (only affecting balances acquired after the update) rather than retroactively freezing existing holders, or
- Emit an immutable, queryable "freezable" flag on tokenfactory denoms at creation time (whether `allow_list`/`MsgUpdateDenom` capability is permanently disabled), so downstream protocols (lending/AMM/escrow contracts) can check this before accepting a tokenfactory denom as collateral, mirroring the "ensure the token cannot have an active freeze authority" recommendation from the original report.

### Proof of Concept
1. Admin `A` creates denom `factory/A/xyz` via `MsgCreateDenom` (no allow-list). [4](#0-3) 
2. Admin mints and sends `factory/A/xyz` to a lending/escrow contract account `C` (e.g., a CosmWasm collateral pool) via `mintTo`/`bank.Send`. [5](#0-4) 
3. Admin `A` calls `MsgUpdateDenom` on `factory/A/xyz` with an `AllowList` that does not include `C`'s address; this succeeds because only the size/format of the list is validated. [6](#0-5) 
4. Any subsequent attempt by `C` to send/withdraw its `factory/A/xyz` balance (e.g., repay/borrow flow, LP withdrawal) is rejected by `IsInDenomAllowList`, permanently freezing `C`'s funds and DoSing any protocol logic depending on moving that balance. [3](#0-2)

### Citations

**File:** x/tokenfactory/keeper/msg_server.go (L23-55)
```go
func (server msgServer) CreateDenom(goCtx context.Context, msg *types.MsgCreateDenom) (*types.MsgCreateDenomResponse, error) {
	ctx := sdk.UnwrapSDKContext(goCtx)

	denom, err := server.Keeper.CreateDenom(ctx, msg.Sender, msg.Subdenom)
	if err != nil {
		return nil, err
	}

	createDenomEvent := sdk.NewEvent(
		types.TypeMsgCreateDenom,
		sdk.NewAttribute(types.AttributeCreator, msg.Sender),
		sdk.NewAttribute(types.AttributeNewTokenDenom, denom),
	)

	if msg.AllowList != nil {
		err = server.validateAllowList(ctx, msg.AllowList)
		if err != nil {
			return nil, err
		}
		server.bankKeeper.SetDenomAllowList(ctx, denom, *msg.AllowList)
		createDenomEvent = createDenomEvent.AppendAttributes(
			sdk.NewAttribute(types.AttributeAllowList, strings.Join(msg.AllowList.Addresses, ",")),
		)
	}

	ctx.EventManager().EmitEvents(sdk.Events{
		createDenomEvent,
	})

	return &types.MsgCreateDenomResponse{
		NewTokenDenom: denom,
	}, nil
}
```

**File:** x/tokenfactory/keeper/msg_server.go (L57-91)
```go
func (server msgServer) UpdateDenom(goCtx context.Context, msg *types.MsgUpdateDenom) (*types.MsgUpdateDenomResponse, error) {
	ctx := sdk.UnwrapSDKContext(goCtx)

	denom, err := server.validateUpdateDenom(ctx, msg)
	if err != nil {
		return nil, err
	}

	authorityMetadata, err := server.GetAuthorityMetadata(ctx, denom)
	if err != nil {
		return nil, err
	}

	if msg.Sender != authorityMetadata.GetAdmin() {
		return nil, types.ErrUnauthorized
	}

	updateDenomEvent := sdk.NewEvent(
		types.TypeMsgUpdateDenom,
		sdk.NewAttribute(types.AttributeCreator, msg.Sender),
		sdk.NewAttribute(types.AttributeUpdatedTokenDenom, denom),
	)

	if msg.AllowList != nil {
		server.bankKeeper.SetDenomAllowList(ctx, denom, *msg.AllowList)
		updateDenomEvent = updateDenomEvent.AppendAttributes(
			sdk.NewAttribute(types.AttributeAllowList, strings.Join(msg.AllowList.Addresses, ",")),
		)
	}

	ctx.EventManager().EmitEvents(sdk.Events{
		updateDenomEvent,
	})

	return &types.MsgUpdateDenomResponse{}, nil
```

**File:** x/tokenfactory/keeper/createdenom.go (L72-114)
```go
func (k Keeper) validateUpdateDenom(ctx sdk.Context, msg *types.MsgUpdateDenom) (tokenDenom string, err error) {
	_, _, err = types.DeconstructDenom(msg.GetDenom())
	if err != nil {
		return "", err
	}
	_, found := k.bankKeeper.GetDenomMetaData(ctx, msg.GetDenom())
	if !found {
		return "", types.ErrDenomDoesNotExist.Wrapf("denom: %s", msg.GetDenom())
	}

	err = k.validateAllowList(ctx, msg.AllowList)
	if err != nil {
		return "", err
	}

	return msg.GetDenom(), nil
}

func (k Keeper) validateAllowListSize(ctx sdk.Context, allowList *banktypes.AllowList) error {
	if allowList == nil {
		return types.ErrAllowListUndefined
	}

	if len(allowList.Addresses) > int(k.GetDenomAllowListMaxSize(ctx)) {
		return types.ErrAllowListTooLarge
	}
	return nil
}

func (k Keeper) validateAllowList(ctx sdk.Context, allowList *banktypes.AllowList) error {
	err := k.validateAllowListSize(ctx, allowList)
	if err != nil {
		return err
	}

	// validate all addresses in the allow list are bech32
	for _, addr := range allowList.Addresses {
		if _, err = sdk.AccAddressFromBech32(addr); err != nil {
			return fmt.Errorf("invalid address %s: %w", addr, err)
		}
	}
	return nil
}
```

**File:** sei-cosmos/x/bank/keeper/send.go (L501-524)
```go
// IsInDenomAllowList checks if the given address is allowed to send the given coins.
// The check is performed only fot token factory denoms. For each token factory denom,
// it checks if there is allow list for the given denom. If there is no allow list,
// the address is allowed to send the coins. If there is an allow list, the address is
// allowed to send the coins only if it is in the allow list.
func (k BaseSendKeeper) IsInDenomAllowList(ctx sdk.Context, addr sdk.AccAddress, coins sdk.Coins, cache map[string]AllowedAddresses) bool {
	for _, coin := range coins {
		// Skip if denom does not contain the token factory prefix
		if !strings.HasPrefix(coin.Denom, TokenFactoryPrefix) {
			continue
		}

		allowedAddresses := k.getAllowedAddresses(ctx, cache, coin.Denom)
		// skip if there is no allow list for the denom
		if len(allowedAddresses.set) == 0 {
			continue
		}

		if !allowedAddresses.contains(addr) {
			return false
		}
	}
	return true
}
```

**File:** x/tokenfactory/keeper/bankactions.go (L11-33)
```go
func (k Keeper) mintTo(ctx sdk.Context, amount sdk.Coin, mintTo string) error {
	// verify that denom is an x/tokenfactory denom
	_, _, err := types.DeconstructDenom(amount.Denom)
	if err != nil {
		return err
	}

	logger.Info("Minting amount for module", "amount", amount, "module", types.ModuleName)
	err = k.bankKeeper.MintCoins(ctx, types.ModuleName, sdk.NewCoins(amount))
	if err != nil {
		return err
	}

	addr, err := sdk.AccAddressFromBech32(mintTo)
	if err != nil {
		return err
	}

	logger.Info("Sending minted amount to addr", "amount", amount, "addr", addr)
	return k.bankKeeper.SendCoinsFromModuleToAccount(ctx, types.ModuleName,
		addr,
		sdk.NewCoins(amount))
}
```
