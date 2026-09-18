### Title
Denom allow-list bypass via legacy bank precompile versions calling `SendCoins` directly instead of `MsgServer.Send` - ([File: precompiles/bank/legacy/v562/bank.go])

### Summary
The tokenfactory denom allow-list (an authorization whitelist restricting who may send/receive a specific `factory/...` denom) is enforced in exactly one place — `msgServer.Send` / `msgServer.MultiSend` in `sei-cosmos/x/bank/keeper/msg_server.go` — via calls to `IsInDenomAllowList` and `BlockedAddr`. `BaseSendKeeper.SendCoins`/`SendCoinsWithoutAccCreation`/`InputOutputCoins` themselves perform no such check. Several legacy versions of the EVM `bank` precompile (reachable at their respective historical/replay upgrade heights) call `p.bankKeeper.SendCoins(...)` directly instead of routing through the `bankMsgServer.Send` message handler, which means the allow-list enforcement is silently skipped for those code paths — structurally the same class of bug as CVE-2017-15137, where a security whitelist enforced on one entry point (`oc import`) was not enforced on another equivalent entry point (`oc tag`).

### Finding Description
`IsInDenomAllowList` is documented and implemented as the authorization gate for token-factory denoms with an allow-list configured: [1](#0-0) 

This check (and `BlockedAddr`) is invoked only inside the message-server handlers for `MsgSend`/`MsgMultiSend`: [2](#0-1) [3](#0-2) 

Newer versions of the EVM `bank` precompile correctly route transfers through the msg server, inheriting the allow-list check: [4](#0-3) 

However, multiple **legacy** precompile implementations bypass the msg server and call `SendCoins` on the bank keeper directly, which contains no allow-list check at all: [5](#0-4) [6](#0-5) 

These legacy precompile versions remain compiled into the binary and are selected at runtime based on the upgrade height associated with each version tag, via `GetVersioned`/`CustomPrecompiles`: [7](#0-6) [8](#0-7) [9](#0-8) 

Normally `CustomPrecompiles` returns only the `latestCustomPrecompiles` map (the current/latest version) unless `ctx.IsTracing()` is true, in which case the *historical* version matching the block height is resolved instead — this is by design to support consistent trace/replay of historical blocks. This means the vulnerable legacy `send` function is exercised for **historical block heights during tracing/replay**, not for new transactions on the current chain tip.

### Impact Explanation
If a chain deployed a version of the `bank` precompile in the `v5.6.2`–`v6.0.0` window (or any version calling `bankKeeper.SendCoins` directly rather than through `bankMsgServer.Send`) while that version was the *live* version at some past block height, any address could have moved tokenfactory-denom funds in/out of allow-listed accounts through `precompiles/bank`'s `send`/`sendNative` EVM entry points, bypassing the denom's allow-list — this is an unauthorized transfer via precompile for allow-list-restricted tokens, i.e., unauthorized fund movement for accounts that were supposed to be excluded from a token's transfer whitelist. Because these legacy code paths are only reachable today through `ctx.IsTracing()`-based historical resolution (used for replay/trace RPCs), the currently-exploitable surface is state-view/trace correctness rather than live consensus-affecting fund movement, but it does confirm a genuine allow-list enforcement gap in the precompile dispatch design: any future or overlooked precompile/keeper caller of `SendCoins`/`InputOutputCoins`/`TransferCoins` that does not also call `IsInDenomAllowList`/`BlockedAddr` will silently bypass the allow-list, exactly mirroring the CVE-2017-15137 pattern of an inconsistently-enforced whitelist.

### Likelihood Explanation
Likelihood on the *current* live chain tip is low, since the vulnerable `send` bypass only executes for pinned historical/tracing code paths at their original block heights. However, the underlying design flaw — allow-list enforcement centralized in `msgServer.Send`/`MultiSend` rather than in the shared `SendCoins`/`InputOutputCoins` keeper primitives — is a structural weakness: any current or future caller of these primitives outside the msg server (precompiles, CosmWasm bank message encoders, module-to-module transfers) will bypass the allow-list by default, requiring careful auditing rather than being enforced by a single choke point.

### Recommendation
Move the `IsInDenomAllowList`/`BlockedAddr` allow-list enforcement into the shared `BaseSendKeeper.SendCoins`/`InputOutputCoins` primitives (or a wrapper all callers are forced through) rather than duplicating it only in `msgServer.Send`/`MultiSend`, so that every current and future caller — precompiles (including legacy/historical versions), CosmWasm bank message handling, and any future module — is uniformly subject to the same denom allow-list check, eliminating the class of whitelist-bypass bugs illustrated by the legacy `bank` precompile versions.

### Proof of Concept
1. Configure a token-factory denom `factory/<admin>/X` with `SetDenomAllowList` restricting transfers to a specific set of addresses (as in `TestSendReceiverNotInAllowList` at `sei-cosmos/x/bank/app_test.go:117-149`).
2. Using the EVM RPC tracing/replay path that resolves the precompile version historically active at a block height where a vulnerable legacy `bank` precompile version (e.g. `v5.6.2`/`v5.8.0`) was live, invoke `send`/`sendNative` on the bank precompile from/to an address not in the allow-list.
3. Because `precompiles/bank/legacy/v562/bank.go`'s `send` calls `p.bankKeeper.SendCoins` directly rather than `p.bankMsgServer.Send`, the transfer succeeds despite the sender/receiver not being present in the denom's `AllowList`, whereas the same transfer through `MsgSend`/current precompile version would be rejected with `"... is not allowed to send/receive funds"` as seen in `TestSendReceiverNotInAllowList`.

### Citations

**File:** sei-cosmos/x/bank/keeper/send.go (L501-524)
```go
// IsInDenomAllowList checks if the given address is allowed to send the given coins.
// The check is performed only fot token factory denoms. For each token factory denom,
// it checks if there is allow list for the given denom. If there is no allow list,
// the address is allowed to send the coins. If there is an allow list, the address is
// allowed to send the coins only if it is in the allow list.
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

**File:** sei-cosmos/x/bank/keeper/msg_server.go (L26-54)
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

	err = k.SendCoins(ctx, from, to, msg.Amount)
	if err != nil {
		return nil, err
	}
```

**File:** sei-cosmos/x/bank/keeper/msg_server.go (L74-99)
```go
func (k msgServer) MultiSend(goCtx context.Context, msg *types.MsgMultiSend) (*types.MsgMultiSendResponse, error) {
	ctx := sdk.UnwrapSDKContext(goCtx)
	denomToAllowListCache := make(map[string]AllowedAddresses)
	// NOTE: totalIn == totalOut should already have been checked
	for _, in := range msg.Inputs {
		if err := k.IsSendEnabledCoins(ctx, in.Coins...); err != nil {
			return nil, err
		}
		accAddr := sdk.MustAccAddressFromBech32(in.Address)
		if !k.IsInDenomAllowList(ctx, accAddr, in.Coins, denomToAllowListCache) {
			return nil, sdkerrors.Wrapf(sdkerrors.ErrUnauthorized, "%s is not allowed to send funds", accAddr)
		}
	}

	for _, out := range msg.Outputs {
		accAddr := sdk.MustAccAddressFromBech32(out.Address)

		if k.BlockedAddr(accAddr) || !k.IsInDenomAllowList(ctx, accAddr, out.Coins, denomToAllowListCache) {
			return nil, sdkerrors.Wrapf(sdkerrors.ErrUnauthorized, "%s is not allowed to receive funds", out.Address)
		}
	}

	err := k.InputOutputCoins(ctx, msg.Inputs, msg.Outputs)
	if err != nil {
		return nil, err
	}
```

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

**File:** precompiles/bank/legacy/v562/bank.go (L159-170)
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
```

**File:** precompiles/bank/legacy/v580/bank.go (L145-156)
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
```

**File:** precompiles/bank/setup.go (L26-46)
```go
func GetVersioned(latestUpgrade string, keepers utils.Keepers) utils.VersionedPrecompiles {
	return utils.VersionedPrecompiles{
		latestUpgrade: check(NewPrecompile(keepers)),
		"v5.5.2":      check(bankv552.NewPrecompile(keepers)),
		"v5.5.5":      check(bankv555.NewPrecompile(keepers)),
		"v5.6.2":      check(bankv562.NewPrecompile(keepers)),
		"v5.8.0":      check(bankv580.NewPrecompile(keepers)),
		"v6.0.0":      check(bankv600.NewPrecompile(keepers)),
		"v6.0.1":      check(bankv601.NewPrecompile(keepers)),
		"v6.0.3":      check(bankv603.NewPrecompile(keepers)),
		"v6.0.5":      check(bankv605.NewPrecompile(keepers)),
		"v6.0.6":      check(bankv606.NewPrecompile(keepers)),
		"v6.1.0":      check(bankv610.NewPrecompile(keepers)),
		"v6.1.4":      check(bankv614.NewPrecompile(keepers)),
		"v6.2.0":      check(bankv620.NewPrecompile(keepers)),
		"v6.3.0":      check(bankv630.NewPrecompile(keepers)),
		"v6.4.0":      check(bankv640.NewPrecompile(keepers)),
		"v6.5":        check(bankv65.NewPrecompile(keepers)),
		"v6.6":        check(bankv66.NewPrecompile(keepers)),
	}
}
```

**File:** x/evm/keeper/keeper.go (L159-169)
```go
func (k *Keeper) TraceSnapshotStore() *TraceSnapshotStore     { return k.traceSnapshotStore }
func (k *Keeper) SetTraceSnapshotCapture(f func() sctypes.Committer) {
	k.traceSnapshotCapture = f
}

func (k *Keeper) SetCustomPrecompiles(cp map[common.Address]putils.VersionedPrecompiles, latestUpgrade string) {
	k.customPrecompiles = cp
	k.latestUpgrade = latestUpgrade
	k.latestCustomPrecompiles = make(map[common.Address]vm.PrecompiledContract, len(cp))
	for addr, versioned := range cp {
		k.latestCustomPrecompiles[addr] = versioned[latestUpgrade]
```

**File:** x/evm/keeper/keeper.go (L185-210)
```go
func (k *Keeper) GetCustomPrecompilesVersions(ctx sdk.Context) map[common.Address]string {
	height := ctx.BlockHeight()
	cp := make(map[common.Address]string, len(k.customPrecompiles))
	for addr, versioned := range k.customPrecompiles {
		mostRecentUpgradeHeight := int64(0)
		noForkHistory := true
		for upgrade := range versioned {
			upgradeHeight := k.upgradeKeeper.GetDoneHeight(ctx, upgrade)
			if upgradeHeight != 0 {
				noForkHistory = false
			}
			if height < upgradeHeight {
				// requested height hasn't seen this upgrade version yet.
				continue
			}
			if upgradeHeight > mostRecentUpgradeHeight {
				mostRecentUpgradeHeight = upgradeHeight
				cp[addr] = upgrade
			}
		}
		if noForkHistory {
			cp[addr] = k.latestUpgrade
		}
	}
	return cp
}
```
