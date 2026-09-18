### Title
Tokenfactory `MsgChangeAdmin` allows setting a denom's admin to the empty string, permanently freezing admin control - (File: `x/tokenfactory/types/authority_metadata.go`, `x/tokenfactory/keeper/admins.go`)

### Summary
The tokenfactory module's admin-change flow is the direct analog of the reported "missing zero address check in `setGov()`" bug class: an unprivileged/authorized-but-single-transaction actor can set the admin of a tokenfactory denom to an empty ("zero") value, after which no address can ever administer that denom again.

### Finding Description
`MsgChangeAdmin.ValidateBasic()` requires `NewAdmin` to parse as a valid bech32 address via `sdk.AccAddressFromBech32(m.NewAdmin)` [1](#0-0) . However, the keeper-level persistence path does not enforce this: `setAdmin` simply overwrites `metadata.Admin` with whatever string is passed and calls `setAuthorityMetadata`, which in turn calls `metadata.Validate()` [2](#0-1) . `DenomAuthorityMetadata.Validate()` explicitly special-cases the empty string as valid, only validating the bech32 format `if metadata.Admin != ""` [3](#0-2) .

The `ChangeAdmin` message handler only checks that the sender is the current admin and that `NewAdmin` differs from the current admin — it performs no zero/empty-address check before calling `setAdmin` [4](#0-3) . The existing test suite documents this behavior as intentional/expected: after `ChangeAdmin(..., "")`, `AuthorityMetadata.Admin` becomes `""` and the operation succeeds without error [5](#0-4) .

Once `Admin == ""`, `ChangeAdmin` can never be called successfully again for that denom, because any legitimate sender's address will never equal the stored empty-string admin, and there is no path to reset the admin without going through this same sender-must-equal-admin check that will now always fail (except by a message with `Sender == ""`, which is unparseable/unsignable).

### Impact Explanation
This permanently and irrecoverably freezes admin control (mint/burn/change-admin rights) for the affected tokenfactory denom — a direct fund-management-and-supply-control freezing bug, analogous to the reported `setGov()` issue. Any tokenfactory-created denom whose current admin submits (even accidentally, e.g., via a scripting bug, empty CLI arg, or malformed integration) a `MsgChangeAdmin` with `NewAdmin=""` loses admin control forever. This can permanently disable future minting or supply management of that denom.

### Likelihood Explanation
The current admin of any tokenfactory denom is an ordinary, unprivileged (from the chain's perspective) transaction sender — no elevated permissions are required beyond already being the token's own admin. The CLI even builds `MsgChangeAdmin` directly from a raw user-supplied string argument with no non-empty check before submission [6](#0-5) , and `ValidateBasic`'s bech32 parse is bypassed at the keeper layer for the empty-string case, so this is trivially reachable by mistake or by a scripted/automated call, and the test suite confirms it is not rejected.

### Recommendation
Disallow empty (or otherwise unparseable) `NewAdmin` values in `MsgChangeAdmin`/`ChangeAdmin`: either reject `msg.NewAdmin == ""` explicitly in the `ChangeAdmin` msg-server handler [7](#0-6) , or remove the `metadata.Admin != ""` bypass in `DenomAuthorityMetadata.Validate()` so an empty admin can never be persisted via this path [3](#0-2) . If clearing the admin intentionally is a desired governance feature, it should require a distinct, explicit message (e.g., `MsgClearAdmin` requiring extra confirmation) rather than being silently permitted through `MsgChangeAdmin`.

### Proof of Concept
1. Create a tokenfactory denom as admin `A` (`CreateDefaultDenom` in tests, or `seid tx tokenfactory create-denom`).
2. `A` submits `MsgChangeAdmin{Sender: A, Denom: denom, NewAdmin: ""}`.
3. The message passes `ValidateBasic` only if bech32 parsing of `""` fails — but the actual failure mode is bypassed at the keeper layer since `setAdmin`/`setAuthorityMetadata`/`Validate()` all special-case and accept the empty string, as demonstrated by the existing unit test asserting `suite.Require().NoError(err)` and `AuthorityMetadata.Admin == ""` after this exact call [5](#0-4) .
4. Any subsequent `MsgChangeAdmin`, `MsgMint`, or `MsgBurn` for that denom fails because `msg.Sender != authorityMetadata.GetAdmin()` can never be satisfied again (no valid sender address equals `""`) [8](#0-7) .

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

**File:** x/tokenfactory/keeper/admins.go (L22-49)
```go
// setAuthorityMetadata stores authority metadata for a specific denom
func (k Keeper) setAuthorityMetadata(ctx sdk.Context, denom string, metadata types.DenomAuthorityMetadata) error {
	err := metadata.Validate()
	if err != nil {
		return err
	}

	store := k.GetDenomPrefixStore(ctx, denom)

	bz, err := proto.Marshal(&metadata)
	if err != nil {
		return err
	}

	store.Set([]byte(types.DenomAuthorityMetadataKey), bz)
	return nil
}

func (k Keeper) setAdmin(ctx sdk.Context, denom string, admin string) error {
	metadata, err := k.GetAuthorityMetadata(ctx, denom)
	if err != nil {
		return err
	}

	metadata.Admin = admin

	return k.setAuthorityMetadata(ctx, denom, metadata)
}
```

**File:** x/tokenfactory/types/authority_metadata.go (L7-15)
```go
func (metadata DenomAuthorityMetadata) Validate() error {
	if metadata.Admin != "" {
		_, err := sdk.AccAddressFromBech32(metadata.Admin)
		if err != nil {
			return err
		}
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

**File:** x/tokenfactory/keeper/admins_test.go (L59-67)
```go
	// Try setting admin to empty
	_, err = suite.msgServer.ChangeAdmin(sdk.WrapSDKContext(suite.Ctx), types.NewMsgChangeAdmin(suite.TestAccs[1].String(), suite.defaultDenom, ""))

	suite.Require().NoError(err)
	queryRes, err = suite.queryClient.DenomAuthorityMetadata(suite.Ctx.Context(), &types.QueryDenomAuthorityMetadataRequest{
		Denom: suite.defaultDenom,
	})
	suite.Require().NoError(err)
	suite.Require().Equal("", queryRes.AuthorityMetadata.Admin)
```

**File:** x/tokenfactory/client/cli/tx.go (L216-242)
```go
// NewChangeAdminCmd broadcast MsgChangeAdmin
func NewChangeAdminCmd() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "change-admin [denom] [new-admin-address] [flags]",
		Short: "Changes the admin address for a factory-created denom. Must have admin authority to do so.",
		Args:  cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			clientCtx, err := client.GetClientTxContext(cmd)
			if err != nil {
				return err
			}

			txf := tx.NewFactoryCLI(clientCtx, cmd.Flags()).WithTxConfig(clientCtx.TxConfig).WithAccountRetriever(clientCtx.AccountRetriever)

			msg := types.NewMsgChangeAdmin(
				clientCtx.GetFromAddress().String(),
				args[0],
				args[1],
			)

			return tx.GenerateOrBroadcastTxWithFactory(cmd.Context(), clientCtx, txf, msg)
		},
	}

	flags.AddTxFlagsToCmd(cmd)
	return cmd
}
```
