### Title
Single-step `MsgChangeAdmin` for tokenfactory denoms permanently and irrecoverably locks mint/burn/metadata authority - (File: x/tokenfactory/types/msgs.go)

### Summary
The tokenfactory module's `MsgChangeAdmin` message reassigns the sole administrative authority over a `factory/{creator}/{subdenom}` denom (mint, burn, `ChangeAdmin`, `SetDenomMetadata` rights) in a single, irreversible step, with no two-step "propose/claim" confirmation and no verification that the new admin address is a live, controlled account.

### Finding Description
`MsgChangeAdmin.ValidateBasic()` only checks that `NewAdmin` parses as a valid bech32 address and that `Denom` is well-formed; it does not — and cannot — verify that the submitting party controls the private key for `NewAdmin`. [1](#0-0) 

The keeper's `ChangeAdmin` handler applies the transfer immediately and unconditionally once the current admin authorizes it — there is no `pendingAdmin`/claim step: [2](#0-1) 

Tests explicitly confirm that after `ChangeAdmin`, the *old* admin immediately and permanently loses all authority (mint/burn fail), and that setting the admin to an empty string is treated identically to any other address change, i.e., the module has no notion of "pending" or reversible ownership changes: [3](#0-2) 

This mirrors exactly the bug class in the report: ownership/admin transfer is a one-step operation without any `pendingOwner`/claim mechanism, so if the current admin (an unprivileged tokenfactory denom creator, reachable directly via `MsgChangeAdmin`) mistypes or otherwise supplies an address whose key is not held by anyone they control, the mistake is unrecoverable — `ValidateBasic` cannot distinguish a "wrong but well-formed" address from a correct one.

### Impact Explanation
Once `ChangeAdmin` executes with an uncontrolled address, all further administrative capability over that denom — minting new supply, burning from the admin's account, updating denom metadata, and any future re-assignment of admin — is permanently and irrecoverably lost, with no redeployment or recovery path available (tokenfactory denoms cannot be "redeployed" the way a contract can; the denom's identity and existing supply persist in bank state under an admin that can never act again). This is a permanent, protocol-level freezing of that denom's administrative/mint-burn control, directly analogous to the `onlyOwner()` functions becoming permanently unusable in the original report.

### Likelihood Explanation
Any account is directly able to call `MsgCreateDenom` (becoming the default admin) and then `MsgChangeAdmin` — this requires no special privilege, precompile access, or governance action, only a standard signed transaction from an unprivileged tokenfactory denom creator. Because `ValidateBasic` and the keeper accept any syntactically valid bech32 address with no confirmation step, a single typo or copy-paste error by the admin is sufficient to trigger the permanent loss.

### Recommendation
Introduce a two-step admin transfer for tokenfactory denoms: `MsgChangeAdmin` (or a new message) should set a `pendingAdmin` field in `AuthorityMetadata` rather than overwriting `Admin` directly, and require a follow-up transaction signed by `pendingAdmin` (e.g., `MsgAcceptAdmin`/`MsgClaimAdmin`) to finalize the transfer. Retain the existing single-step semantics only for explicitly renouncing to an empty admin, since that is an intentional, irrevocable action by design.

### Proof of Concept
1. Account `A` calls `MsgCreateDenom` to create `factory/A/mycoin`, becoming its admin.
2. `A` calls `MsgChangeAdmin{Sender: A, Denom: "factory/A/mycoin", NewAdmin: <typo'd or uncontrolled bech32 address>}`.
3. `ValidateBasic` passes (address is syntactically valid) and the keeper's `ChangeAdmin` handler immediately updates `AuthorityMetadata.Admin` to the new address, exactly as exercised in `x/tokenfactory/keeper/admins_test.go` (`TestAdminMsgs`), where the prior admin is shown to permanently lose all mint/burn authority the instant `ChangeAdmin` succeeds.
4. Since nobody controls the private key for the new admin address, `factory/A/mycoin` can never again be minted, burned by the admin, have its metadata updated, or have its admin reassigned — a permanent, irrecoverable loss of administrative control over that denom.

### Citations

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

**File:** x/tokenfactory/keeper/msg_server.go (L156-176)
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
```

**File:** x/tokenfactory/keeper/admins_test.go (L38-67)
```go
	// Test Change Admin
	_, err = suite.msgServer.ChangeAdmin(sdk.WrapSDKContext(suite.Ctx), types.NewMsgChangeAdmin(suite.TestAccs[0].String(), suite.defaultDenom, suite.TestAccs[1].String()))
	suite.Require().NoError(err)
	queryRes, err = suite.queryClient.DenomAuthorityMetadata(suite.Ctx.Context(), &types.QueryDenomAuthorityMetadataRequest{
		Denom: suite.defaultDenom,
	})
	suite.Require().NoError(err)
	suite.Require().Equal(suite.TestAccs[1].String(), queryRes.AuthorityMetadata.Admin)

	// Make sure old admin can no longer do actions
	_, err = suite.msgServer.Burn(sdk.WrapSDKContext(suite.Ctx), types.NewMsgBurn(suite.TestAccs[0].String(), sdk.NewInt64Coin(suite.defaultDenom, 5)))

	suite.Require().Error(err)

	// Make sure the new admin works
	_, err = suite.msgServer.Mint(sdk.WrapSDKContext(suite.Ctx), types.NewMsgMint(suite.TestAccs[1].String(), sdk.NewInt64Coin(suite.defaultDenom, 5)))

	addr1bal += 5
	suite.Require().NoError(err)
	suite.Require().True(suite.App.BankKeeper.GetBalance(suite.Ctx, suite.TestAccs[1], suite.defaultDenom).Amount.Int64() == addr1bal)

	// Try setting admin to empty
	_, err = suite.msgServer.ChangeAdmin(sdk.WrapSDKContext(suite.Ctx), types.NewMsgChangeAdmin(suite.TestAccs[1].String(), suite.defaultDenom, ""))

	suite.Require().NoError(err)
	queryRes, err = suite.queryClient.DenomAuthorityMetadata(suite.Ctx.Context(), &types.QueryDenomAuthorityMetadataRequest{
		Denom: suite.defaultDenom,
	})
	suite.Require().NoError(err)
	suite.Require().Equal("", queryRes.AuthorityMetadata.Admin)
```
