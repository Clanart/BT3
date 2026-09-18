### Title
`MsgChangeAdmin` performs a single-step, irreversible admin transfer for tokenfactory denoms - (File: `x/tokenfactory/keeper/msg_server.go`)

### Summary
The tokenfactory module's `ChangeAdmin` message handler reassigns denom admin authority to `msg.NewAdmin` in a single atomic step with no acceptance/confirmation step by the new admin, mirroring the single-step `transferOwnership` pattern flagged in the source report.

### Finding Description
`msgServer.ChangeAdmin` validates that the sender is the current admin and that the new admin differs from the current one, then immediately calls `server.setAdmin(ctx, msg.Denom, msg.NewAdmin)` to overwrite the `AuthorityMetadata.Admin` field for the denom in a single transaction, with no intermediate "pending admin" or explicit acceptance step by the new admin account. [1](#0-0) 

`MsgChangeAdmin.ValidateBasic` only checks that `NewAdmin` parses as a syntactically valid bech32 address; it does not (and cannot) verify that the address is one the sender actually controls or intends. [2](#0-1) 

Because the transfer is a single step, any typo, copy/paste error, or use of an unowned/incorrect (but validly formatted) address in `new_admin` immediately and permanently transfers all admin authority (mint, burn, `SetDenomMetadata`, `UpdateDenom`/allow-list management) for that denom away from the sender, with no ability to recover unless the erroneous recipient cooperates. This is directly analogous to the referenced `OwnableImpl.transferOwnership` issue, which recommends a 2-step (propose/accept) pattern precisely to prevent this class of mistake.

### Impact Explanation
A tokenfactory denom admin who mistypes or misuses `new_admin` in `MsgChangeAdmin` permanently and irrevocably loses admin authority over their denom (minting, burning, denom metadata, allow-list updates) to an unintended address. Since denom admin authority in tokenfactory is a privileged, chain-tracked capability with no built-in recovery path (the admin field is directly overwritten), this constitutes permanent freezing of the denom's administrative functionality reachable by any tokenfactory denom admin via a normal signed transaction — matching the "permanent freezing" impact criterion.

### Likelihood Explanation
This requires only a single, valid, self-authorized transaction from the current admin (no attacker, no special privileges beyond being the existing denom admin), and address transposition/typo mistakes are a common real-world occurrence, especially since Sei addresses are bech32-encoded strings without embedded checksbriefly-visible-name assistance. The `ChangeAdmin` test suite confirms this succeeds unconditionally for any syntactically valid `new_admin` different from the current admin. [3](#0-2) 

### Recommendation
Introduce a 2-step admin transfer for tokenfactory denoms analogous to OpenZeppelin's `Ownable2Step`: `ChangeAdmin` should set a "pending admin" field on `AuthorityMetadata` rather than overwriting `Admin` directly, and add a new message (e.g. `MsgAcceptAdmin`/`MsgClaimAdmin`) that must be signed by the pending admin to finalize the transfer, only then updating `AuthorityMetadata.Admin`.

### Proof of Concept
1. Admin A creates a denom via `MsgCreateDenom`, becoming its admin (`AuthorityMetadata.Admin = A`).
2. Admin A intends to transfer admin rights to address `B` but mistypes it as address `C` (a syntactically valid bech32 address A does not control) in `MsgChangeAdmin{Sender: A, Denom: denom, NewAdmin: C}`.
3. `msgServer.ChangeAdmin` verifies `A == AuthorityMetadata.Admin`, sees `C != A`, and calls `setAdmin(ctx, denom, C)` immediately — see `x/tokenfactory/keeper/msg_server.go` lines 156-176.
4. `AuthorityMetadata.Admin` is now permanently `C`; A can no longer mint/burn/update metadata for `denom` (as demonstrated by the "old admin can no longer do actions" assertion in `admins_test.go` lines 47-50), and recovery is impossible without cooperation from whoever (if anyone) controls `C`.

### Citations

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

**File:** x/tokenfactory/keeper/admins_test.go (L70-118)
```go
func (suite *KeeperTestSuite) TestChangeAdminDenom() {
	for _, tc := range []struct {
		desc                    string
		msgChangeAdmin          func(denom string) *types.MsgChangeAdmin
		expectedChangeAdminPass bool
		expectedAdminIndex      int
		msgMint                 func(denom string) *types.MsgMint
		expectedMintPass        bool
	}{
		{
			desc: "creator admin can't mint after setting to '' ",
			msgChangeAdmin: func(denom string) *types.MsgChangeAdmin {
				return types.NewMsgChangeAdmin(suite.TestAccs[0].String(), denom, "")
			},
			expectedChangeAdminPass: true,
			expectedAdminIndex:      -1,
			msgMint: func(denom string) *types.MsgMint {
				return types.NewMsgMint(suite.TestAccs[0].String(), sdk.NewInt64Coin(denom, 5))
			},
			expectedMintPass: false,
		},
		{
			desc: "non-admins can't change the existing admin",
			msgChangeAdmin: func(denom string) *types.MsgChangeAdmin {
				return types.NewMsgChangeAdmin(suite.TestAccs[1].String(), denom, suite.TestAccs[2].String())
			},
			expectedChangeAdminPass: false,
			expectedAdminIndex:      0,
		},
		{
			desc: "change to same admin should fail",
			msgChangeAdmin: func(denom string) *types.MsgChangeAdmin {
				return types.NewMsgChangeAdmin(suite.TestAccs[0].String(), denom, suite.TestAccs[0].String())
			},
			expectedChangeAdminPass: false,
			expectedAdminIndex:      0,
		},
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
