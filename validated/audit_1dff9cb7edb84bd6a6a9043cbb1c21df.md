Based on my investigation, this is the key finding.

### Title
IBC receive-side native ERC20 conversion failure duplicates value: recipient retains unconverted bank coins while sender is refunded on the source chain - ([File: x/erc20/keeper/ibc_callbacks.go])

### Summary
`Keeper.OnRecvPacket` in `x/erc20/keeper/ibc_callbacks.go` runs as post-processing middleware *after* the core ICS20 transfer app has already unescrowed/minted the received Cosmos coin into the recipient's bank balance [1](#0-0) . If the subsequent step of converting that bank coin into its native ERC20 representation fails (`MintingEnabled` or `ConvertCoinNativeERC20` returning an error), the function returns a `channeltypes.NewErrorAcknowledgement(err)` [2](#0-1) . Returning an error acknowledgement causes the *source* chain to treat the transfer as failed and refund the original sender (unescrow/re-mint there), while the bank coins already credited to the recipient on this (destination) chain are never reverted or clawed back — the test suite explicitly documents this as "SendEnabled=false will cause the conversion of bank tokens to erc20 tokens to fail, but not send them back to escrow" [3](#0-2) .

### Finding Description
This is the same bug class as the Notional H-8 report: a downstream, recipient/asset-controlled failure occurs *after* an irreversible state mutation has already been committed, and the calling code treats that failure as if nothing happened (a clean, atomic rollback), when in fact partial value has already moved.

In Notional, the vulnerable pattern was: excess cash refund via native ETH transfer could revert, blocking `repayAccountPrimeDebtAtSettlement` from completing atomically. Here, the analogous broken invariant is IBC 1:1 escrow accounting: for a `IsNativeERC20()` token pair, the ICS20 transfer module’s own `OnRecvPacket` (which runs before this middleware, per the code comment "MUST be executed transfer after the ICS20 OnRecvPacket") already performs the unescrow-and-mint of the Cosmos coin to the recipient's bank balance. This ERC20 middleware then attempts a *second*, separate action — converting that Cosmos coin into the native ERC20 token via `ConvertCoinNativeERC20` [4](#0-3)  — and if that fails for any reason (paused/blacklisted ERC20 transfer function, `SendEnabled=false`, self-destructed contract, insufficient balance/allowance shift, or any other bank-level restriction), it returns an `ErrorAcknowledgement`.

Per the IBC packet lifecycle, an `ErrorAcknowledgement` written on the destination chain, once relayed back to the source chain, triggers the source chain to refund the original sender (undoing its own escrow/burn). Nothing in this code path reverts the bank-coin credit that was already given to the recipient on the destination chain during the ICS20 transfer's own `OnRecvPacket`. The result: the sender is made whole on the source chain AND the recipient keeps the newly credited bank coin on the destination chain — the same underlying value now exists twice, breaking the escrow-backed 1:1 accounting invariant between the native ERC20 token, its Cosmos-coin representation, and the IBC escrow.

This differs from the (correctly handled) `OnAcknowledgementPacket`/`OnTimeoutPacket` failure path, where `ConvertCoinToERC20FromPacket` failing to convert simply leaves the *already-refunded* sender with a bank coin instead of an ERC20 token — a single instance of value, not a duplicate [5](#0-4) .

### Impact Explanation
This falls under the Critical "unauthorized minting/duplication of spendable user value across native balances, ERC20 representations, or IBC escrows" impact category. Each time the ERC20-conversion step in `OnRecvPacket` fails after the underlying transfer already credited the recipient, the total circulating value of the native-ERC20-backed asset increases by the packet amount without any corresponding new escrow deposit — an unbacked duplication of funds that breaks global supply/escrow invariants for that token pair.

### Likelihood Explanation
Triggering the underlying transfer-app credit followed by a downstream conversion failure does not require any privileged action; it only requires a condition that makes `ConvertCoinNativeERC20`/`MintingEnabled` fail after the bank credit has already occurred — the test suite uses `SetSendEnabled(...,false)` (governance-controlled) to reproduce it, but any bank `SendRestriction` hook, a paused/blacklisting ERC20 implementation, or a race with contract self-destruction could plausibly cause the same downstream failure. I was not able to fully confirm within the available index whether a *fully unprivileged* end user (without governance or module-level restriction changes) can reliably force `ConvertCoinNativeERC20` to fail for an arbitrary governance-registered native ERC20 pair — this would need further investigation of `MintingEnabled` and the specific `ERC20MinterBurnerDecimalsContract` used for native ERC20 pairs to identify an unprivileged failure trigger (e.g., a pausable/blacklistable variant, or an integer-overflow/edge condition in `transfer`).

### Recommendation
`OnRecvPacket`'s ERC20-conversion step for native ERC20 pairs should be executed atomically with (or immediately branch-reverted alongside) the underlying transfer app's mint/unescrow, so that a failed conversion causes the *entire* packet-receive state transition (including the bank credit) to roll back before an `ErrorAcknowledgement` is returned. Alternatively, on conversion failure, `OnRecvPacket` should return a *success* acknowledgement (leaving the recipient with the bank coin, as is already done for the ack/timeout paths) rather than an `ErrorAcknowledgement`, since the latter causes the source chain to also refund the sender, creating the duplication.

### Proof of Concept
1. Register a native ERC20 token pair via governance (`MsgRegisterERC20`) whose underlying contract can be made to fail `transfer` calls (e.g., via `SetSendEnabled(denom, false)`, as done in the existing test) [6](#0-5) .
2. Escrow/send tokens from chain A to chain B, then send them back to chain A via IBC (destination = chain A, where the ERC20 is native).
3. Before the packet is relayed, disable send/minting for that denom (or trigger any other condition causing `ConvertCoinNativeERC20` to fail).
4. `OnRecvPacket` runs: the underlying ICS20 transfer credits the recipient's bank balance with the received coin, then the ERC20 conversion attempt fails and an `ErrorAcknowledgement` is returned [7](#0-6) .
5. The error acknowledgement is relayed back to the source chain, which refunds the original sender's escrowed/burned tokens.
6. Result: the recipient on chain A holds the bank-coin credit from step 4, and the sender on chain B/source has been refunded — the same value now exists in two places, verifiable via `BankKeeper.GetBalance` on both accounts, matching the test's own confirmation that failed conversion “does not send them back to escrow” [3](#0-2) .

### Citations

**File:** x/erc20/keeper/ibc_callbacks.go (L23-34)
```go
// OnRecvPacket performs the ICS20 middleware receive callback for automatically
// converting an IBC Coin to their ERC20 representation.
// For the conversion to succeed, the IBC denomination must have previously been
// registered via governance. Note that the native staking denomination (e.g. "aatom"),
// is excluded from the conversion.
//
// CONTRACT: This middleware MUST be executed transfer after the ICS20 OnRecvPacket
// Return acknowledgement and continue with the next layer of the IBC middleware
// stack if:
// - ERC20s are disabled
// - Denomination is native staking token
// - The base denomination is not registered as ERC20
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

**File:** x/erc20/keeper/ibc_callbacks.go (L156-188)
```go
// OnAcknowledgementPacket responds to the success or failure of a packet
// acknowledgement written on the receiving chain. If the acknowledgement was a
// success then nothing occurs. If the acknowledgement failed, then the sender
// is refunded and then the IBC Coins are converted to ERC20.
// If the ERC20 conversion fails for whatever reason, such as an attempt to call
// a self-destructed ERC20 contract or an invalid function, OnAcknowledgementPacket
// still succeeds, but the user receives the corresponding bank token from the
// TokenPair instead. A user may then manually re-attempt the conversion.
func (k Keeper) OnAcknowledgementPacket(
	ctx sdk.Context, _ channeltypes.Packet,
	data transfertypes.FungibleTokenPacketData,
	ack channeltypes.Acknowledgement,
) error {
	switch ack.Response.(type) {
	case *channeltypes.Acknowledgement_Error:
		// convert the token from Cosmos Coin to its ERC20 representation
		return k.ConvertCoinToERC20FromPacket(ctx, data)
	default:
		// the acknowledgement succeeded on the receiving chain so nothing needs to
		// be executed and no error needs to be returned
		return nil
	}
}

// OnTimeoutPacket converts the IBC coin to ERC20 after refunding the sender
// since the original packet sent was never received and has been timed out.
// If the ERC20 conversion fails for whatever reason, such as an attempt to call
// a self-destructed ERC20 contract or an invalid function, OnTimeoutPacket still
// succeeds, but the user receives the corresponding bank token from the TokenPair
// instead. A user may then manually re-attempt the conversion.
func (k Keeper) OnTimeoutPacket(ctx sdk.Context, _ channeltypes.Packet, data transfertypes.FungibleTokenPacketData) error {
	return k.ConvertCoinToERC20FromPacket(ctx, data)
}
```

**File:** evmd/tests/ibc/ibc_middleware_test.go (L728-736)
```go
			if tc.withCallback {
				suite.evmChainA.NextBlock()

				// SendEnabled=false will cause the conversion of bank tokens to erc20 tokens to fail,
				// but not send them back to escrow
				evmApp.BankKeeper.SetSendEnabled(evmCtx, nativeErc20.Denom, false)
				isSendEnabled := evmApp.BankKeeper.IsSendEnabledDenom(evmCtx, nativeErc20.Denom)
				suite.Require().False(isSendEnabled)

```
