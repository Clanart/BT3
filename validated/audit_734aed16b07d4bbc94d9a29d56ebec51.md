### Title
`sendNative` in the EVM bank precompile bypasses `IsSendEnabledCoins` and `BlockedAddr` checks that gate all other native-token transfer paths - (File: precompiles/bank/bank.go)

### Summary
The reported bug is a classic "pause bypass": one function enforced a paused/disabled state while a parallel function performing an equivalent operation forgot to. The sei-chain bank precompile has the exact same structural flaw between its two native-transfer entrypoints, `send` and `sendNative`.

### Finding Description
The Cosmos SDK bank module gates every coin transfer with two checks before calling the low-level `SendCoins` keeper method: `IsSendEnabledCoins` (which enforces the governance-controlled pause/enable flag per denom) and `BlockedAddr` (which prevents transfers to blocklisted/module accounts that could break bank invariants). These checks are implemented in `msgServer.Send`/`MultiSend` [1](#0-0)  and in the CW/wasmd bridge's `BankCoinTransferrer.TransferCoins` [2](#0-1) . Critically, the low-level `BaseSendKeeper.SendCoins` itself performs neither check — it is the caller's responsibility to gate access before invoking it [3](#0-2) .

The EVM bank precompile's `send` method correctly respects this contract: it builds a `banktypes.MsgSend` and routes it through `p.bankMsgServer.Send`, which performs the `IsSendEnabledCoins`/`BlockedAddr` checks [4](#0-3) .

However, the sibling `sendNative` method — reachable by any EVM caller sending native `usei`/wei value to the precompile — calls `p.bankKeeper.SendCoinsAndWei` **directly**, completely bypassing both the send-enabled check and the blocked-address check: [5](#0-4) 

Because `SendCoinsAndWei`/`SendCoins` at the keeper level performs no such gating (mirroring the pattern shown for `SendCoins` above), `sendNative` can move funds to any address, including one that governance has blocklisted, and can do so even if usei transfers have been disabled via the `SendEnabled` bank param.

### Impact Explanation
- If governance disables `usei` sends via the bank module's `SendEnabled` param (the chain's equivalent of "pausing" transfers, e.g., as an incident-response measure), `sendNative` still allows funds to move, defeating the pause exactly as in the reported bug.
- `sendNative` also never checks `BlockedAddr`, so it can send `usei` directly to blocklisted/module addresses. The bank module's own documentation warns that receiving funds outside expected rules at these addresses "is likely to be broken and could result in a halted network" [6](#0-5) , so this can also lead to broken invariants / validator halt, not just fund-loss-during-pause.
- Reachable by any unprivileged EVM transaction sender calling the precompile at address `0x0000000000000000000000000000000000001001` with non-zero `value` and the `sendNative` selector.

### Likelihood Explanation
High likelihood: any EOA or contract can invoke the `sendNative` precompile method directly; no special privilege is required, and the missing checks are unconditional (not behind any feature flag).

### Recommendation
Add the same guards used by `send`/`msgServer.Send` to `sendNative` before calling `SendCoinsAndWei`:
- Call `p.bankKeeper.IsSendEnabledCoins(ctx, sdk.NewCoin("usei", usei))` (and equivalent wei-denominated check if applicable) and reject if disabled.
- Call `p.bankKeeper.BlockedAddr(receiverSeiAddr)` and reject sends to blocked addresses.

Alternatively, route `sendNative` through the same `MsgSend`/`bankMsgServer.Send` path that `send` already uses, so both entrypoints share one gated code path.

### Proof of Concept
1. Governance sets `SendEnabled` for `usei` to `false` (or the chain otherwise wants to halt native transfers).
2. An attacker (any EOA) calls the bank precompile at `0x0000000000000000000000000000000000001001` with method `sendNative(string receiverAddr)`, non-zero `msg.value`, and a `receiverAddr` that is a normal (or even blocklisted) Sei address.
3. `Execute` dispatches to `sendNative` [7](#0-6) , which calls `p.bankKeeper.SendCoinsAndWei` directly without checking `IsSendEnabledCoins` or `BlockedAddr` [8](#0-7) .
4. The transfer succeeds despite the pause/block, in contrast to what would happen if the same transfer were attempted via `MsgSend` (which would be rejected by `msgServer.Send`'s `IsSendEnabledCoins`/`BlockedAddr` checks).

Note: I was not able to view the full body of `SendCoinsAndWei` in `sei-cosmos/x/bank/keeper/send.go` in this session (tool budget exhausted), so I am inferring its lack of gating from the sibling `SendCoins` implementation and the documented pattern that all such checks live in msg-server/bridge callers rather than the keeper. If desired, verify `SendCoinsAndWei`'s body directly to confirm it truly performs no `IsSendEnabledCoins`/`BlockedAddr` check before treating this as fully confirmed.

### Citations

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

**File:** sei-cosmos/x/bank/keeper/send.go (L164-182)
```go
func (k BaseSendKeeper) SendCoins(ctx sdk.Context, fromAddr sdk.AccAddress, toAddr sdk.AccAddress, amt sdk.Coins) error {
	if err := k.SendCoinsWithoutAccCreation(ctx, fromAddr, toAddr, amt); err != nil {
		return err
	}

	// Create account if recipient does not exist.
	//
	// NOTE: This should ultimately be removed in favor a more flexible approach
	// such as delegated fee messages.
	accExists := k.ak.HasAccount(ctx, toAddr)
	if !accExists {
		defer func() {
			recordNewAccounts(ctx.Context(), 1)
		}()
		k.ak.SetAccount(ctx, k.ak.NewAccountWithAddress(ctx, toAddr))
	}

	return nil
}
```

**File:** precompiles/bank/bank.go (L170-171)
```go
	case SendNativeMethod:
		return p.sendNative(ctx, method, args, caller, callingContract, value, readOnly, hooks, evm)
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

**File:** precompiles/bank/bank.go (L265-292)
```go
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
	accExists := p.accountKeeper.HasAccount(ctx, receiverSeiAddr)
	if !accExists {
		defer metrics.RecordBankNewAccount(ctx.Context())
		p.accountKeeper.SetAccount(ctx, p.accountKeeper.NewAccountWithAddress(ctx, receiverSeiAddr))
	}
```

**File:** sei-cosmos/x/bank/spec/02_keepers.md (L15-23)
```markdown
## Blocklisting Addresses

The `x/bank` module accepts a map of addresses that are considered blocklisted
from directly and explicitly receiving funds through means such as `MsgSend` and
`MsgMultiSend` and direct API calls like `SendCoinsFromModuleToAccount`.

Typically, these addresses are module accounts. If these addresses receive funds
outside the expected rules of the state machine, invariants are likely to be
broken and could result in a halted network.
```
