Confirmed: `ChangeAdmin` in `x/tokenfactory/keeper/msg_server.go` and `setAdmin`/`setAuthorityMetadata` in `x/tokenfactory/keeper/admins.go` perform a direct, single-step overwrite of the `admin` field with only a bech32-format check in `ValidateBasic` (`x/tokenfactory/types/msgs.go`), with no ability to recover if the wrong (but validly-formatted / unowned) address is supplied.

### Title
Tokenfactory `MsgChangeAdmin` performs a single-step, unrecoverable admin transfer with no destination validation - (File: x/tokenfactory/keeper/msg_server.go)

### Summary
The tokenfactory module's `ChangeAdmin` message handler overwrites a denom's `AuthorityMetadata.Admin` field in a single atomic step, based solely on the current admin's signature, with no acceptance/claim step by the new admin.

### Finding Description
`MsgChangeAdmin` is processed by `msgServer.ChangeAdmin`, which checks that `msg.Sender` equals the current admin and that `msg.NewAdmin` differs from the current admin, then immediately calls `server.setAdmin(ctx, msg.Denom, msg.NewAdmin)`, which writes the new admin value directly into state via `setAuthorityMetadata`. [1](#0-0) [2](#0-1) 

`ValidateBasic` for `MsgChangeAdmin` only checks that `NewAdmin` parses as a valid bech32 address — it performs no reachability/ownership check, and there is no concept of a "pending admin" that must claim the role in a second transaction. [3](#0-2) 

This is architecturally identical to the reported bug class: a privileged role (`admin`, analogous to `uberOwner`) can be reassigned to an unintended or inaccessible address in one irreversible transaction, since only the *current* admin's signature is required and the *new* admin never needs to prove control of the key. [4](#0-3) 

This module is directly reachable by any tokenfactory denom creator/admin — an unprivileged, permissionless transaction sender — since denom creation and admin management require no special chain permissions. [5](#0-4) 

### Impact Explanation
If an admin submits `MsgChangeAdmin` with a typo'd address, an address they do not control the key for, or a contract address without `ChangeAdmin` support, the denom's admin capabilities (`Mint`, `Burn`, further `ChangeAdmin`, `SetDenomMetadata`, `UpdateDenom`) become **permanently inaccessible** for that denom. This is a permanent freezing of mint/burn/administration authority over that specific token supply, matching the "permanent freezing" impact category, since there is no path to recover, reset, or reclaim admin rights once misdirected (the same class of unrecoverable lockup as the reported `uberOwner` issue, just scoped to a tokenfactory denom rather than the whole protocol).

### Likelihood Explanation
Low probability (requires an admin's own error entering an address) but is a routine, reachable operation performed by ordinary users via a documented CLI command (`seid tx tokenfactory change-admin`), and is entirely one signed transaction away from an irreversible outcome — no governance or validator collusion needed. [6](#0-5) 

### Recommendation
Convert `ChangeAdmin` into a two-step process: add a `pending_admin` field to `DenomAuthorityMetadata`, have `MsgChangeAdmin` (or a renamed propose-style message) set `pending_admin` instead of `admin` directly, and add a new `MsgAcceptAdmin` (or similar) message that only the `pending_admin` signer can call to finalize the transfer. Allow the current admin to overwrite/cancel a pending proposal if it was set incorrectly.

### Proof of Concept
1. Create a denom: `seid tx tokenfactory create-denom mydenom --from admin`.
2. Change admin to an address whose private key is not held by anyone (e.g., a random valid bech32 string or a burn address): `seid tx tokenfactory change-admin factory/<admin>/mydenom <unknown-address> --from admin`.
3. Confirm via `seid q tokenfactory denom-authority-metadata factory/<admin>/mydenom` that the admin is now `<unknown-address>`.
4. Attempt any admin action (`mint`, `burn`, `change-admin`) from the original admin — all fail with `types.ErrUnauthorized`, and no account can ever mint/burn/administer that denom again, exactly as demonstrated in `x/tokenfactory/keeper/admins_test.go` (`TestAdminMsgs`), where the state transition is shown to be immediate and irreversible. [7](#0-6)

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

**File:** proto/tokenfactory/tx.proto (L69-79)
```text
// MsgChangeAdmin is the sdk.Msg type for allowing an admin account to reassign
// adminship of a denom to a new account
message MsgChangeAdmin {
  string sender = 1 [(gogoproto.moretags) = "yaml:\"sender\""];
  string denom = 2 [(gogoproto.moretags) = "yaml:\"denom\""];
  string new_admin = 3 [(gogoproto.moretags) = "yaml:\"new_admin\""];
}

// MsgChangeAdminResponse defines the response structure for an executed
// MsgChangeAdmin message.
message MsgChangeAdminResponse {}
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

**File:** x/tokenfactory/keeper/admins_test.go (L38-50)
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
```
