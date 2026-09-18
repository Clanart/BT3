### Title
Single-step tokenfactory `ChangeAdmin` allows irreversible loss of denom admin authority - (File: x/tokenfactory/keeper/msg_server.go)

### Summary
The tokenfactory module's `MsgChangeAdmin` handler transfers denom admin rights to a new address in a single, unconfirmed step, with no acceptance step from the new admin and no protection against setting a mistyped or uncontrolled address.

### Finding Description
`ChangeAdmin` in `msgServer` verifies the sender is the current admin and that the new admin differs from the current one, then immediately overwrites the `AuthorityMetadata.Admin` field via `setAdmin`, with no two-step handshake: [1](#0-0) 

`setAdmin` unconditionally persists whatever address string was supplied as the new admin, without any confirmation from that address: [2](#0-1) 

`MsgChangeAdmin.ValidateBasic` only checks that `NewAdmin` is a syntactically valid bech32 address — it does not, and cannot, verify that the address is actually controlled by anyone or is the intended recipient: [3](#0-2) 

This is exactly the bug class described in the external report: any admin/owner-controlled role transfer done in one atomic step is dangerous because a typo'd, malformed-but-valid, or otherwise wrong address permanently receives (or effectively burns) the privileged role, with no opportunity for the intended new admin to reject or for the old admin to recover. Unlike `NodeRegistry`, which uses a propose/accept two-step ownership transfer, `tokenfactory`'s `ChangeAdmin` has no analogous safety mechanism. Notably, the module explicitly allows the admin to be set to the empty string `""`, deliberately renouncing admin control — this same "no way back" pattern also applies to accidental transfers to any address that is not actually controlled by the intended party, since Sei addresses are also derivable/typo-adjacent bech32 strings and this method can also be invoked via CosmWasm contracts (`EncodeTokenFactoryChangeAdmin`), widening the population of callers who could make this irreversible mistake. [4](#0-3) 

### Impact Explanation
If the admin of a `factory/{creator}/{subdenom}` denom submits `MsgChangeAdmin` with a wrong (but validly formatted) new-admin address, all admin privileges over that denom — minting, burning, further changing the admin, and updating denom metadata — are permanently and unrecoverably lost, since there is no admin key held by the old address, and no fallback authority (unless the new address happens to be one the old admin also controls, which is exactly the accidental-transfer scenario at issue). This is a permanent freeze of denom governance functionality for that token, matching the "permanent freezing" impact class.

### Likelihood Explanation
Low, as with the original finding — it requires operator/admin error in specifying the new admin address (e.g., copy-paste mistake, wrong environment/account, confusion between similar bech32 addresses). No malicious actor is required; the token creator/admin themselves triggers it.

### Recommendation
Adopt the same two-step propose/accept pattern already used elsewhere in the codebase (e.g., `NodeRegistry`'s ownership transfer) for `ChangeAdmin`: introduce a "pending admin" field set by `MsgChangeAdmin`, and require a new `MsgAcceptAdmin` (or similar) signed by the pending admin to finalize the transfer, only then overwriting `AuthorityMetadata.Admin`. This ensures the new admin controls the destination key before the migration completes, and lets the current admin cancel/retry if the wrong address was set.

### Proof of Concept
1. Denom creator/admin `A` creates a tokenfactory denom via `MsgCreateDenom`, becoming its admin (`GetAuthorityMetadata(denom).Admin == A`).
2. `A` submits `MsgChangeAdmin{Sender: A, Denom: denom, NewAdmin: B}` where `B` is a validly-formatted bech32 address that `A` mistakenly typed (not actually controlled by the intended recipient, e.g., a stray digit changed vs. the intended `B'`).
3. `ChangeAdmin` in `msg_server.go` passes the `sender == admin` and `newAdmin != admin` checks and calls `setAdmin(ctx, denom, B)`, immediately overwriting `AuthorityMetadata.Admin = B`.
4. From this point, `A` can no longer `Mint`, `Burn`, `ChangeAdmin`, or `SetDenomMetadata` on `denom` (all now require `msg.Sender == B`), and since `B` is not actually controlled by any party with knowledge of/intent for this denom, admin control of `denom` is permanently lost/frozen.

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

**File:** x/tokenfactory/client/wasm/encoder.go (L47-58)
```go
func EncodeTokenFactoryChangeAdmin(rawMsg json.RawMessage, sender sdk.AccAddress) ([]sdk.Msg, error) {
	encodedChangeAdminMsg := bindings.ChangeAdmin{}
	if err := json.Unmarshal(rawMsg, &encodedChangeAdminMsg); err != nil {
		return []sdk.Msg{}, types.ErrEncodeTokenFactoryChangeAdmin
	}
	changeAdminMsg := types.MsgChangeAdmin{
		Sender:   sender.String(),
		Denom:    encodedChangeAdminMsg.Denom,
		NewAdmin: encodedChangeAdminMsg.NewAdminAddress,
	}
	return []sdk.Msg{&changeAdminMsg}, nil
}
```
