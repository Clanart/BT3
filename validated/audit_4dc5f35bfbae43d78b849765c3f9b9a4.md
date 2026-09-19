## Title
Tokenfactory denom admin can unilaterally mint unlimited supply, diluting/rug-pulling existing holders - ([File: x/tokenfactory/keeper/msg_server.go])

### Summary
The tokenfactory module grants the creator/admin of a denom unchecked, permanent authority to mint new supply of that denom to themselves at any time, with no cap, no community consent, and no on-chain check tied to backing collateral or economic assumptions. This mirrors the Folio `Role::Owner` rug-pull pattern: a single privileged role, without making any code change, can unilaterally decrease the economic value held by every other holder of the asset — in the tokenfactory case via supply inflation/dilution rather than basket asset removal.

### Finding Description
`MsgMint` is authorized solely by checking that the caller equals the denom's current admin, then unconditionally minting the requested amount to the admin's own account with no bound on issuable supply: [1](#0-0) 

The underlying `mintTo` helper performs no supply cap, backing check, or governance gate — it simply mints via the bank keeper and sends to the admin: [2](#0-1) 

The module's own documentation acknowledges this admin has essentially unilateral economic control over holders of the denom: mint to any account, burn from any account (via their own transfers), and reassign/relinquish admin rights, with "more admin capabilities may be added" in the future: [3](#0-2) 

This is structurally identical to the Folio finding: a role (`Role::Owner` in Folio, tokenfactory "admin" here) that ordinary users must trust holds power to decrease the economic value of a token held by others, purely by invoking a normal, permissionless message (`MsgMint`), without any code change to the module. Just as a Folio Owner can call `RemoveFromBasket` to strip collateral backing DTF shares, a tokenfactory denom admin can call `MsgMint` repeatedly to dilute the token supply, destroying the proportional value held by every other token holder who was not warned this was possible.

### Impact Explanation
This matches the "supply inflation" impact criterion. Any tokenfactory denom (which is fully permissionless to create — `factory/{creator}/{subdenom}`) can attract holders/liquidity under the assumption of a fixed or admin-limited supply schedule. The admin can then mint arbitrary amounts to themselves, diluting all other holders' economic stake to near zero and effectively rug-pulling them, exactly analogous to the Folio Owner draining basket collateral to zero out share value.

### Likelihood Explanation
High likelihood of exploitation by a malicious denom creator: tokenfactory denom creation and the resulting admin role are fully permissionless and reachable by any unprivileged transaction sender via `MsgCreateDenom` and subsequent `MsgMint` calls — no validator or governance action is required.

### Recommendation
- **Short term:** Prominently document, both in module docs and any UI/indexer surfacing tokenfactory denoms, that a denom's admin has unilateral, uncapped minting authority and must be treated as a trusted, reputable party; surface the current admin and mint history to end users before they hold or trade a tokenfactory-issued asset.
- **Long term:** Consider optional supply-cap or timelock/governance-gated minting primitives within tokenfactory that projects can opt into, and document ABC-Labs-style threat model assumptions for `x/tokenfactory` admin privileges so integrators can assess counterparty risk before relying on a given denom.

### Proof of Concept
1. Attacker calls `MsgCreateDenom` to create `factory/{attacker}/token`, becoming its admin (`CreateDenom` handler auto-assigns admin = creator).
2. Attacker markets/distributes the token, or lists it on a DEX/lending market, accumulating real, unaffiliated holders.
3. Attacker repeatedly submits `MsgMint` messages, minting large amounts of the denom to their own address: [4](#0-3) 
4. Attacker sells/dumps the newly minted supply against any liquidity pool holding the denom, or simply now controls the overwhelming majority of supply — diluting all pre-existing holders' proportional value, without any code change or additional authorization beyond the pre-existing admin role.

### Citations

**File:** x/tokenfactory/keeper/msg_server.go (L94-126)
```go
func (server msgServer) Mint(goCtx context.Context, msg *types.MsgMint) (*types.MsgMintResponse, error) {
	ctx := sdk.UnwrapSDKContext(goCtx)

	// pay some extra gas cost to give a better error here.
	_, denomExists := server.bankKeeper.GetDenomMetaData(ctx, msg.Amount.Denom)
	if !denomExists {
		return nil, types.ErrDenomDoesNotExist.Wrapf("denom: %s", msg.Amount.Denom)
	}

	authorityMetadata, err := server.GetAuthorityMetadata(ctx, msg.Amount.GetDenom())
	if err != nil {
		return nil, err
	}

	if msg.Sender != authorityMetadata.GetAdmin() {
		return nil, types.ErrUnauthorized
	}

	err = server.mintTo(ctx, msg.Amount, msg.Sender)
	if err != nil {
		return nil, err
	}

	ctx.EventManager().EmitEvents(sdk.Events{
		sdk.NewEvent(
			types.TypeMsgMint,
			sdk.NewAttribute(types.AttributeMintToAddress, msg.Sender),
			sdk.NewAttribute(types.AttributeAmount, msg.Amount.String()),
		),
	})

	return &types.MsgMintResponse{}, nil
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

**File:** x/tokenfactory/README.md (L1-18)
```markdown
# Token Factory

The tokenfactory module allows any account to create a new token with
the name `factory/{creator address}/{subdenom}`. Because tokens are
namespaced by creator address, this allows token minting to be
permissionless, due to not needing to resolve name collisions. A single
account can create multiple denoms, by providing a unique subdenom for each
created denom. Once a denom is created, the original creator is given
"admin" privileges over the asset. This allows them to:

- Mint their denom to any account
- Burn their denom from any account
- Create a transfer of their denom between any two accounts
- Change the admin. In the future, more admin capabilities may be added. Admins
  can choose to share admin privileges with other accounts using the authz
  module. The `ChangeAdmin` functionality, allows changing the master admin
  account, or even setting it to `""`, meaning no account has admin privileges
  of the asset.
```
