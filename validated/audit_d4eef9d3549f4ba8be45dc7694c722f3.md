### Title
Malicious delegated tokenfactory admin can permanently freeze a denom's transfers and mint/burn with no on-chain path for the original creator to reclaim control - (File: `x/tokenfactory/keeper/msg_server.go`)

### Summary
The Rigor `Project.sol` bug lets a builder irrevocably delegate contractor-only privileges (adding tasks, marking tasks complete) to an address that then cannot be removed or replaced by the builder — only the delegate itself can hand back control. The `x/tokenfactory` module has the same structural weakness: a denom's `admin` can transfer administrative rights to another address via `MsgChangeAdmin`, and only the current admin can sign a further `MsgChangeAdmin` to change it again. If the delegate is malicious or unresponsive, the original creator has no on-chain mechanism to reclaim or replace the admin.

### Finding Description
`ChangeAdmin` in `x/tokenfactory/keeper/msg_server.go` requires `msg.Sender == authorityMetadata.GetAdmin()` before it will rewrite the admin field: [1](#0-0) 

There is no alternate authority (governance, module owner, or the original creator) that can override this check. The module's own `NewProposalHandler` explicitly rejects all governance content for tokenfactory: [2](#0-1) 

Once admin rights are transferred (the documented, intended `authz`-style delegation flow), the new admin has exclusive, irrevocable control of `Mint`, `Burn`, `ChangeAdmin`, `SetDenomMetadata`, and `UpdateDenom` (which sets the bank `AllowList` for the denom) as described in the module README: [3](#0-2) 

A malicious admin can, for example, call `MsgUpdateDenom` to set a restrictive `AllowList` that blocks all transfers of the denom (enforced in `BaseSendKeeper.IsInDenomAllowList`/`CanSendTo`), effectively freezing every holder's balance of that denom, or refuse ever to `ChangeAdmin` back — exactly analogous to the contractor in `Project.sol` who can block funding/task completion and cannot be replaced once delegated.

### Impact Explanation
For any tokenfactory denom whose creator has delegated admin rights (a documented, encouraged pattern via the `authz` module), a malicious or non-cooperative delegate can:
- Permanently block all transfers of that denom by setting a restrictive allow list, freezing user funds in that denom.
- Mint/burn tokens arbitrarily (supply manipulation) with no recourse.
- Refuse to ever sign a `ChangeAdmin` message back to the original creator, since the module keeper only accepts a `ChangeAdmin` signed by the current admin and there is no governance escape hatch.

This matches the "permanent freezing of funds" / "unauthorized transfer" criteria, scoped to holders of the affected denom.

### Likelihood Explanation
As with the original finding, this requires a chosen delegation of trust — the denom creator voluntarily transfers admin rights to another account. It is not exploitable against an unwilling party. Likelihood is therefore low, mirroring the acknowledged-but-lower-severity judgment on the original report, since it stems from an intentional design choice (delegatable, exclusive single-key admin with no recovery path) rather than an externally-reachable flaw.

### Recommendation
Provide a recovery mechanism analogous to the report's suggested mitigations for `Project.sol`:
1. Allow a governance-gated `MsgChangeAdmin`-equivalent path (e.g., via a tokenfactory governance proposal handler, currently stubbed out in `x/tokenfactory/handler.go`) so a malicious/unresponsive admin can be replaced.
2. Alternatively, support time-locked or multi-party admin transfer patterns so denom control cannot be permanently and unilaterally frozen by a single delegated key.

### Proof of Concept
1. Account `A` creates denom `factory/A/foo` via `MsgCreateDenom`, becoming its admin.
2. `A` calls `MsgChangeAdmin` to delegate admin rights to `B` (the intended, documented flow for shared admin privileges). [1](#0-0) 
3. `B` calls `MsgUpdateDenom` to set an `AllowList` containing only `B`'s own address, or simply never signs a `MsgChangeAdmin` back to `A`.
4. All other holders of `factory/A/foo` are now permanently blocked from sending/receiving the denom (enforced via `BaseSendKeeper.IsInDenomAllowList`), and `A` has no on-chain call that can revoke or replace `B`'s admin status. [4](#0-3)

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

**File:** x/tokenfactory/handler.go (L11-15)
```go
func NewProposalHandler(_ keeper.Keeper) govtypes.Handler {
	return func(ctx sdk.Context, content govtypes.Content) error {
		return sdkerrors.Wrapf(sdkerrors.ErrUnknownRequest, "unrecognized tokenfactory proposal content type")
	}
}
```

**File:** x/tokenfactory/README.md (L8-18)
```markdown
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

**File:** sei-cosmos/x/bank/keeper/send.go (L506-524)
```go
func (k BaseSendKeeper) IsInDenomAllowList(ctx sdk.Context, addr sdk.AccAddress, coins sdk.Coins, cache map[string]AllowedAddresses) bool {
	for _, coin := range coins {
		// Skip if denom does not contain the token factory prefix
		if !strings.HasPrefix(coin.Denom, TokenFactoryPrefix) {
			continue
		}

		allowedAddresses := k.getAllowedAddresses(ctx, cache, coin.Denom)
		// skip if there is no allow list for the denom
		if len(allowedAddresses.set) == 0 {
			continue
		}

		if !allowedAddresses.contains(addr) {
			return false
		}
	}
	return true
}
```
