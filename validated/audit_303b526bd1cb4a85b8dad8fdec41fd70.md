I found a genuine analog. In `MsgUpdateDenom.UpdateDenom` at `x/tokenfactory/keeper/msg_server.go:80-85`, the handler calls `server.bankKeeper.SetDenomAllowList(ctx, denom, *msg.AllowList)` directly, with no call to `validateAllowList` — unlike `CreateDenom` at `x/tokenfactory/keeper/msg_server.go:37-41`, which does call `server.validateAllowList(ctx, msg.AllowList)` before setting the allow list. [1](#0-0) [2](#0-1) 

### Title
Tokenfactory `MsgUpdateDenom` bypasses the denom allow-list size quota enforced on `MsgCreateDenom` - (File: x/tokenfactory/keeper/msg_server.go)

### Summary
The tokenfactory module enforces a `DenomAllowlistMaxSize` parameter that bounds the number of addresses in a denom's allow list, but this bound is checked only in the `CreateDenom` message handler, not in `UpdateDenom`.

### Finding Description
`msgServer.CreateDenom` validates any supplied `msg.AllowList` via `server.validateAllowList(ctx, msg.AllowList)` — which checks the list length against `types.Params.DenomAllowlistMaxSize` (referenced in `x/tokenfactory/keeper/createdenom_test.go:53-56`, where the test builds `largeAllowList := make([]string, allowListSize+1)` to exercise the size check) — before calling `server.bankKeeper.SetDenomAllowList`. [3](#0-2) [4](#0-3) 

By contrast, `msgServer.UpdateDenom` sets `msg.AllowList` on the bank keeper directly with no call to `validateAllowList` or any other size check: [5](#0-4) 

This is the same bug class as the Nextcloud Groupfolders issue: a resource-size bound (there, storage quota on file attachments; here, the maximum allow-list size on a denom) is enforced on one write path (`CreateDenom`) but not on a functionally equivalent alternate write path (`UpdateDenom`) that reaches the same underlying state mutation (`bankKeeper.SetDenomAllowList`). Since a denom's admin can call `UpdateDenom` at will after creation (the only pre-check being `msg.Sender == authorityMetadata.GetAdmin()`), any denom admin can attach an allow list of unbounded size, unconstrained by the parameter that is supposed to cap it chain-wide.

### Impact Explanation
An unbounded allow list per denom is a state-growth / storage-bloat vector: any user who has created a tokenfactory denom (permissionless, per `x/tokenfactory/README.md`) is admin of that denom, and can call `MsgUpdateDenom` repeatedly with arbitrarily large `AllowList.Addresses` to write state that the chain's own `DenomAllowlistMaxSize` parameter was intended to bound. Since the allow list is enforced by the bank module on every transfer of that denom (iterated on the send path), an oversized allow list also inflates gas/CPU cost of processing transfers of that denom, which can be used to degrade validator processing time.

### Likelihood Explanation
Trivially reachable: any account that has permissionlessly created a tokenfactory denom (`MsgCreateDenom`, no special privilege required) can subsequently submit `MsgUpdateDenom` with an oversized `AllowList` and successfully bypass the size cap that is otherwise enforced at creation time. No special conditions or races are required.

### Recommendation
Call the same `validateAllowList(ctx, msg.AllowList)` check in `UpdateDenom` before invoking `bankKeeper.SetDenomAllowList`, mirroring the check already present in `CreateDenom`.

### Proof of Concept
1. Create a tokenfactory denom via `MsgCreateDenom` (no allow list needed).
2. As the resulting denom admin, submit `MsgUpdateDenom` with `AllowList.Addresses` containing more entries than `Params.DenomAllowlistMaxSize` (e.g., `DenomAllowlistMaxSize + 1` as used in the existing `TestCreateDenom` test pattern at `x/tokenfactory/keeper/createdenom_test.go:55-56`).
3. Observe that the transaction succeeds and `bankKeeper.SetDenomAllowList` stores an allow list exceeding the configured maximum, whereas the same oversized list submitted via `MsgCreateDenom` would be rejected by `validateAllowList`.

**Caveat:** I was unable to fully inspect the exact error condition and numeric bound logic inside `validateAllowList` (only its call sites were confirmed in the index); the background agent should read `x/tokenfactory/keeper/createdenom.go` (where `validateAllowList` is defined, per the grep match) to confirm the precise check before patching `UpdateDenom`.

### Citations

**File:** x/tokenfactory/keeper/msg_server.go (L37-46)
```go
	if msg.AllowList != nil {
		err = server.validateAllowList(ctx, msg.AllowList)
		if err != nil {
			return nil, err
		}
		server.bankKeeper.SetDenomAllowList(ctx, denom, *msg.AllowList)
		createDenomEvent = createDenomEvent.AppendAttributes(
			sdk.NewAttribute(types.AttributeAllowList, strings.Join(msg.AllowList.Addresses, ",")),
		)
	}
```

**File:** x/tokenfactory/keeper/msg_server.go (L57-92)
```go
func (server msgServer) UpdateDenom(goCtx context.Context, msg *types.MsgUpdateDenom) (*types.MsgUpdateDenomResponse, error) {
	ctx := sdk.UnwrapSDKContext(goCtx)

	denom, err := server.validateUpdateDenom(ctx, msg)
	if err != nil {
		return nil, err
	}

	authorityMetadata, err := server.GetAuthorityMetadata(ctx, denom)
	if err != nil {
		return nil, err
	}

	if msg.Sender != authorityMetadata.GetAdmin() {
		return nil, types.ErrUnauthorized
	}

	updateDenomEvent := sdk.NewEvent(
		types.TypeMsgUpdateDenom,
		sdk.NewAttribute(types.AttributeCreator, msg.Sender),
		sdk.NewAttribute(types.AttributeUpdatedTokenDenom, denom),
	)

	if msg.AllowList != nil {
		server.bankKeeper.SetDenomAllowList(ctx, denom, *msg.AllowList)
		updateDenomEvent = updateDenomEvent.AppendAttributes(
			sdk.NewAttribute(types.AttributeAllowList, strings.Join(msg.AllowList.Addresses, ",")),
		)
	}

	ctx.EventManager().EmitEvents(sdk.Events{
		updateDenomEvent,
	})

	return &types.MsgUpdateDenomResponse{}, nil
}
```

**File:** x/tokenfactory/keeper/createdenom_test.go (L53-56)
```go
func (suite *KeeperTestSuite) TestCreateDenom() {
	params, _ := suite.queryClient.Params(suite.Ctx.Context(), &types.QueryParamsRequest{})
	allowListSize := params.Params.DenomAllowlistMaxSize
	largeAllowList := make([]string, allowListSize+1)
```
