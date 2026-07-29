## Title
Silent failure in `ConvertCoinToERC20FromPacket` permanently traps user funds in the `erc20` module account when a native‑ERC20 token blocks the unescrow transfer — (File: `x/erc20/keeper/ibc_callbacks.go`)

### Summary
This is the Cosmos EVM analog of the OpenQ "malicious ERC20 bricks bounty" bug class. In OpenQ, a token that reverts on transfer permanently locks the whole bounty. In `x/erc20`, a native-ERC20 token pair (which can be **permissionlessly registered** by any user, see `RegisterERC20` in `x/erc20/keeper/msg_server.go:324-361` — permissionless when `PermissionlessRegistration` param is true) that reverts/blocks a `transfer` call (e.g. pausable/blacklistable ERC20, a very common real-world token feature) causes the IBC timeout/ack-error refund path to strand the user's Cosmos-coin balance in the `erc20` module account forever — neither burned nor returned to the user.

### Finding Description
When an IBC transfer of a "native ERC20" token pair fails (timeout or error acknowledgement), `x/erc20/keeper/ibc_callbacks.go` calls `ConvertCoinToERC20FromPacket`, which in the native-ERC20 case invokes `k.ConvertCoinNativeERC20`: [1](#0-0) 

`ConvertCoinNativeERC20` performs two side effects in sequence without atomic rollback protection:
1. `k.bankKeeper.SendCoinsFromAccountToModule(ctx, sender, types.ModuleName, coins)` — moves the user's bank coins into the `erc20` module account.
2. `k.evmKeeper.CallEVM(... "transfer" ...)` — unescrows the corresponding ERC20 tokens from the module address back to the user; only on success does it later `BurnCoins` the escrowed coins. [2](#0-1) 

If step 2 fails (the ERC20 `transfer` call reverts or returns `false` — e.g. the token is paused, or the recipient is blacklisted), `ConvertCoinNativeERC20` returns an error **before** reaching `BurnCoins`. Crucially, the caller `ConvertCoinToERC20FromPacket` explicitly **swallows** this error instead of propagating it: [3](#0-2) 

Because Cosmos SDK state writes to the `KVStore` are not automatically rolled back unless wrapped in an explicit `CacheContext`, and because the enclosing function returns `nil` (success) after catching the error, the `SendCoinsFromAccountToModule` mutation from step 1 is committed permanently. The result: the user's bank coin balance is debited into the `erc20` module account, the ERC20 side never receives the tokens back, and the coins are never burned — they are simply orphaned in the module account with no code path to reclaim them.

This whole flow is reachable through fully unprivileged, ordinary usage:
- Any user can permissionlessly register an ERC20 contract as a native token pair (`MsgRegisterERC20` with `PermissionlessRegistration=true`).
- Any holder of that token can convert to the Cosmos coin (`MsgConvertERC20`) and initiate an ordinary `MsgTransfer` over IBC.
- A timeout or error acknowledgement (a completely normal, frequent occurrence in IBC — e.g. destination chain has `SendEnabled=false`, channel congestion, or the ERC20 owner pauses/blacklists at any point before the refund executes) triggers `OnTimeoutPacket`/`OnAcknowledgementPacket` → `ConvertCoinToERC20FromPacket` → the vulnerable path above.

### Impact Explanation
This breaks the core escrow invariant that IBC-escrowed value must always be either fully delivered or fully refunded. Instead, user funds are irreversibly moved into the `erc20` module account with no burn and no user-facing balance — a permanent, unrecoverable loss of user funds triggered through ordinary transaction/IBC-callback flow, matching the "Critical permanent freezing/locking/theft of user funds" impact category. Unlike the original OpenQ finding (which required deliberately malicious tokens), any ERC20 with a legitimate pause/blacklist feature — extremely common in real tokens — can trigger this during normal operation, not just adversarial ones.

### Likelihood Explanation
High. Permissionless registration of native ERC20 token pairs is directly supported. Achieving an IBC timeout or error acknowledgement is trivial and common (e.g., relayer downtime, destination chain disables the denom, receiver rejects). Combined with any pausable/blacklistable ERC20 (or even a simple malicious contract that always reverts `transfer` to a specific address), an attacker registering their own contract can reliably reproduce fund loss for themselves or induce it for other holders of that token pair.

### Recommendation
- Make the escrow/unescrow sequence in `ConvertCoinNativeERC20` atomic: use `ctx.CacheContext()`/`CacheMultiStore` so that if the ERC20 `transfer` call fails, the `SendCoinsFromAccountToModule` mutation is rolled back together with it.
- Stop swallowing the `ConvertCoinNativeERC20` error in `ConvertCoinToERC20FromPacket`; if conversion fails, either propagate the error so the whole refund attempt reverts (leaving the coin in the user's own bank balance instead of the module account), or explicitly return the coins to the user via `SendCoinsFromModuleToAccount` before returning `nil`.
- Add a balance/supply invariant check (mirroring the existing `ErrBalanceInvariance` checks already used elsewhere in this file) asserting that the sum of user bank balance + module escrow + ERC20 balance is conserved after every `ConvertCoinNativeERC20` call, even on the error path.

### Proof of Concept
1. Deploy a pausable/blacklistable ERC20 contract (owner-controlled `pause()`/`blacklist()`), attacker holds full supply.
2. Attacker submits `MsgRegisterERC20` for this contract; permissionless registration succeeds since `PermissionlessRegistration=true` (default test behavior confirms this path, see `tests/integration/x/erc20/test_proposals.go` "ok - non-governance, permissionless true").
3. Attacker calls `MsgConvertERC20` to mint the corresponding Cosmos coin, then sends `MsgTransfer` over IBC to another chain.
4. Before the packet is acknowledged, attacker (or the token’s natural pause mechanism) pauses the contract, or causes the packet to time out/receive an error acknowledgement (e.g., by disabling `SendEnabled` on the destination, a technique already exercised in the existing test `evmd/tests/ibc/ibc_middleware_test.go` `TestOnRecvPacketNativeErc20`).
5. `OnTimeoutPacket`/`OnAcknowledgementPacket` → `ConvertCoinToERC20FromPacket` → `ConvertCoinNativeERC20` executes: `SendCoinsFromAccountToModule` moves the coin into the `erc20` module account, then the EVM `transfer` call reverts because the contract is paused.
6. The error is swallowed at `x/erc20/keeper/ibc_callbacks.go:237-253`; the function returns `nil`. Query the user's bank balance (zero for that denom) and the ERC20 balance (zero, transfer never happened) — the coin is now stuck in the `erc20` module account with no code path to retrieve it.

### Citations

**File:** x/erc20/keeper/ibc_callbacks.go (L236-253)
```go
		// Convert from Coin to ERC20
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

**File:** x/erc20/keeper/msg_server.go (L237-306)
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

	// Check unpackedRet execution
	var unpackedRet types.ERC20BoolResponse
	if len(res.Ret) == 0 {
		// if the token does not return a value, check for the transfer event in logs
		if err := validateTransferEventExists(res.Logs, contract); err != nil {
			return err
		}
	} else {
		if err := erc20.UnpackIntoInterface(&unpackedRet, "transfer", res.Ret); err != nil {
			return err
		}
		if !unpackedRet.Value {
			return sdkerrors.Wrap(errortypes.ErrLogic, "failed to execute unescrow tokens from user")
		}
	}

	// Check expected Receiver balance after transfer execution
	balanceTokenAfter := k.BalanceOf(ctx, erc20, contract, receiver)
	if balanceTokenAfter == nil {
		return sdkerrors.Wrap(types.ErrEVMCall, "failed to retrieve balance")
	}

	exp := big.NewInt(0).Add(balanceToken, amount.BigInt())

	if r := balanceTokenAfter.Cmp(exp); r != 0 {
		return sdkerrors.Wrapf(
			types.ErrBalanceInvariance,
			"invalid token balance - expected: %v, actual: %v", exp, balanceTokenAfter,
		)
	}

	// Burn escrowed Coins
	err = k.bankKeeper.BurnCoins(ctx, types.ModuleName, coins)
	if err != nil {
		return sdkerrors.Wrap(err, "failed to burn coins")
	}

	return nil
}
```
