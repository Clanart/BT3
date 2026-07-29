## Title
Escrow accounts bypass the module-account guard in `Keeper.OnRecvPacket`, allowing drain of another channel's IBC escrow into stranded ERC20 balance - (File: `x/erc20/keeper/ibc_callbacks.go`)

### Summary
`x/erc20/keeper/ibc_callbacks.go`'s `OnRecvPacket` only skips ERC20 conversion when the packet's receiver resolves to an account implementing `sdk.ModuleAccountI`. IBC-go escrow accounts (returned by `transfertypes.GetEscrowAddress`) are plain `BaseAccount`s, not `ModuleAccountI`, so the guard does not protect them. An attacker who legitimately holds IBC vouchers for the receiving chain's own native "native-ERC20" token can set `FungibleTokenPacketData.Receiver` to the escrow address of an *unrelated* channel. When received, the underlying ICS20 transfer unescrows real native coin to that escrow address, and the erc20 middleware then converts that balance to ERC20 by escrowing-and-burning it from the target escrow account and minting the ERC20 balance to the same (keyless) address — permanently draining bank-side backing for that other channel's outstanding vouchers while the "converted" value becomes unreachable ERC20 state that the transfer module's escrow-unlock logic (bank-only `SendCoins`) can never redeem.

### Finding Description
- `Keeper.OnRecvPacket` decodes `data.Receiver` and only bails out of conversion if `types.IsModuleAccount(receiverAcc)` is true: [1](#0-0) 
- `IsModuleAccount` is a pure type assertion to `sdk.ModuleAccountI`: [2](#0-1) 
- IBC-go escrow addresses (`transfertypes.GetEscrowAddress(port, channel)`), used pervasively throughout this repo's tests as ordinary bank-balance holders, are never registered as auth `ModuleAccount`s — they surface as plain `BaseAccount`s once funded, and this repo treats them exactly like other bank-holding accounts, e.g. checking their balance with `BankKeeper.GetBalance`: [3](#0-2) 
- For the "native ERC20" case, when `data.Denom` unwraps to a token pair that is native to the receiving chain, `Keeper.OnRecvPacket` calls `ConvertCoinNativeERC20`, using the resolved receiver as both `sender` (source of the escrow-burn) and `receiver` (ERC20 mint target): [4](#0-3) 
- `ConvertCoinNativeERC20` moves native coin out of that address into the `erc20` module and burns it, then mints the equivalent ERC20 balance to the same address via `CallEVM`: [5](#0-4) 

Because the ICS20 transfer layer (`im.Module.OnRecvPacket`, executed before the erc20 middleware) performs no validation on which address the received/unescrowed coins go to beyond a valid `sdk.AccAddress`, an attacker can freely set `Receiver` to any known escrow address, including one belonging to a *different* channel that currently backs real, outstanding vouchers issued to other users on another counterparty chain. The erc20 middleware then treats that escrow account like any ordinary user account: it burns the escrow's real bank balance and replaces it with an ERC20 balance at the same (keyless, uncontrollable) address. The IBC transfer module's own unescrow path (`bankKeeper.SendCoins` from the escrow account) has no way to recover or spend that ERC20 balance, so the backing for the drained channel's vouchers is irreversibly destroyed even though the vouchers on the counterparty chain remain valid and redeemable.

### Impact Explanation
This produces a critical, irreversible accounting break in IBC escrow backing: the native coin balance that guaranteed 1:1 redemption for a channel's outstanding vouchers is destroyed and converted into an ERC20 balance permanently stranded at a keyless escrow address, while the vouchers on the counterparty chain remain claimable. Later redemptions of that channel's vouchers will fail once escrow funds run out, permanently freezing/losing legitimate users' funds — matching the "Critical permanent freezing/theft of escrowed assets" and "irreversible accounting corruption ... IBC escrows" impact categories.

### Likelihood Explanation
The attacker only needs to be an ordinary holder of legitimately-issued IBC vouchers for the receiving chain's native-ERC20 token (obtained via a normal prior transfer) and craft a `MsgTransfer` on the counterparty chain with `Receiver` set to a computable, public escrow address (`transfertypes.GetEscrowAddress(port, channel)` is a deterministic, publicly derivable function). No validator, relayer, or governance privilege is required — an honest relayer will faithfully relay this otherwise well-formed packet.

### Recommendation
Extend the guard in `Keeper.OnRecvPacket` (and the analogous check in `ConvertCoinToERC20FromPacket`) beyond `types.IsModuleAccount` to also reject/no-op when the resolved receiver/sender address matches any IBC escrow address (e.g., by checking membership against `transfertypes.GetEscrowAddress` for known/active channels, or more robustly by maintaining an explicit set of protected/escrow addresses that must never be subject to automatic ERC20 conversion).

### Proof of Concept
1. Attacker (chain B) legitimately transfers native token X from chain A (an `x/erc20` native-ERC20-paired denom) to chain B, receiving IBC vouchers backed 1:1 by `escrowB1 = GetEscrowAddress(portA, channelA1)`.
2. Separately, chain A already has another channel `channelA2` (to chain C) with real users holding vouchers backed by `escrowB2 = GetEscrowAddress(portA, channelA2)` funded with token X.
3. Attacker sends the voucher back to chain A via `MsgTransfer` over `channelA1`, setting `Receiver = escrowB2.String()` instead of their own address.
4. On receipt, `im.Module.OnRecvPacket` unescrows real token-X coin from `escrowB1` and credits it to `escrowB2` (a normal bank transfer, no restriction).
5. `Keeper.OnRecvPacket` resolves `receiverAcc = escrowB2` (a `BaseAccount`), `IsModuleAccount` returns `false`, so `ConvertCoinNativeERC20` is called with `sender=escrowB2`: it burns that native balance from `escrowB2` and mints the equivalent ERC20 balance to `escrowB2`'s hex address.
6. `escrowB2`'s bank balance backing `channelA2`'s outstanding vouchers is now short by the transferred amount; users attempting to redeem `channelA2` vouchers via the transfer module's unescrow path (bank-only) will find insufficient escrow funds, while the "missing" value sits unusable as ERC20 balance at the keyless `escrowB2` address.

### Citations

**File:** x/erc20/keeper/ibc_callbacks.go (L63-70)
```go
	recipient := sdk.AccAddress(recipientBz)

	receiverAcc := k.accountKeeper.GetAccount(ctx, recipient)

	// return acknowledgement without conversion if receiver is a module account
	if types.IsModuleAccount(receiverAcc) {
		return ack
	}
```

**File:** x/erc20/keeper/ibc_callbacks.go (L118-139)
```go
	// Case 2. native ERC20 token
	case found && pair.IsNativeERC20():
		// Token pair is disabled -> return
		if !pair.Enabled {
			return ack
		}

		pair, err := k.MintingEnabled(ctx, recipient, coin.Denom)
		if err != nil {
			ctx.EventManager().EmitEvent(
				sdk.NewEvent("erc20_callback_failure",
					sdk.NewAttribute(types.TypeMsgConvertCoin, "mint_failure"),
					sdk.NewAttribute(types.AttributeKeyCosmosCoin, coin.Denom),
					sdk.NewAttribute(types.AttributeKeyReceiver, recipient.String()),
				),
			)
			return channeltypes.NewErrorAcknowledgement(err)
		}

		if err := k.ConvertCoinNativeERC20(ctx, pair, coin.Amount, common.BytesToAddress(recipient.Bytes()), recipient); err != nil {
			return channeltypes.NewErrorAcknowledgement(err)
		}
```

**File:** x/erc20/types/utils.go (L91-95)
```go
// IsModuleAccount returns true if the given account is a module account
func IsModuleAccount(acc sdk.AccountI) bool {
	_, isModuleAccount := acc.(sdk.ModuleAccountI)
	return isModuleAccount
}
```

**File:** evmd/tests/ibc/ics20_precompile_transfer_test.go (L206-220)
```go
			escrowAddress := transfertypes.GetEscrowAddress(packet.GetSourcePort(), packet.GetSourceChannel())

			// check that module account escrow address has locked the tokens
			chainAEscrowBalance := evmAppA.BankKeeper.GetBalance(
				suite.chainA.GetContext(),
				escrowAddress,
				sourceDenomToTransfer,
			)
			suite.Require().Equal(transferAmount.String(), chainAEscrowBalance.Amount.String())

			// check that voucher exists on chain B
			evmAppB := suite.chainB.App.(*evmd.EVMD)
			chainBDenom := transfertypes.NewDenom(originalCoin.Denom, traceAToB)
			chainBBalance := evmAppB.BankKeeper.GetBalance(
				suite.chainB.GetContext(),
```

**File:** x/erc20/keeper/msg_server.go (L256-303)
```go
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
```
