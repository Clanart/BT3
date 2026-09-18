Based on the analog bug-class (missing zero-address validation on a privileged setter, as in `LPToken.set_minter()`), I found a matching pattern in the tokenfactory module's admin-change flow.

### Title
`MsgChangeAdmin` accepts an unusable/zero `NewAdmin` address, permanently freezing denom admin capability - (File: x/tokenfactory/types/msgs.go, x/tokenfactory/keeper/admins.go)

### Summary
The `x/tokenfactory` module's `MsgChangeAdmin` message lets the current admin of a `factory/{creator}/{subdenom}` denom transfer admin rights to a new address. Neither `MsgChangeAdmin.ValidateBasic()` nor the keeper's `setAdmin` function reject a syntactically-valid but practically unusable address (e.g., the all-zero 20-byte address, or any other bech32-valid address with no known/controllable private key) as the new admin.

### Finding Description
`MsgChangeAdmin.ValidateBasic()` only verifies that `Sender` and `NewAdmin` are well-formed bech32 addresses via `sdk.AccAddressFromBech32`, with no additional check preventing `NewAdmin` from being the zero address: [1](#0-0) 

The `ChangeAdmin` msg-server handler enforces sender authorization and that `NewAdmin != currentAdmin`, but performs no zero-address check before calling `setAdmin`: [2](#0-1) 

`setAdmin` itself simply overwrites the stored `AuthorityMetadata.Admin` field with whatever string was supplied, with no validation beyond `metadata.Validate()` (which only checks bech32 well-formedness, not usability): [3](#0-2) 

This is distinct from the intentionally-supported "burn admin" path via `MsgClearAdmin`/`""`, which the module documentation explicitly describes as a supported way to remove admin privileges entirely: [4](#0-3) 

Setting `NewAdmin` to the zero address (or any other bech32-valid address nobody controls) is not a documented/intended feature — it's indistinguishable from a fat-fingered address by the current admin, exactly mirroring the `LPToken.set_minter()` bug class: a privileged setter that doesn't reject a "black hole" address before committing it as the new authority.

### Impact Explanation
Once `NewAdmin` is set to an uncontrolled address, all subsequent admin-gated operations for that denom (`Mint`, `Burn`, `ForceTransfer`, `SetDenomMetadata`, and any further `ChangeAdmin`) become permanently unusable, since `ChangeAdmin` requires `msg.Sender == authorityMetadata.GetAdmin()` and nobody can produce a valid signature from the zero address: [5](#0-4) 

This permanently freezes the denom's minting/burning/administration capability — matching the accepted "permanent freezing" impact class.

### Likelihood Explanation
Likelihood is low-to-moderate: it requires the denom's current admin to submit a `MsgChangeAdmin` transaction with a mistaken or malformed destination address (analogous to the original `LPToken.set_minter()` report, which is also a self-inflicted-mistake class of bug). No special privilege beyond being the current admin is required, and the transaction is a single, ordinary user-submitted message.

### Recommendation
Add an explicit check in `MsgChangeAdmin.ValidateBasic()` (and/or in the keeper's `setAdmin`/`ChangeAdmin` handler) to reject `NewAdmin` equal to the zero address (`bytes.Repeat([]byte{0}, AccAddressLen)` equivalent) before persisting it, distinguishing this from the intentional "clear admin" (`""`) path already provided by `MsgClearAdmin`.

### Proof of Concept
1. Account `A` creates a tokenfactory denom via `MsgCreateDenom`, becoming its admin.
2. Account `A` submits `MsgChangeAdmin{Sender: A, Denom: denom, NewAdmin: sdk.AccAddress(make([]byte, 20)).String()}` (bech32 encoding of the all-zero address).
3. `ValidateBasic` passes because the zero address is bech32-valid; `ChangeAdmin` handler passes the `Sender == currentAdmin` and `NewAdmin != currentAdmin` checks and calls `setAdmin`, storing the zero address as the new admin.
4. Any subsequent `MsgMint`, `MsgBurn`, `MsgChangeAdmin`, or `MsgSetDenomMetadata` for `denom` now requires a signature from the zero address, which is unobtainable — the denom's admin functionality is permanently frozen.

### Citations

**File:** x/tokenfactory/types/msgs.go (L171-188)
```go
func (m MsgChangeAdmin) ValidateBasic() error {
	_, err := sdk.AccAddressFromBech32(m.Sender)
	if err != nil {
		return sdkerrors.Wrapf(sdkerrors.ErrInvalidAddress, "Invalid sender address (%s)", err)
	}

	_, err = sdk.AccAddressFromBech32(m.NewAdmin)
	if err != nil {
		return sdkerrors.Wrapf(sdkerrors.ErrInvalidAddress, "Invalid address (%s)", err)
	}

	_, _, err = DeconstructDenom(m.Denom)
	if err != nil {
		return err
	}

	return nil
}
```

**File:** x/tokenfactory/keeper/msg_server.go (L156-186)
```go
func (server msgServer) ChangeAdmin(goCtx context.Context, msg *types.MsgChangeAdmin) (*types.MsgChangeAdminResponse, error) {
	ctx := sdk.UnwrapSDKContext(goCtx)

	authorityMetadata, err := server.GetAuthorityMetadata(ctx, msg.Denom)
	if err != nil {
		return nil, err
	}

	if msg.Sender != authorityMetadata.GetAdmin() {
		return nil, types.ErrUnauthorized
	}

	// Validate new admin we change to should be different from current admin
	if msg.NewAdmin == authorityMetadata.GetAdmin() {
		return nil, types.ErrAdminAlreadyExists
	}

	err = server.setAdmin(ctx, msg.Denom, msg.NewAdmin)
	if err != nil {
		return nil, err
	}
	ctx.EventManager().EmitEvents(sdk.Events{
		sdk.NewEvent(
			types.TypeMsgChangeAdmin,
			sdk.NewAttribute(types.AttributeDenom, msg.GetDenom()),
			sdk.NewAttribute(types.AttributeNewAdmin, msg.NewAdmin),
		),
	})

	return &types.MsgChangeAdminResponse{}, nil
}
```

**File:** x/tokenfactory/keeper/admins.go (L40-48)
```go
func (k Keeper) setAdmin(ctx sdk.Context, denom string, admin string) error {
	metadata, err := k.GetAuthorityMetadata(ctx, denom)
	if err != nil {
		return err
	}

	metadata.Admin = admin

	return k.setAuthorityMetadata(ctx, denom, metadata)
```

**File:** x/tokenfactory/README.md (L14-18)
```markdown
- Change the admin. In the future, more admin capabilities may be added. Admins
  can choose to share admin privileges with other accounts using the authz
  module. The `ChangeAdmin` functionality, allows changing the master admin
  account, or even setting it to `""`, meaning no account has admin privileges
  of the asset.
```
