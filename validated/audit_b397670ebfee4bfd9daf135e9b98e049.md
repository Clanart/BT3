### Title
Native `authz` `MsgGrant` accepts an already-expired grant expiration timestamp - (File: sei-cosmos/x/authz/authorization_grant.go)

### Summary
The Cosmos `x/authz` module's `NewGrant` constructor, which backs the `MsgGrant` transaction handled by `x/authz/keeper/msg_server.go`, has its expiration-in-the-future validation disabled. Any account can submit an `authz.MsgGrant` (directly via a Cosmos tx, or indirectly through the `authz`/`gov`/`staking`/`distribution`/`slashing` EVM precompiles when the legacy code path is used) with an `expiration` timestamp that is already in the past, or equal to/less than the current block time, without the transaction being rejected for that reason.

### Finding Description
`NewGrant` is supposed to reject an expiration that is not after the current block time, but the check is commented out with a "TODO: add this for 0.45" note: [1](#0-0) 

This mirrors exactly the reported bug class in the external report: a time/expiration value is accepted without validating it against `block.timestamp` (here, `ctx.BlockTime()`), so a value that is already "expired" at creation time is stored as valid.

By contrast, the newer EVM-facing precompile helper `GrantAuthorizations` in `precompiles/common/authorization.go` (and its legacy `v67` copy) does perform this exact check before calling `authztypes.NewMsgGrant`: [2](#0-1) 

However, this defense only exists in the precompile wrapper code, not in the underlying `x/authz` module itself. Any caller that reaches `MsgGrant` through the native Cosmos SDK message path (bank/gov/staking/authz `MsgServer.Grant`, `x/authz/keeper/msg_server.go`) bypasses the precompile's `validateAuthorizationExpiration` check entirely, because that validation lives only in `precompiles/common/authorization.go`/`v67`, not in `NewGrant` or in `MsgGrant.ValidateBasic()`/the keeper's `Grant` handler.

### Impact Explanation
An `authz` grant created with an expiration timestamp in the past (or equal to the current block time) is technically already-expired the moment it is stored. Depending on how the grant's expiration is later evaluated (e.g., `Grant.Expiration.Before(ctx.BlockTime())` pruning logic elsewhere in the keeper), this can produce inconsistent/incorrect authorization-lifetime accounting: a grantee could either be denied use of a nominally-valid grant immediately, or — more importantly for security — stale/expired grants could persist in state longer than intended if downstream expiration-pruning logic assumes `NewGrant` already guaranteed a future expiration and skips re-validation, creating windows where an authorization that should be treated as invalid is instead treated as active until explicit pruning runs. This is a state-correctness/authorization-integrity issue in a module that gates delegated execution of arbitrary bank/staking/gov/distribution messages, so an inconsistency here has multiplied downstream security-relevant consequences (unauthorized message execution windows via `MsgExec`).

### Likelihood Explanation
The bug is trivially reachable: any unprivileged account can submit a `MsgGrant` transaction directly (no admin/governance permission required) with a past `expiration` field, and the module-level `NewGrant`/`ValidateBasic` path performs no rejection based on block time. The only guard that exists is in the precompile wrapper, which is not universally used by all callers/module handlers of `MsgGrant`.

### Recommendation
Re-enable the disabled check in `NewGrant` (or add equivalent validation in `x/authz/keeper/keeper.go`'s grant-saving path / `MsgServer.Grant`) so any `MsgGrant`, regardless of whether it originates from the precompile wrapper or a raw Cosmos transaction, is rejected when `!expiration.After(ctx.BlockTime())`, consistent with the check already implemented in `precompiles/common/authorization.go`.

### Proof of Concept
1. Submit a standard Cosmos SDK `MsgGrant` transaction (bypassing the EVM `authz` precompile) with `expiration` set to a timestamp earlier than the current block time.
2. Observe that `NewGrant` (`sei-cosmos/x/authz/authorization_grant.go:12-20`) performs no rejection, because the future-expiration check is commented out.
3. The grant is persisted via the keeper with an expiration already in the past, unlike calls made through `precompiles/common/authorization.go`, which would have rejected the same input with `sdkerrors.ErrInvalidRequest.Wrap("authorization expiration must be after the current block time")`.

### Citations

**File:** sei-cosmos/x/authz/authorization_grant.go (L12-20)
```go
// NewGrant returns new Grant
func NewGrant( /*blockTime time.Time, */ a Authorization, expiration time.Time) (Grant, error) {
	// TODO: add this for 0.45
	// if !expiration.After(blockTime) {
	// 	return Grant{}, sdkerrors.ErrInvalidRequest.Wrapf("expiration must be after the current block time (%v), got %v", blockTime.Format(time.RFC3339), expiration.Format(time.RFC3339))
	// }
	g := Grant{
		Expiration: expiration,
	}
```

**File:** precompiles/common/authorization.go (L65-75)
```go
// validateAuthorizationExpiration keeps the user-provided time within the
// protobuf Timestamp range used to marshal native authz grants.
func validateAuthorizationExpiration(ctx sdk.Context, expiration time.Time) error {
	if !expiration.After(ctx.BlockTime()) {
		return sdkerrors.ErrInvalidRequest.Wrap("authorization expiration must be after the current block time")
	}
	if _, err := gogotypes.TimestampProto(expiration); err != nil {
		return sdkerrors.ErrInvalidRequest.Wrapf("authorization expiration cannot be encoded as a protobuf timestamp: %v", err)
	}
	return nil
}
```
