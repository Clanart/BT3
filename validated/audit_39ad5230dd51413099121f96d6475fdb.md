## Title
Missing two-step admin transfer in tokenfactory `ChangeAdmin` can permanently lock out denom management - (File: `x/tokenfactory/keeper/msg_server.go`)

### Summary
The `tokenfactory` module's `MsgChangeAdmin` handler performs the transfer of a denom's admin authority in a single, atomic step, exactly the anti-pattern flagged in the external report for Folks Finance's `update_admin` methods. Any unprivileged account that has created a tokenfactory denom (and is thus its admin) can call `ChangeAdmin` directly, and if the `new_admin` address is mistyped, unreachable, or otherwise wrong, the denom's admin authority (mint, burn, further `ChangeAdmin`, `SetDenomMetadata`, `UpdateDenom`) is irrevocably lost with no way to recover it.

### Finding Description
`ChangeAdmin` looks up the current `AuthorityMetadata` for the denom, checks the sender is the current admin, and then immediately overwrites the admin field in state — there is no proposal/acceptance step and no validation that the new admin address is reachable or capable of acting: [1](#0-0) 

The state mutation itself is a direct, single write with no staging: [2](#0-1) 

`ValidateBasic` for `MsgChangeAdmin` only checks that `new_admin` parses as a valid bech32 address — it cannot detect a typo that resolves to a different, uncontrolled valid address, nor detect that the address is simply unreachable/lost: [3](#0-2) 

This mirrors the reported bug class precisely: an `update_admin`/`ChangeAdmin`-style call that transfers privileged control in one step, with no `propose_admin`/`accept_admin` pattern requiring the new admin to affirmatively claim the role.

### Impact Explanation
Any tokenfactory denom's `AuthorityMetadata.Admin` field, once set to a wrong or uncontrolled address via `ChangeAdmin`, permanently freezes all admin-only capabilities for that denom: minting, burning, changing the admin again, and updating denom metadata/allow-list. This is a "permanent freezing" of a chain-level resource (the tokenfactory denom's administrative control plane) directly caused by execution of a single, ordinary message from any account — no special privilege required beyond having created the denom in the first place, which is itself permissionless (as documented, "any account" can create a denom).

### Likelihood Explanation
Likelihood is high in the sense that this requires no attacker at all — it is a pure operator/user error scenario identical to the exploit scenario described in the source report (Alice calling `update_admin` with an incorrect address). Because `ChangeAdmin` is a single top-level `Msg` reachable directly by any tokenfactory denom creator/admin via a standard transaction (and also via the CosmWasm `ChangeAdmin` binding used by contracts), a single mistyped address in a normal transaction is sufficient to trigger permanent loss.

### Recommendation
Implement a two-step admin transfer for `x/tokenfactory`: add a `ProposeAdmin`-equivalent message that stores the candidate admin without granting authority, and require the candidate to submit an `AcceptAdmin`-equivalent message (signed by the candidate) before `AuthorityMetadata.Admin` is updated in `x/tokenfactory/keeper/admins.go`'s `setAdmin`. This prevents mistyped or unreachable addresses from permanently bricking a denom's admin capabilities.

### Proof of Concept
1. Account `A` creates denom `factory/A/foo` via `MsgCreateDenom`, becoming its admin.
2. `A` submits `MsgChangeAdmin{Sender: A, Denom: "factory/A/foo", NewAdmin: <typo'd-but-valid-bech32 address B'>}`.
3. `ChangeAdmin` in `x/tokenfactory/keeper/msg_server.go` validates `A == authorityMetadata.Admin`, sets `metadata.Admin = B'`, and commits — see `x/tokenfactory/keeper/admins_test.go` `TestChangeAdminDenom` "success change admin" case demonstrating the unconditional, single-step effect of `ChangeAdmin`: [4](#0-3) 
4. Since `B'` is not controlled by `A` (typo), no further `Mint`, `Burn`, `ChangeAdmin`, `SetDenomMetadata`, or `UpdateDenom` message can ever be authorized for `factory/A/foo` again — the denom's administrative control is permanently frozen.

### Citations

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

**File:** x/tokenfactory/keeper/admins.go (L40-49)
```go
func (k Keeper) setAdmin(ctx sdk.Context, denom string, admin string) error {
	metadata, err := k.GetAuthorityMetadata(ctx, denom)
	if err != nil {
		return err
	}

	metadata.Admin = admin

	return k.setAuthorityMetadata(ctx, denom, metadata)
}
```

**File:** x/tokenfactory/types/msgs.go (L169-188)
```go
func (m MsgChangeAdmin) Route() string { return RouterKey }
func (m MsgChangeAdmin) Type() string  { return TypeMsgChangeAdmin }
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

**File:** x/tokenfactory/keeper/admins_test.go (L107-118)
```go
		{
			desc: "success change admin",
			msgChangeAdmin: func(denom string) *types.MsgChangeAdmin {
				return types.NewMsgChangeAdmin(suite.TestAccs[0].String(), denom, suite.TestAccs[1].String())
			},
			expectedAdminIndex:      1,
			expectedChangeAdminPass: true,
			msgMint: func(denom string) *types.MsgMint {
				return types.NewMsgMint(suite.TestAccs[1].String(), sdk.NewInt64Coin(denom, 5))
			},
			expectedMintPass: true,
		},
```
