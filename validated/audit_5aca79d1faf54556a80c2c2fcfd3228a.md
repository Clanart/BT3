### Title
No two-step confirmation for tokenfactory `ChangeAdmin`, allowing accidental permanent loss of denom admin authority - ([File: x/tokenfactory/keeper/msg_server.go])

### Summary
The `sei-chain` tokenfactory module exposes a single-step `MsgChangeAdmin` transaction that lets the current admin of a `factory/{creator}/{subdenom}` denom irrevocably hand off admin authority to a new address in one transaction, with no acceptance step by the nominated address. This mirrors the reported `Swap.sol` `transferOwnership()` pattern: a single call performed by the current privileged party writes a new address into the authority slot with no verification that the new address is a valid, reachable/controlled account.

### Finding Description
`ChangeAdmin` in `x/tokenfactory/keeper/msg_server.go` checks that the sender is the current admin and that the new admin differs from the current one, then immediately commits the change via `setAdmin`: [1](#0-0) 

`MsgChangeAdmin.ValidateBasic()` only verifies that `NewAdmin` decodes as a syntactically valid bech32 `AccAddress` — it does not verify the address corresponds to an account that can ever sign a future transaction (e.g., it could be a randomly generated address with no known private key, a module account, or any other unreachable/uncontrolled address): [2](#0-1) 

As documented, admin status controls minting, burning, transferring, and changing admin again for the denom, and there is no owner-nominates/new-admin-accepts handshake — the change is final the moment the transaction executes: [3](#0-2) 

This is functionally identical to the reported bug class: a single privileged-party action (`transferOwnership`/`ChangeAdmin`) permanently reassigns authority to an address that is only checked for basic format validity, not for actual controllability, and there is no two-phase nominate/accept pattern to catch mistakes such as typos, wrong network/prefix confusion, or copy-paste errors.

### Impact Explanation
If an admin mistakenly sets `NewAdmin` to an address it does not control (or an address with no known key, e.g. a randomly-generated placeholder or a mistyped bech32 string that still checksums correctly), admin authority over the tokenfactory denom is permanently and irrecoverably lost. Since the tokenfactory README states admin control governs `Mint`, `Burn`, `ChangeAdmin`, and denom transfer capability, this results in permanent freezing of all future administrative actions over that denom (no further minting/burning/admin changes are possible), which is a legitimate "permanent freezing" outcome analogous to the reported vulnerability's "breaking all onlyOwner functions."

### Likelihood Explanation
Likelihood is low-to-moderate: this requires the current denom admin to submit the `MsgChangeAdmin` transaction themselves with an incorrect address — no external attacker action is needed, matching the "accidental" framing of the original report. Given that tokenfactory denom creation and admin management is a permissionless, commonly used feature (any account can create a denom via `MsgCreateDenom`), operational error during admin rotation (e.g., moving admin control to a multisig, DAO contract, or new hot wallet) is a realistic scenario, and there is no safety net once the mistake is submitted on-chain.

### Recommendation
Add a two-step admin transfer flow for tokenfactory denoms: introduce a "pending admin" field set by `ChangeAdmin` (or a new `MsgProposeAdmin`), and require the nominated address to submit a corresponding `MsgAcceptAdmin`/`MsgClaimAdmin` transaction (signed by the nominee) before the authority record is actually updated. This ensures the new admin address is active/controlled before the current admin's authority is revoked, consistent with the recommended two-step ownership pattern for `Swap.sol`.

### Proof of Concept
1. An account creates a tokenfactory denom via `MsgCreateDenom`, becoming its admin (`x/tokenfactory/keeper/msg_server.go`, `CreateDenom` flow referenced in `x/tokenfactory/README.md`).
2. The admin submits `MsgChangeAdmin{Sender: admin, Denom: denom, NewAdmin: "sei1..."}` where `sei1...` is a syntactically valid bech32 address that the admin does not actually control (e.g., a randomly generated address, or one generated with a typo that still passes checksum validation).
3. `ChangeAdmin` in `x/tokenfactory/keeper/msg_server.go` (lines 156-176) validates only that the sender matches the current admin and that `NewAdmin != currentAdmin`, then calls `setAdmin` unconditionally.
4. From this point, no account can ever again call `Mint`, `Burn`, or `ChangeAdmin` for that denom, since the on-chain admin field now points to an address with no accessible private key — permanently freezing all administrative functionality for the denom, as demonstrated by the admin-authority checks in `x/tokenfactory/keeper/admins_test.go` (lines 91-98, showing only the recorded admin can invoke `ChangeAdmin`). [4](#0-3)

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

**File:** x/tokenfactory/README.md (L14-18)
```markdown
- Change the admin. In the future, more admin capabilities may be added. Admins
  can choose to share admin privileges with other accounts using the authz
  module. The `ChangeAdmin` functionality, allows changing the master admin
  account, or even setting it to `""`, meaning no account has admin privileges
  of the asset.
```

**File:** x/tokenfactory/keeper/admins_test.go (L91-98)
```go
		{
			desc: "non-admins can't change the existing admin",
			msgChangeAdmin: func(denom string) *types.MsgChangeAdmin {
				return types.NewMsgChangeAdmin(suite.TestAccs[1].String(), denom, suite.TestAccs[2].String())
			},
			expectedChangeAdminPass: false,
			expectedAdminIndex:      0,
		},
```
