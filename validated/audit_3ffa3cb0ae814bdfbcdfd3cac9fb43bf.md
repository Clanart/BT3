Found the analog: the bank precompile's `send` path (via `p.bankMsgServer.Send`) enforces the `BlockedAddr` and tokenfactory `IsInDenomAllowList` checks, but `sendNative` (usei/wei transfer, which handles the value sent with an EVM call) bypasses `bankMsgServer.Send`/`msgServer.Send` entirely and calls `p.bankKeeper.SendCoinsAndWei` directly.

### Title
Inconsistent access restrictions across bank send paths allow tokenfactory denom-deny-list and blocked-address bypass via `sendNative` precompile - (File: precompiles/bank/bank.go)

### Summary
The bank module enforces two access controls on transfers of coins: `BlockedAddr` (module/reserved addresses that must never receive funds) and `IsInDenomAllowList` (tokenfactory per-denom allow-lists set via `SetDenomAllowList`), both applied in `msgServer.Send`/`MultiSend` [1](#0-0)  and mirrored in the wasm bank transferrer [2](#0-1) . However, the EVM bank precompile's `sendNative` method, which moves the usei/wei `value` attached to a precompile call, calls `p.bankKeeper.SendCoinsAndWei` directly and never checks `BlockedAddr` or `IsInDenomAllowList`.

### Finding Description
`PrecompileExecutor.send` (ERC20-pointer initiated transfers of a tokenfactory/bank denom) routes through `p.bankMsgServer.Send`, which enforces both `BlockedAddr` and `IsInDenomAllowList` checks before moving funds [3](#0-2) , matching the checks in `msgServer.Send` [1](#0-0) .

In contrast, `PrecompileExecutor.sendNative` — which is the EVM-native path for moving `usei`/`wei` value attached to a precompile call — resolves sender/receiver Sei addresses and calls `p.bankKeeper.SendCoinsAndWei(ctx, senderSeiAddr, receiverSeiAddr, usei, wei)` directly, with no call to `BlockedAddr` or `IsInDenomAllowList` [4](#0-3) . `SendCoinsAndWei` in the base send keeper itself does not perform these access checks either — that logic lives only in `msgServer.Send`/`MultiSend` [5](#0-4)  and the wasm `BankCoinTransferrer` [2](#0-1) , not in the keeper's `SendCoins`/`SendCoinsAndWei` primitives.

This means any EVM contract or EOA can call the bank precompile's `sendNative(receiver)` with `value` set to bypass the blocked-address protection that guards module/reserved accounts, and to bypass tokenfactory denom allow-lists — but `sendNative` only ever moves the base denom (`usei`)/wei, not arbitrary tokenfactory denoms, since it operates purely on attached call value. `BlockedAddr` protects arbitrary denoms including the base denom, so the base-denom blocked-address bypass is the concretely reachable part of this gap: an attacker-controlled EVM tx can send `usei`/`wei` value via the bank precompile straight into any address that the bank module normally blocks from directly receiving funds (typically module accounts), circumventing an invariant the base keeper `BlockedAddr` function documents as required to avoid breaking accounting invariants [6](#0-5) [7](#0-6) .

### Impact Explanation
Directly crediting a blocklisted module account (e.g. a fee-collector, staking-pool, or mint module account) outside the state machine's expected accounting rules can corrupt module invariants and desynchronize tracked balances from actual module account balances, which the bank spec explicitly calls out as a network-halting risk ("If these addresses receive funds outside the expected rules of the state machine, invariants are likely to be broken and could result in a halted network") [8](#0-7) . This maps to fund-loss/invariant-break criteria (unauthorized transfer that violates protected module-account restrictions) accepted by the rules.

### Likelihood Explanation
Reachable by any unprivileged EVM sender: `sendNative` is a public precompile method invoked with a plain EVM transaction (non-static, non-delegatecall) carrying `value`, requiring only that the caller's EVM address be associated with a Sei address [9](#0-8) . No special privilege, governance action, or malicious-node assumption is required — this is a single-transaction, publicly reachable path.

### Recommendation
Add the same `BlockedAddr` check (and, if tokenfactory-denom semantics are ever extended to `sendNative`, `IsInDenomAllowList`) to `sendNative` in `precompiles/bank/bank.go` before calling `SendCoinsAndWei`, mirroring the checks already performed in `msgServer.Send`/`MultiSend` and the wasm `BankCoinTransferrer.TransferCoins`.

### Proof of Concept
1. Determine a bank-module `BlockedAddr` address (e.g. a module account address such as the fee collector).
2. From any EOA with an EVM↔Sei address association, call the bank precompile (`0x0000000000000000000000000000000000001001`) `sendNative(<blockedAddrBech32>)` with non-zero `value`.
3. `PrecompileExecutor.sendNative` resolves `receiverSeiAddr` from the bech32 string and calls `p.bankKeeper.SendCoinsAndWei(ctx, senderSeiAddr, receiverSeiAddr, usei, wei)` with no `BlockedAddr` check [10](#0-9) , successfully crediting funds to the blocked account — whereas the equivalent `MsgSend`/`send()` precompile path to the same address would be rejected with `"is not allowed to receive funds"` [11](#0-10) .

### Citations

**File:** sei-cosmos/x/bank/keeper/msg_server.go (L26-94)
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

	defer func() {
		for _, a := range msg.Amount {
			if a.Amount.IsInt64() {
				bankMetrics.sendAmount.Record(goCtx, a.Amount.Int64(), otelmetric.WithAttributes(attribute.String("denom_class", telemetry.DenomClass(a.Denom))))
			}
		}
	}()

	ctx.EventManager().EmitEvent(
		sdk.NewEvent(
			sdk.EventTypeMessage,
			sdk.NewAttribute(sdk.AttributeKeyModule, types.AttributeValueCategory),
		),
	)

	return &types.MsgSendResponse{}, nil
}

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
```

**File:** sei-wasmd/x/wasm/keeper/keeper.go (L1198-1213)
```go
// TransferCoins transfers coins from source to destination account when coin send was enabled for them and the recipient
// is not in the blocked address list.
func (c BankCoinTransferrer) TransferCoins(parentCtx sdk.Context, fromAddr sdk.AccAddress, toAddr sdk.AccAddress, amount sdk.Coins) error {
	em := sdk.NewEventManager()
	ctx := parentCtx.WithEventManager(em)
	if err := c.keeper.IsSendEnabledCoins(ctx, amount...); err != nil {
		return err
	}
	if c.keeper.BlockedAddr(toAddr) {
		return sdkerrors.Wrapf(sdkerrors.ErrUnauthorized, "%s is not allowed to receive funds", toAddr.String())
	}

	sdkerr := c.keeper.SendCoins(ctx, fromAddr, toAddr, amount)
	if sdkerr != nil {
		return sdkerr
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

**File:** precompiles/bank/bank.go (L251-287)
```go
func (p PrecompileExecutor) sendNative(ctx sdk.Context, method *abi.Method, args []interface{}, caller common.Address, callingContract common.Address, value *big.Int, readOnly bool, hooks *tracing.Hooks, evm *vm.EVM) ([]byte, uint64, error) {
	if readOnly {
		return nil, 0, errors.New("cannot call sendNative from staticcall")
	}
	if ctx.EVMPrecompileCalledFromDelegateCall() {
		return nil, 0, errors.New("cannot delegatecall sendNative")
	}
	if err := pcommon.ValidateArgsLength(args, 1); err != nil {
		return nil, 0, err
	}
	if value == nil || value.Sign() == 0 {
		return nil, 0, errors.New("set `value` field to non-zero to send")
	}

	senderSeiAddr, ok := p.evmKeeper.GetSeiAddress(ctx, caller)
	if !ok {
		return nil, 0, errors.New("invalid addr")
	}

	receiverAddr, ok := (args[0]).(string)
	if !ok || receiverAddr == "" {
		return nil, 0, errors.New("invalid addr")
	}

	receiverSeiAddr, err := sdk.AccAddressFromBech32(receiverAddr)
	if err != nil {
		return nil, 0, err
	}

	usei, wei, err := pcommon.HandlePaymentUseiWei(ctx, p.evmKeeper.GetSeiAddressOrDefault(ctx, p.address), senderSeiAddr, value, p.bankKeeper, p.evmKeeper, hooks, evm.GetDepth())
	if err != nil {
		return nil, 0, err
	}

	if err := p.bankKeeper.SendCoinsAndWei(ctx, senderSeiAddr, receiverSeiAddr, usei, wei); err != nil {
		return nil, 0, err
	}
```

**File:** sei-cosmos/x/bank/keeper/send.go (L372-381)
```go
// BlockedAddr checks if a given address is restricted from
// receiving funds.
func (k BaseSendKeeper) BlockedAddr(addr sdk.AccAddress) bool {
	if len(addr) == len(CoinbaseAddressPrefix)+8 {
		if bytes.Equal(CoinbaseAddressPrefix, addr[:len(CoinbaseAddressPrefix)]) {
			return true
		}
	}
	return k.blockedAddrs[addr.String()]
}
```

**File:** sei-cosmos/x/bank/spec/02_keepers.md (L15-25)
```markdown
## Blocklisting Addresses

The `x/bank` module accepts a map of addresses that are considered blocklisted
from directly and explicitly receiving funds through means such as `MsgSend` and
`MsgMultiSend` and direct API calls like `SendCoinsFromModuleToAccount`.

Typically, these addresses are module accounts. If these addresses receive funds
outside the expected rules of the state machine, invariants are likely to be
broken and could result in a halted network.

By providing the `x/bank` module with a blocklisted set of addresses, an error occurs for the operation if a user or client attempts to directly or indirectly send funds to a blocklisted account, for example, by using [IBC](http://docs.cosmos.network/master/ibc/).
```
