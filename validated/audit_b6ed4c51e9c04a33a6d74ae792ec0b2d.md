### Title
`bank` precompile `sendNative` bypasses `BlockedAddr`/`SendEnabled` restrictions enforced by the standard `MsgSend` path - (File: precompiles/bank/bank.go)

### Summary
The `bank` precompile exposes two usei-transfer paths to EVM callers: `send` and `sendNative`. `send` routes through the bank module's `MsgServer.Send`, which enforces `IsSendEnabledCoins`, sender/receiver `IsInDenomAllowList`, and `BlockedAddr` checks. `sendNative`, used for native usei/wei transfers (the path every plain-value EVM transaction goes through), calls `bankKeeper.SendCoinsAndWei` directly, skipping those same authorization checks.

### Finding Description
`precompiles/bank/bank.go`'s `send` method builds a `banktypes.MsgSend` and dispatches it through `p.bankMsgServer.Send(...)`: [1](#0-0) 

The bank module's `MsgServer.Send` implementation is where the module-level restrictions live — `IsSendEnabledCoins`, `IsInDenomAllowList` for both sender and receiver, and critically `BlockedAddr(to)`, which prevents transfers to protected module accounts (e.g. fee collector, mint, distribution, etc.) that are not supposed to receive arbitrary external transfers: [2](#0-1) 

However `sendNative`, the method used for plain native-value transfers (the path taken whenever an EVM account sends `value` in a transaction/message call), does not go through `bankMsgServer`. It calls `p.bankKeeper.SendCoinsAndWei` directly: [3](#0-2) 

`SendCoinsAndWei` operates at the raw bank-keeper level and does not perform the `BlockedAddr`/`IsInDenomAllowList`/`IsSendEnabledCoins` authorization checks that `MsgServer.Send` performs before calling the equivalent keeper method. This is structurally the same bug class as the Redmine CVE: one API surface (`MsgSend`/`add_issue_notes`-gated endpoint) enforces a permission/restriction check, while an alternate reachable surface (`sendNative`/Issues API) that achieves an equivalent effect omits that same check.

### Impact Explanation
An EVM caller (any unprivileged transaction sender or contract) can call the `bank` precompile's `sendNative(receiver)` with `msg.value` set, directing usei/wei to an address that the bank module's `BlockedAddr` list would otherwise reject (e.g. module accounts for fee collection, minting, distribution, staking bonded/not-bonded pools, or any address flagged as blocked for invariant-safety reasons). Sending funds to such addresses outside of the module's expected accounting flow can corrupt module invariants (e.g., a module account's balance no longer matching its internally tracked accounting), or lock funds permanently into a module account with no user-facing withdrawal path, or (as this is combined with `IsSendEnabledCoins` bypass) allow the movement of funds whose denom transfers have been globally or per-denom disabled by governance. Because `sendNative` is the canonical path used for all native-value EVM transfers, this is broadly and trivially reachable by any user, and the consequence — permanent freezing of funds or module-invariant corruption — falls within "concrete fund loss or permanent freezing" per the validation criteria.

### Likelihood Explanation
Every EVM value-transfer to a Sei address effectively routes through this precompile logic (or `SendCoinsAndWei` is used equivalently for msg.value handling), and no privileged setup is required — a single crafted EVM transaction with `value` set and a target blocked-module bech32 address is sufficient. The bypass is unconditional: there is no attempt anywhere in `sendNative` to consult `BlockedAddr`, `IsSendEnabledCoins`, or `IsInDenomAllowList` before calling `SendCoinsAndWei`, unlike its sibling `send` method which explicitly goes through `bankMsgServer.Send`.

### Recommendation
Route `sendNative`'s native transfer through the same authorization checks enforced by `MsgServer.Send` — either by calling `bankMsgServer.Send`-equivalent restriction checks (`IsSendEnabledCoins`, `IsInDenomAllowList`, `BlockedAddr`) before invoking `SendCoinsAndWei`, or by refactoring `SendCoinsAndWei` itself to perform these checks so all bank-keeper entry points enforce the same restrictions consistently.

### Proof of Concept
1. Identify a bech32 Sei address that is registered in the bank keeper's `BlockedAddr` set (e.g. a module account such as the fee collector or a staking pool address).
2. From any EVM account, call the `bank` precompile at `0x0000000000000000000000000000000000001001`, method `sendNative(string receiver)`, setting `receiver` to that blocked address's bech32 string and attaching `value` (usei/wei) to the call.
3. Observe that `p.bankKeeper.SendCoinsAndWei(ctx, senderSeiAddr, receiverSeiAddr, usei, wei)` succeeds in `precompiles/bank/bank.go` (lines 280-287) without any `BlockedAddr`/`IsSendEnabledCoins` check, whereas an equivalent `MsgSend` to the same blocked address via `bankMsgServer.Send` would be rejected with `ErrUnauthorized` per `sei-cosmos/x/bank/keeper/msg_server.go` lines 47-49.

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

**File:** precompiles/bank/bank.go (L251-288)
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
	accExists := p.accountKeeper.HasAccount(ctx, receiverSeiAddr)
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
