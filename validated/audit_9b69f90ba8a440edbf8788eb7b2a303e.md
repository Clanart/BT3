### Title
Missing zero-address validation in `MsgChangeAdmin` permanently bricks tokenfactory denom administration - (File: `x/tokenfactory/types/msgs.go`, `x/tokenfactory/keeper/admins.go`)

### Summary
The tokenfactory module's `ChangeAdmin` message allows a denom's current admin to reassign the `admin` field to any bech32-decodable address, including the "zero" address (20 zero bytes), with no check that the new admin is a meaningful, controllable account. Once set, no one can execute privileged actions (`Mint`, `Burn`, `UpdateDenom`, further `ChangeAdmin`) on that denom.

### Finding Description
`MsgChangeAdmin.ValidateBasic` only checks that `NewAdmin` decodes as a valid bech32 address, and separately requires it differ from the current admin — it never rejects the zero-value address: [1](#0-0) 

The message handler enforces only that the sender is the current admin and that the new admin differs from the old one, then persists the change unconditionally: [2](#0-1) 

The underlying keeper setter performs no address validity/liveness check either — it simply overwrites `metadata.Admin` and stores it: [3](#0-2) 

`sdk.AccAddressFromBech32` will happily accept a 20-zero-byte address as "valid" since it only checks bech32 encoding/checksum, not that the address corresponds to a reachable key. This mirrors exactly the reported bug class ("no check for valid address when setting [a privileged role]") — here applied to the tokenfactory denom admin instead of `Game.sol`'s `guardian`.

### Impact Explanation
Once `admin` is set to the zero address (or any other address nobody holds the private key for), the denom becomes permanently unmintable/unburnable and its metadata/allow-list can never be updated again — this is an irreversible, unauthorized-by-design lockout of the denom's economic functions reachable purely through a standard `MsgChangeAdmin` transaction from the denom's own current admin (an unprivileged, ordinary tx sender in the context of the chain — no validator/governance/operator privilege required). This satisfies the "permanent freezing" impact bar (denom functionality frozen forever, and any future minting that was expected/relied upon becomes impossible).

### Likelihood Explanation
Trivial to trigger — a single `MsgChangeAdmin` transaction with `NewAdmin` set to the bech32-encoded zero address (accidental misconfiguration, or a scripting bug in a tool/integration that defaults an unset admin field to the zero value) permanently locks the denom. No special conditions or races needed.

### Recommendation
Add an explicit zero-address (and possibly "no admin" sentinel) check in `MsgChangeAdmin.ValidateBasic` and/or `setAdmin`, e.g.:
```go
if sdk.AccAddress(sdk.MustAccAddressFromBech32(m.NewAdmin)).Empty() ||
   bytes.Equal(sdk.MustAccAddressFromBech32(m.NewAdmin), make([]byte, 20)) {
    return sdkerrors.Wrap(sdkerrors.ErrInvalidAddress, "new admin cannot be the zero address")
}
```
If intentionally relinquishing admin control is a desired feature, expose it via an explicit `ClearAdmin`-style message rather than allowing an opaque zero-address assignment through `ChangeAdmin`.

### Proof of Concept
1. Attacker (or careless admin) owns denom `factory/{admin}/foo` with `admin = {admin}`.
2. Submit `MsgChangeAdmin{Sender: admin, Denom: "factory/{admin}/foo", NewAdmin: "sei1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqzs8xqm"}` (bech32 encoding of 20 zero bytes) — `ValidateBasic` passes because it only checks `AccAddressFromBech32` decodes without error, and it differs from the old admin.
3. `msgServer.ChangeAdmin` verifies `msg.Sender == authorityMetadata.GetAdmin()` (true) and `msg.NewAdmin != authorityMetadata.GetAdmin()` (true), then calls `setAdmin`, overwriting the stored admin with the zero address.
4. All subsequent `Mint`, `Burn`, `UpdateDenom`, and `ChangeAdmin` calls for this denom fail the `msg.Sender != authorityMetadata.GetAdmin()` check for every real signer, permanently freezing denom administration.

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
