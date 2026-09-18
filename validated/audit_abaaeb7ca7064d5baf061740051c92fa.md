## Finding

### Title
Incomplete fix: legacy Bank precompile `send()` versions (v562/v580/v600) bypass tokenfactory allow-list/deny-list checks enforced by the current version and by `bank.MsgServer.Send` - (File: `precompiles/bank/legacy/v562/bank.go`, `precompiles/bank/legacy/v580/bank.go`, `precompiles/bank/legacy/v600/bank.go`)

### Summary
The current Bank precompile's `send()` method routes transfers through the full Cosmos SDK message-processing pipeline via `p.bankMsgServer.Send(...)` [1](#0-0) , which enforces `IsSendEnabledCoins` and, critically, `IsInDenomAllowList` for both sender and recipient [2](#0-1) . However, three older legacy versions of the same precompile (`v562`, `v580`, `v600`) implement `send()` by calling `p.bankKeeper.SendCoins(...)` directly [3](#0-2) [4](#0-3) , which does **not** perform the `IsInDenomAllowList` check that is the entire enforcement mechanism for tokenfactory denom allow-lists (`x/tokenfactory` `MsgUpdateDenom`'s allow-list feature, backed by `bankKeeper.SetDenomAllowList`/`GetDenomAllowList` in `sei-cosmos/x/bank/keeper/send.go`).

Starting at `v603` (the version immediately after `v600`), the fix was applied and `send()` was changed to go through `p.bankMsgServer.Send(...)` instead of `SendCoins` directly - matching the exact "sibling function not receiving the fix" pattern described in the reference report (`GHSA-985r-q3qp-299h`): one code path got hardened, while structurally identical, still-reachable sibling code paths were left unpatched.

### Finding Description
sei-chain uses a versioned-precompile mechanism (seen consistently across `precompiles/*/legacy/vXXX/` directories for `addr`, `pointer`, `bank`, `gov`, etc.) where the EVM dispatches precompile calls to different Go implementations depending on the chain-upgrade height that was active when the calling logic/pointer was established, so old, no-longer-"current" precompile code remains live and callable at the same precompile address for pre-upgrade-pinned execution paths. This is registered in `precompiles/bank/setup.go`.

Within this versioned family, the `send()` handler for the Bank precompile evolved:
- v562/v580/v600 (older): validate the pointer/caller relationship, then call `p.bankKeeper.SendCoins(ctx, senderSeiAddr, receiverSeiAddr, ...)` directly [5](#0-4) .
- v603 onward (current `precompiles/bank/bank.go`): construct a `banktypes.MsgSend`, call `msg.ValidateBasic()`, then dispatch through `p.bankMsgServer.Send(sdk.WrapSDKContext(ctx), msg)` [1](#0-0) .

The Cosmos bank module's `MsgServer.Send` is the only place enforcing `IsInDenomAllowList` for both `from` and `to` addresses [6](#0-5) , which is the mechanism the tokenfactory module relies on to let a denom admin restrict who may hold/transact a tokenfactory-created denom via `MsgUpdateDenom`'s `AllowList` field [7](#0-6) . `BaseSendKeeper.SendCoins` (called directly by the legacy precompiles) has no knowledge of this allow-list at all — the check lives exclusively in the `MsgServer.Send` wrapper, not in `SendCoins`/`sendCoins` internals.

Because the legacy precompile versions remain live and reachable by any EVM contract/pointer whose execution context resolves to that older precompile version, an unprivileged EVM caller can invoke the ERC20-native-pointer `send` interface on that legacy path to move a tokenfactory denom into or out of an address that the denom's admin has deliberately excluded via the allow-list, completely defeating the access-control feature — the exact "sibling method left unguarded after a partial fix" bug class from the external report.

### Impact Explanation
This allows unauthorized transfer of tokenfactory-denominated funds through the Bank precompile: an address blocked from sending or receiving a given tokenfactory denom (via its admin-set allow-list) can still move that denom by calling the vulnerable legacy precompile version's `send()` method, since `SendCoins` performs no allow-list enforcement. This is an authorization bypass on a fund-movement control, matching the impact tier of "unauthorized transfer via precompile" called out as in-scope.

### Likelihood Explanation
Reachable directly by any unprivileged EVM transaction sender or contract that can trigger the versioned Bank precompile's legacy code path (v562/v580/v600) for a pointer contract associated with a tokenfactory denom protected by an allow-list. No special privilege, governance action, or validator collusion is required — only a standard `MsgEVMTransaction`/contract call reaching the precompile at the pinned legacy version.

### Recommendation
Apply the same hardening used in the current `precompiles/bank/bank.go` (and v603+) to the legacy `send()` implementations in `precompiles/bank/legacy/v562/bank.go`, `precompiles/bank/legacy/v580/bank.go`, and `precompiles/bank/legacy/v600/bank.go`: route the transfer through `bankMsgServer.Send` (or otherwise explicitly invoke `IsSendEnabledCoins` and `IsInDenomAllowList` for both `from` and `to`) instead of calling `SendCoins` directly, regardless of which precompile version is dispatched.

### Proof of Concept
1. A tokenfactory denom admin restricts transacting addresses using `MsgUpdateDenom` with an `AllowList` that excludes attacker address `A` [7](#0-6) .
2. A native ERC20 pointer contract for that denom exists whose EVM call path resolves to the legacy Bank precompile version (`v562`/`v580`/`v600`) — e.g. because it was created/pinned prior to the upgrade that shipped the `v603` fix.
3. Attacker `A` (or anyone sending to `A`) calls the pointer's `send(...)` which reaches the legacy precompile's `send()`, which calls `p.bankKeeper.SendCoins(...)` directly [8](#0-7) , with no `IsInDenomAllowList` check performed anywhere in that call path.
4. The transfer succeeds despite `A` being on the denom's exclusion list, whereas the same call against the current `precompiles/bank/bank.go` `send()` would be rejected by `MsgServer.Send`'s allow-list check [6](#0-5) .

**Note on confidence:** I was unable to fully verify, within the available tools, the exact mechanism/conditions in `precompiles/bank/setup.go` that determine when the EVM actually dispatches a call to `v562`/`v580`/`v600` versus the current implementation (e.g., whether it's keyed by chain-upgrade height, per-contract creation height, or another selector), since I could not read that file's contents in this session. This detail should be confirmed before treating the analog as fully proven, though the versioned-precompile pattern is consistent across many other modules (`addr`, `pointer`, `gov`) in this codebase and each maintains equivalent "legacy" implementations that remain compiled and registered.

### Citations

**File:** precompiles/bank/bank.go (L232-245)
```go
	msg := &banktypes.MsgSend{
		FromAddress: senderSeiAddr.String(),
		ToAddress:   receiverSeiAddr.String(),
		Amount:      sdk.NewCoins(sdk.NewCoin(denom, sdk.NewIntFromBigInt(amount))),
	}

	err = msg.ValidateBasic()
	if err != nil {
		return nil, 0, err
	}

	if _, err = p.bankMsgServer.Send(sdk.WrapSDKContext(ctx), msg); err != nil {
		return nil, 0, err
	}
```

**File:** sei-cosmos/x/bank/keeper/msg_server.go (L26-49)
```go
func (k msgServer) Send(goCtx context.Context, msg *types.MsgSend) (*types.MsgSendResponse, error) {
	ctx := sdk.UnwrapSDKContext(goCtx)

	if err := k.IsSendEnabledCoins(ctx, msg.Amount...); err != nil {
		return nil, err
	}

	from, err := sdk.AccAddressFromBech32(msg.FromAddress)
	if err != nil {
		return nil, err
	}
	to, err := sdk.AccAddressFromBech32(msg.ToAddress)
	if err != nil {
		return nil, err
	}

	allowListCache := make(map[string]AllowedAddresses)
	if !k.IsInDenomAllowList(ctx, from, msg.Amount, allowListCache) {
		return nil, sdkerrors.Wrapf(sdkerrors.ErrUnauthorized, "%s is not allowed to send funds", msg.FromAddress)
	}

	if k.BlockedAddr(to) || !k.IsInDenomAllowList(ctx, to, msg.Amount, allowListCache) {
		return nil, sdkerrors.Wrapf(sdkerrors.ErrUnauthorized, "%s is not allowed to receive funds", msg.ToAddress)
	}
```

**File:** precompiles/bank/legacy/v562/bank.go (L168-170)
```go
	if err := p.bankKeeper.SendCoins(ctx, senderSeiAddr, receiverSeiAddr, sdk.NewCoins(sdk.NewCoin(denom, sdk.NewIntFromBigInt(amount)))); err != nil {
		return nil, err
	}
```

**File:** precompiles/bank/legacy/v600/bank.go (L145-170)
```go
	senderSeiAddr, err := p.accAddressFromArg(ctx, args[0])
	if err != nil {
		return nil, err
	}
	receiverSeiAddr, err := p.accAddressFromArg(ctx, args[1])
	if err != nil {
		return nil, err
	}

	if err := p.bankKeeper.SendCoins(ctx, senderSeiAddr, receiverSeiAddr, sdk.NewCoins(sdk.NewCoin(denom, sdk.NewIntFromBigInt(amount)))); err != nil {
		return nil, err
	}

	return method.Outputs.Pack(true)
}

func (p PrecompileExecutor) sendNative(ctx sdk.Context, method *abi.Method, args []interface{}, caller common.Address, callingContract common.Address, value *big.Int, readOnly bool, hooks *tracing.Hooks, evm *vm.EVM) ([]byte, error) {
	if readOnly {
		return nil, errors.New("cannot call sendNative from staticcall")
	}
	if ctx.EVMPrecompileCalledFromDelegateCall() {
		return nil, errors.New("cannot delegatecall sendNative")
	}
	if err := pcommon.ValidateArgsLength(args, 1); err != nil {
		return nil, err
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
