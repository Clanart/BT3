This confirms the emergency-pause bypass analog. The `SendEnabled` flag is sei-chain's equivalent of the `BaseGauge` emergency pause: setting a denom's `SendEnabled` param to `false` is meant to halt transfers of that coin (e.g., in response to an exploit). This restriction is enforced only inside the bank `msgServer.Send`/`MultiSend` handlers via `k.IsSendEnabledCoins(ctx, ...)` before calling `SendCoins`. [1](#0-0) 

The underlying `BaseSendKeeper.SendCoins`/`setBalance` functions do not perform this check themselves — `IsSendEnabledCoins` is a caller-side guard, not enforced inside `SendCoins`. [2](#0-1) 

Several legacy EVM bank-precompile versions (`v552`, `v555`, `v580`) call `p.bankKeeper.SendCoins(...)` **directly**, bypassing the `IsSendEnabledCoins` gate entirely — mirroring exactly the reported bug class (pause/guard exists but not applied on a reachable execution path): [3](#0-2) [4](#0-3) 

Newer precompile versions (`v601` onward) were changed to route through `p.bankMsgServer.Send(...)`, which does enforce `IsSendEnabledCoins`: [5](#0-4) 

I was not able to fully confirm, within the tool budget, whether the currently active (non-legacy) `precompiles/bank/bank.go` production version still uses the vulnerable direct `SendCoins` call path or the safer `bankMsgServer.Send` path — the file listing showed `precompiles/bank/bank.go` exists alongside a `legacy/` directory of historical precompile versions, but I did not get to read its `send` implementation before running out of iterations. This distinction is decisive for whether the vulnerability is currently live in production.

### Title
Bank precompile bypasses `SendEnabled` (send-pause) enforcement via direct `SendCoins` call - (File: `precompiles/bank/legacy/v552/bank.go`, `precompiles/bank/legacy/v555/bank.go`, `precompiles/bank/legacy/v580/bank.go`)

### Summary
Sei-chain's bank module exposes a `SendEnabled` param per denom, which node operators/governance can flip to `false` as an emergency circuit-breaker to halt transfers of a specific token (e.g., a compromised tokenfactory denom or pointer-mapped asset). This check, `IsSendEnabledCoins`, is enforced in the standard Cosmos `MsgSend`/`MsgMultiSend` handlers, but is **not** enforced inside `BaseSendKeeper.SendCoins` itself. Legacy versions of the EVM bank precompile's `send` function call `bankKeeper.SendCoins` directly instead of going through the msg server, completely bypassing the `SendEnabled` pause/guard.

### Finding Description
`IsSendEnabledCoins` is only invoked as a caller-side check in `msgServer.Send`/`MultiSend`, not inside `SendCoins`/`setBalance`. The EVM bank precompile's `send` method (present in `precompiles/bank/legacy/v552`, `v555`, `v580`) calls `p.bankKeeper.SendCoins(ctx, senderSeiAddr, receiverSeiAddr, ...)` directly after resolving the ERC20 native pointer caller, with no `IsSendEnabledCoins` gate anywhere in that path. This is structurally identical to the reported bug class: an emergency-stop mechanism exists in the codebase (`SendEnabled=false`) but a reachable execution path (EVM contract calling the bank precompile) omits the modifier/check that would enforce it.

### Impact Explanation
If an admin disables `SendEnabled` for a denom in response to an active exploit or compromised token, an attacker who reaches the vulnerable precompile version can continue moving/transferring that denom's balances through EVM contract calls, defeating the emergency halt and enabling continued fund loss during exactly the window the pause is meant to protect.

### Likelihood Explanation
Exploitability requires (a) the vulnerable legacy precompile version being the one actually wired into the active precompile registry for the running chain version, and (b) an admin actively using `SendEnabled=false` as an incident-response tool. Since sei-chain versions its precompiles and the newer versions (`v601`+) already route through `bankMsgServer.Send` (which enforces the check), the practical exposure depends entirely on which precompile version is currently registered as the live implementation for a given chain upgrade height.

### Recommendation
Ensure all bank precompile `send` implementations route through `bankKeeper`'s enabled-checking entrypoints (i.e., call `IsSendEnabledCoins` before `SendCoins`, or always use `bankMsgServer.Send`) consistently across every precompile version, and add a regression test asserting that disabling `SendEnabled` for a denom blocks transfers made via the EVM bank precompile.

### Proof of Concept
1. Governance/admin sets `SendEnabled=false` for a target denom via bank params, intending to halt its transfers. [2](#0-1) 
2. An attacker calls the EVM bank precompile's `send(...)` method (on a vulnerable legacy version) from a contract, targeting that denom. [6](#0-5) 
3. Because this code path calls `p.bankKeeper.SendCoins` directly without checking `IsSendEnabledCoins`, the transfer succeeds despite the emergency pause, while the same transfer via a native `MsgSend` would be rejected. [7](#0-6)

### Citations

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

**File:** sei-cosmos/x/bank/keeper/send.go (L355-369)
```go
// IsSendEnabledCoins checks the coins provide and returns an ErrSendDisabled if
// any of the coins are not configured for sending.  Returns nil if sending is enabled
// for all provided coin
func (k BaseSendKeeper) IsSendEnabledCoins(ctx sdk.Context, coins ...sdk.Coin) error {
	for _, coin := range coins {
		if !k.IsSendEnabledCoin(ctx, coin) {
			return sdkerrors.Wrapf(types.ErrSendDisabled, "%s transfers are currently disabled", coin.Denom)
		}
	}
	return nil
}

// IsSendEnabledCoin returns the current SendEnabled status of the provided coin's denom
func (k BaseSendKeeper) IsSendEnabledCoin(ctx sdk.Context, coin sdk.Coin) bool {
	return k.GetParams(ctx).SendEnabledDenom(coin.Denom)
```

**File:** precompiles/bank/legacy/v552/bank.go (L167-203)
```go
func (p Precompile) send(ctx sdk.Context, caller common.Address, method *abi.Method, args []interface{}, value *big.Int, readOnly bool) ([]byte, error) {
	if readOnly {
		return nil, errors.New("cannot call send from staticcall")
	}
	if err := pcommon.ValidateNonPayable(value); err != nil {
		return nil, err
	}

	if err := pcommon.ValidateArgsLength(args, 4); err != nil {
		return nil, err
	}
	denom := args[2].(string)
	if denom == "" {
		return nil, errors.New("invalid denom")
	}
	pointer, _, exists := p.evmKeeper.GetERC20NativePointer(ctx, denom)
	if !exists || pointer.Cmp(caller) != 0 {
		return nil, fmt.Errorf("only pointer %s can send %s but got %s", pointer.Hex(), denom, caller.Hex())
	}
	amount := args[3].(*big.Int)
	if amount.Cmp(utils.Big0) == 0 {
		// short circuit
		return method.Outputs.Pack(true)
	}
	// TODO: it's possible to extend evm module's balance to handle non-usei tokens as well
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

**File:** precompiles/bank/legacy/v580/bank.go (L150-156)
```go
	if err != nil {
		return nil, err
	}

	if err := p.bankKeeper.SendCoins(ctx, senderSeiAddr, receiverSeiAddr, sdk.NewCoins(sdk.NewCoin(denom, sdk.NewIntFromBigInt(amount)))); err != nil {
		return nil, err
	}
```

**File:** precompiles/bank/legacy/v601/bank.go (L162-175)
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
