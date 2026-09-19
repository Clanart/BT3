### Title
Single-step `ChangeAdmin` in tokenfactory permanently locks out denom administration if new admin address is mistyped or uncontrolled - (File: x/tokenfactory/keeper/msg_server.go)

### Summary
The tokenfactory module's `MsgChangeAdmin` handler reassigns denom admin rights in a single step, with no acceptance step by the new admin and no verification that the new admin address is a live/controlled account. Any tokenfactory denom creator (an unprivileged, permissionless role reachable by any account via `MsgCreateDenom`) can permanently and unrecoverably lose administrative control over a denom by mistyping the new admin's bech32 address or supplying an address nobody controls.

### Finding Description
`ChangeAdmin` immediately overwrites the denom's `AuthorityMetadata.Admin` field after only checking that the caller is the current admin and that the new admin differs from the current one: [1](#0-0) 

The actual mutation is a direct, one-shot state write with no two-step confirmation: [2](#0-1) 

The only validation performed on the new admin address is a format check (`sdk.AccAddressFromBech32`) in `ValidateBasic`, which does not verify that the address corresponds to a controllable/reachable account: [3](#0-2) 

This is the same bug class as the reported "No Transfer Ownership Pattern": a privileged role (here, denom admin) is transferred via a single call that writes directly into state, with no nominate/accept handshake and no check that the destination account is valid/controlled. Because `CreateDenom` is fully permissionless (any account can create a `factory/{creator}/{subdenom}` denom and becomes its default admin), this "admin" role is reachable by any ordinary transaction sender, matching the analog's reachability requirement.

### Impact Explanation
If the current admin submits `MsgChangeAdmin` with a typo'd bech32 address, an address with no known private key, or otherwise unreachable/uncontrolled address, the denom's administrative capabilities (`Mint`, `Burn`, further `ChangeAdmin`, `SetDenomMetadata`) become permanently and irrecoverably locked — no path exists in the module to reclaim or reset the admin once set to an uncontrolled address, since only the current (now-uncontrolled) admin can call `ChangeAdmin` again. This is a permanent freezing of denom-management authority (future minting/burning of that tokenfactory-issued asset), consistent with the accepted impact criteria of permanent freezing.

### Likelihood Explanation
Likelihood is driven purely by user error (typo, copy-paste mistake, or wrong network's address format) rather than malicious activity, matching the exact scenario the external report highlights. Given that tokenfactory denom administration is a common day-to-day operation (`change-admin` CLI/tx is standard tooling), the probability of an accidental fat-fingered address entry is non-trivial over the lifetime of many denoms.

### Recommendation
Implement a two-step admin transfer for tokenfactory denoms: add a `ProposeNewAdmin`/`AcceptAdmin` (or equivalent nominate + accept) message pair so that the new admin address must actively claim adminship before the transfer is finalized, mirroring the `Ownable2Step`/`acceptOwnership` pattern referenced in the source report. As an additional safeguard, reject zero-length/invalid or self-referential admin values, and consider requiring the target address to have signed a prior transaction (i.e., have an existing account context) before accepting adminship.

### Proof of Concept
1. Account A creates a tokenfactory denom via `MsgCreateDenom`, becoming its admin (`x/tokenfactory/keeper/msg_server.go`, `CreateDenom`).
2. Account A calls `MsgChangeAdmin{Sender: A, Denom: denom, NewAdmin: "sei1<mistyped-address>"}`. `ValidateBasic` only checks bech32 format, which the mistyped address can still satisfy.
3. `ChangeAdmin` handler verifies `msg.Sender == authorityMetadata.GetAdmin()` and that `NewAdmin != currentAdmin`, then calls `setAdmin`, immediately committing the new (uncontrolled) admin address to state.
4. From this point, no account can call `Mint`, `Burn`, `ChangeAdmin`, or `SetDenomMetadata` for this denom, since `authorityMetadata.GetAdmin()` never again matches any signer the original owner controls — the denom's administrative capability is permanently frozen.

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
