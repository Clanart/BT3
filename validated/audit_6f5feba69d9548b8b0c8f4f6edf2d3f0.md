### Title
Permanent loss of user funds via non-atomic ERC20 unescrow in IBC conversion path when native ERC20 `transfer` reverts - (File: x/erc20/keeper/msg_server.go, x/erc20/keeper/ibc_callbacks.go)

### Summary
`ConvertCoinNativeERC20` in `x/erc20/keeper/msg_server.go` performs a multi-step conversion (escrow Cosmos coin → unescrow ERC20 token via an EVM call → burn escrowed coin) without wrapping the steps in a cached/branched context. When this function is invoked from the IBC receive/acknowledgement/timeout callback paths in `x/erc20/keeper/ibc_callbacks.go` and the EVM `transfer` call reverts (e.g. because the registered native ERC20 contract implements a blacklist/deny-list, similar to the USDC-style pattern in the referenced Teller report), the function returns an error, but the callback only converts this into an `ErrorAcknowledgement`/logged failure instead of a Go `error` that would cause the whole `Msg` to be rolled back. The coins already moved to the module account in the "escrow" step are never returned or burned, permanently freezing the user's funds.

### Finding Description
`ConvertCoinNativeERC20` [1](#0-0)  executes, in order:
1. `SendCoinsFromAccountToModule` — moves the Cosmos coin from the user/sender into the `erc20` module account.
2. `CallEVM(... "transfer" ...)` — calls the registered ERC20 contract's `transfer` function to move tokens from the module's EVM balance to the receiver ("unescrow").
3. `BurnCoins` — burns the previously escrowed coins, completing the conversion.

There is no cached context (`ctx.CacheContext()`) wrapping these three steps, so any state mutated in step 1 is written directly to the shared context store used by the surrounding IBC packet handler.

Since native ERC20 token pairs can be registered permissionlessly (`RegisterERC20` when `PermissionlessRegistration` is enabled) [2](#0-1) , an attacker can register a malicious ERC20 contract whose `transfer()` reverts under attacker-chosen conditions (e.g., when called by the module's escrow address, or when the destination is a targeted victim), mimicking a blacklist.

This function is invoked from two IBC callback paths that treat its error as "recoverable" rather than fatal:
- `OnRecvPacket`, where a failure is simply converted into an `ErrorAcknowledgement` and returned: [3](#0-2) 
- `ConvertCoinToERC20FromPacket` (used by both `OnAcknowledgementPacket` and `OnTimeoutPacket`), where a failure is only logged via an event and the function returns `nil`: [4](#0-3) 

Because IBC packet processing (`MsgRecvPacket`, `MsgAcknowledgement`, `MsgTimeout`) treats a returned `Acknowledgement` object (even an "error" one) or a `nil` error as a *successful* execution of the SDK message, the state changes accumulated before the failure are committed to the chain. The escrow step (`SendCoinsFromAccountToModule`) is therefore never undone, and because the function returned before reaching the `BurnCoins` step, the coins are not burned either — they sit permanently in the `erc20` module account, unassociated with any account balance, effectively lost.

This directly parallels the reported bug class: a value-transfer step that can be blocked by a hostile counterparty (blacklist-capable ERC20 contract instead of a blacklist-capable lender), combined with earlier, already-committed state changes (escrow) that are not protected by atomicity, resulting in guaranteed, irreversible loss of user funds.

### Impact Explanation
This meets the "Critical permanent freezing, locking, theft, or unauthorized extraction of user funds ... token-pair-backed balances" impact bar. Any user attempting to receive an IBC transfer of a native-ERC20-backed coin (`OnRecvPacket`), or attempting an IBC send whose packet fails/times out and gets refunded and reconverted (`ConvertCoinToERC20FromPacket`), can have their coin balance silently and permanently trapped in the `x/erc20` module account if the underlying ERC20 contract's `transfer` reverts during the unescrow step. Because native ERC20 registration is permissionless, an attacker fully controls this trigger condition and can grief arbitrary IBC transfers of their own malicious token pair, or worse, target specific victim addresses.

### Likelihood Explanation
Likelihood is high given: (1) `RegisterERC20` supports permissionless registration of attacker-controlled ERC20 contracts, (2) IBC transfers of native-ERC20-backed tokens routinely traverse `OnRecvPacket`/`ConvertCoinToERC20FromPacket`, and (3) no validation is performed before the escrow step to ensure the unescrow leg will succeed (e.g., no pre-flight `transfer`/allowance check, no atomic rollback wrapper).

### Recommendation
Wrap the escrow → unescrow → burn sequence in `ConvertCoinNativeERC20` in a `ctx.CacheContext()` and only call `writeFn()` after all three steps succeed, so that any failure in the EVM `transfer` call leaves the original balances untouched. Additionally, treat unescrow failures during IBC receive/ack/timeout callbacks as fatal (propagate a Go `error`) rather than silently returning `nil`/an error acknowledgement after partial state mutation, or add compensating logic to fully revert the escrow when unescrow fails.

### Proof of Concept
1. Deploy a malicious ERC20 contract whose `transfer(to, amount)` reverts when `msg.sender == types.ModuleAddress` (the erc20 module's EVM address) — i.e., it always allows normal transfers but blocks the module's escrow-release transfer.
2. Register this contract permissionlessly via `MsgRegisterERC20` as a token pair (assuming `PermissionlessRegistration` is enabled), or have governance register it if disabled.
3. Have a victim/attacker-controlled account send this token via IBC to another chain and back, or receive an IBC packet carrying this token pair's IBC voucher denom targeting the local chain.
4. On `OnRecvPacket`, the middleware calls `ConvertCoinNativeERC20`: the coin is escrowed into the module account (`SendCoinsFromAccountToModule` succeeds), then `CallEVM(... "transfer" ...)` reverts due to the malicious contract logic.
5. `ConvertCoinNativeERC20` returns an error; `OnRecvPacket` converts this into `channeltypes.NewErrorAcknowledgement(err)` and returns it — the enclosing `MsgRecvPacket` handling still succeeds (a Go `nil` error is returned to baseapp), so the escrow transfer from step 4 is committed.
6. Verify on-chain that the module account's coin balance increased by `amount` while the recipient received neither the coin (it was escrowed away) nor the ERC20 token (transfer reverted) — the coins are now permanently stranded in the module account with no code path to reclaim them.

### Citations

**File:** x/erc20/keeper/msg_server.go (L237-266)
```go
func (k Keeper) ConvertCoinNativeERC20(
	ctx sdk.Context,
	pair types.TokenPair,
	amount math.Int,
	receiver common.Address,
	sender sdk.AccAddress,
) error {
	if !amount.IsPositive() {
		return sdkerrors.Wrap(types.ErrNegativeToken, "converted coin amount must be positive")
	}

	erc20 := contracts.ERC20MinterBurnerDecimalsContract.ABI
	contract := pair.GetERC20Contract()

	balanceToken := k.BalanceOf(ctx, erc20, contract, receiver)
	if balanceToken == nil {
		return sdkerrors.Wrap(types.ErrEVMCall, "failed to retrieve balance")
	}

	// Escrow Coins on module account
	coins := sdk.Coins{{Denom: pair.Denom, Amount: amount}}
	if err := k.bankKeeper.SendCoinsFromAccountToModule(ctx, sender, types.ModuleName, coins); err != nil {
		return sdkerrors.Wrap(err, "failed to escrow coins")
	}

	// Unescrow Tokens and send to receiver
	res, err := k.evmKeeper.CallEVM(ctx, erc20, types.ModuleAddress, contract, true, nil, "transfer", receiver, amount.BigInt())
	if err != nil {
		return err
	}
```

**File:** x/erc20/keeper/msg_server.go (L326-345)
```go
func (k *Keeper) RegisterERC20(goCtx context.Context, req *types.MsgRegisterERC20) (*types.MsgRegisterERC20Response, error) {
	ctx := sdk.UnwrapSDKContext(goCtx)

	params := k.GetParams(ctx)

	if !params.PermissionlessRegistration {
		if err := k.validateAuthority(req.Signer); err != nil {
			return nil, err
		}
	}

	// Check if the conversion is globally enabled
	if !k.IsERC20Enabled(ctx) {
		return nil, types.ErrERC20Disabled.Wrap("registration is currently disabled by governance")
	}

	for _, addr := range req.Erc20Addresses {
		if !common.IsHexAddress(addr) {
			return nil, errortypes.ErrInvalidAddress.Wrapf("invalid ERC20 contract address: %s", addr)
		}
```

**File:** x/erc20/keeper/ibc_callbacks.go (L137-139)
```go
		if err := k.ConvertCoinNativeERC20(ctx, pair, coin.Amount, common.BytesToAddress(recipient.Bytes()), recipient); err != nil {
			return channeltypes.NewErrorAcknowledgement(err)
		}
```

**File:** x/erc20/keeper/ibc_callbacks.go (L237-253)
```go
		if err := k.ConvertCoinNativeERC20(ctx, pair, coin.Amount, common.BytesToAddress(sender), sender); err != nil {
			// We want to record only the failed attempt to reconvert the coins during IBC.
			defer func() {
				telemetry.IncrCounter(1, types.ModuleName, "ibc", "error", "total")
			}()
			ctx.EventManager().EmitEvents(
				sdk.Events{
					sdk.NewEvent(
						types.EventTypeFailedConvertERC20,
						sdk.NewAttribute(types.AttributeCoinSourceChannel, pair.Denom),
						sdk.NewAttribute(types.AttributeKeyERC20Token, pair.Erc20Address),
						sdk.NewAttribute("error", err.Error()),
					),
				},
			)
			return nil
		}
```
