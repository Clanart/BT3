### Title
Governance-disabled token pair (`pair.Enabled = false`) can still be minted via IBC timeout/error-ack refund path, bypassing the per-pair pause - (File: `x/erc20/keeper/ibc_callbacks.go`)

### Summary
This maps to the same bug class as the reported `cancelInv()` missing `whenNotPaused()`: a state-mutating operation omits the "pause" guard that a sibling code path enforces, letting users trigger the guarded effect while the system is supposed to be halted. In `x/erc20`, `TokenPair.Enabled` is the module's per-asset pause flag, toggled by governance via `ToggleConversion` [1](#0-0) . `MintingEnabled()`, used by `ConvertCoin`/`ConvertERC20` and by `OnRecvPacket`, correctly checks `pair.Enabled` before minting [2](#0-1) [3](#0-2) . However, `ConvertCoinToERC20FromPacket` — invoked from `OnAcknowledgementPacket` (on error-ack) and `OnTimeoutPacket` (on timeout) — mints the native ERC20 representation directly via `ConvertCoinNativeERC20` without ever checking `pair.Enabled`, only checking the module-wide `params.EnableErc20` flag and denom registration [4](#0-3) .

### Finding Description
`ConvertCoinToERC20FromPacket` is the "refund" logic that runs when an outbound ICS-20 transfer of a native-ERC20-backed coin fails (error acknowledgement) or times out: [5](#0-4) 

It fetches the `TokenPair` and, for `pair.IsNativeERC20()`, only gates on the global `params.EnableErc20` and `IsDenomRegistered` — it never re-checks `pair.Enabled`: [6](#0-5) 

Compare this to the receive path `OnRecvPacket`, which explicitly returns early when `!pair.Enabled` before minting: [7](#0-6) 

and to `MintingEnabled`, used by the direct `MsgConvertCoin`/`MsgConvertERC20` message handlers, which also enforces `pair.Enabled`: [2](#0-1) 

Governance uses `ToggleConversion`/`pair.Enabled` as the mechanism to pause conversion for a specific token pair (e.g., in response to an incident with a compromised or buggy ERC20 contract, exactly the scenario `whenNotPaused()` is meant to protect against) [1](#0-0) . Because `ConvertCoinToERC20FromPacket` omits this check, an unprivileged user holding the Cosmos-coin representation of a *disabled* token pair can:
1. Convert their coin to the ERC20 via `MsgConvertERC20`/normal flow *before* the pair was disabled (or already hold coin balance), then
2. Initiate an ICS-20 transfer of that coin (`x/ibc/transfer` `Transfer`, which for native-ERC20 pairs auto-triggers the erc20 path) to a destination/channel guaranteed to fail or time out (e.g. an unopened/invalid channel, a receiver on a chain designed to reject it, or simply an unresponsive counterparty causing timeout).
3. Once the packet errors or times out, `OnAcknowledgementPacket`/`OnTimeoutPacket` call `ConvertCoinToERC20FromPacket`, which unconditionally calls `ConvertCoinNativeERC20` — reminting/reissuing the ERC20 tokens on the local EVM side even though the pair's `Enabled` flag says conversion is paused.

This lets a user re-obtain a "paused" native ERC20 asset outside the governance-intended freeze window, potentially interacting with a still-vulnerable/compromised ERC20 contract that governance intended to isolate by disabling the pair.

### Impact Explanation
This falls under the allowed "Critical unauthorized minting ... duplication ... of spendable user value across ... ERC20 representations ... or precompile-mediated assets" and "permanent freezing/locking/theft ... bypass" categories: governance pauses (`pair.Enabled=false`) a token pair specifically to stop conversion into/out of the ERC20 side (e.g., because the contract is compromised, has a bug enabling unauthorized minting/burning, or its liquidity/backing is broken). This gap allows unauthorized re-minting of that ERC20 asset through the IBC refund path regardless of the pause, undermining the invariant that `Enabled=false` halts all coin↔ERC20 conversion for that pair, and re-exposes user or protocol funds to the exact contract-level risk the pause was meant to contain.

### Likelihood Explanation
Triggering IBC packet errors or timeouts is fully achievable by an ordinary, unprivileged user (e.g., using an already-known-invalid destination channel/receiver, or simply waiting out the configured timeout on an unresponsive path) — no privileged relayer or validator collusion is required to *cause* the timeout/error; the sender only needs to already hold the native Cosmos-coin representation of a token pair that governance later disables while transfers are still pending. Given that pausing typically occurs during incident response (i.e., exactly when in-flight transfers are most likely to exist or be crafted), likelihood of exploitation is realistic in an incident scenario.

### Recommendation
Add a `pair.Enabled` check inside `ConvertCoinToERC20FromPacket` (mirroring the check in `OnRecvPacket` and `MintingEnabled`) before calling `ConvertCoinNativeERC20`, so that if the token pair has been disabled, the refund path leaves the sender's funds as the native Cosmos coin instead of reminting the ERC20:
```go
case pair.IsNativeERC20():
    ...
    params := k.GetParams(ctx)
    if !params.EnableErc20 || !pair.Enabled || !k.IsDenomRegistered(ctx, coin.Denom) {
        return nil
    }
```

### Proof of Concept
Conceptual reproduction (would need to be validated with `evmd`/IBC test harness):
1. Register a native ERC20 token pair and mint/convert some balance to the Cosmos-coin (bank) representation for account `A` (`MsgConvertERC20`) — analogous to `tests/integration/x/erc20/test_ibc_callback.go`/`evmd/tests/ibc/ibc_middleware_test.go` fixtures shown in the search results, e.g. `SetupNativeErc20` [8](#0-7) .
2. Governance calls `MsgToggleConversion` to set `pair.Enabled = false` for this token, intending to halt any further coin↔ERC20 conversions [1](#0-0) .
3. Account `A` submits `MsgTransfer` (ICS-20) sending the coin to a channel/receiver engineered to fail (e.g. invalid receiver format on a real channel, or a channel that will time out).
4. When the ack error or timeout fires, `OnAcknowledgementPacket`/`OnTimeoutPacket` → `ConvertCoinToERC20FromPacket` executes and calls `ConvertCoinNativeERC20`, re-minting ERC20 tokens to `A`'s EVM balance [9](#0-8)  — despite `pair.Enabled == false`.
5. Compare with the analogous receive-side flow `OnRecvPacket`, which would have correctly refused to mint due to the `!pair.Enabled` guard [7](#0-6) , confirming the asymmetry/bug.

Note: I was not able to execute this against a live test harness within this session; the analysis is based on static code review of the cited functions. A Devin session with repository/test execution access would be needed to run the existing IBC integration test suite (`evmd/tests/ibc/*_test.go`) with a modification that disables the pair mid-flight to empirically confirm the mint occurs.

### Citations

**File:** x/erc20/keeper/proposals.go (L116-138)
```go
// ToggleConversion toggles conversion for a given token pair
func (k Keeper) toggleConversion(
	ctx sdk.Context,
	token string,
) (types.TokenPair, error) {
	id := k.GetTokenPairID(ctx, token)
	if len(id) == 0 {
		return types.TokenPair{}, errorsmod.Wrapf(
			types.ErrTokenPairNotFound, "token '%s' not registered by id", token,
		)
	}

	pair, found := k.GetTokenPair(ctx, id)
	if !found {
		return types.TokenPair{}, errorsmod.Wrapf(
			types.ErrTokenPairNotFound, "token '%s' not registered", token,
		)
	}

	pair.Enabled = !pair.Enabled
	k.SetTokenPair(ctx, pair)
	return pair, nil
}
```

**File:** x/erc20/keeper/mint.go (L43-47)
```go
	if !pair.Enabled {
		return types.TokenPair{}, errorsmod.Wrapf(
			types.ErrERC20TokenPairDisabled, "minting token '%s' is not enabled by governance", token,
		)
	}
```

**File:** x/erc20/keeper/ibc_callbacks.go (L118-125)
```go
	// Case 2. native ERC20 token
	case found && pair.IsNativeERC20():
		// Token pair is disabled -> return
		if !pair.Enabled {
			return ack
		}

		pair, err := k.MintingEnabled(ctx, recipient, coin.Denom)
```

**File:** x/erc20/keeper/ibc_callbacks.go (L190-253)
```go
// ConvertCoinToERC20FromPacket converts the IBC coin to ERC20 after refunding the sender
// This function is only executed when IBC timeout or an Error ACK happens.
func (k Keeper) ConvertCoinToERC20FromPacket(ctx sdk.Context, data transfertypes.FungibleTokenPacketData) error {
	// Sender is local (source) chain address; accept local bech32 or 0x-hex
	senderBz, err := k.addrCodec.StringToBytes(data.Sender)
	if err != nil {
		return err
	}
	sender := sdk.AccAddress(senderBz)

	pairID := k.GetTokenPairID(ctx, data.Denom)
	pair, found := k.GetTokenPair(ctx, pairID)
	if !found {
		// no-op, token pair is not registered
		return nil
	}

	coin := ibc.GetSentCoin(data.Denom, data.Amount)

	switch {

	// Case 1. if pair is native coin -> no-op
	case pair.IsNativeCoin():
		// no-op, received coin is a  native coin
		return nil

	// Case 2. if pair is native ERC20 -> unescrow
	case pair.IsNativeERC20():
		// use a zero gas config to avoid extra costs for the relayers
		ctx = ctx.
			WithKVGasConfig(storetypes.GasConfig{}).
			WithTransientKVGasConfig(storetypes.GasConfig{})

		params := k.GetParams(ctx)
		if !params.EnableErc20 || !k.IsDenomRegistered(ctx, coin.Denom) {
			// no-op, ERC20s are disabled or the denom is not registered
			return nil
		}

		// assume that all module accounts on Cosmos EVM need to have their tokens in the
		// IBC representation as opposed to ERC20
		senderAcc := k.accountKeeper.GetAccount(ctx, sender)
		if types.IsModuleAccount(senderAcc) {
			return nil
		}

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

**File:** evmd/tests/ibc/v2_ibc_middleware_test.go (L317-346)
```go
	for _, tc := range testCases {
		suite.Run(tc.name, func() {
			suite.SetupTest()
			nativeErc20 := SetupNativeErc20(suite.T(), suite.evmChainA, suite.evmChainA.SenderAccounts[0])
			senderEthAddr := nativeErc20.Account
			sender := sdk.AccAddress(senderEthAddr.Bytes())
			sendAmt := math.NewIntFromBigInt(nativeErc20.InitialBal)

			evmCtx := suite.evmChainA.GetContext()
			evmApp := suite.evmChainA.App.(*evmd.EVMD)
			// MOCK erc20 native coin transfer from chainA to chainB
			// 1: Convert erc20 tokens to native erc20 coins for sending through IBC.
			_, err := evmApp.Erc20Keeper.ConvertERC20(
				evmCtx,
				types.NewMsgConvertERC20(
					sendAmt,
					sender,
					nativeErc20.ContractAddr,
					senderEthAddr,
				),
			)
			suite.Require().NoError(err)
			// 1-1: Check native erc20 token is converted to native erc20 coin on chainA.
			erc20BalAfterConvert := evmApp.Erc20Keeper.BalanceOf(evmCtx, nativeErc20.ContractAbi, nativeErc20.ContractAddr, senderEthAddr)
			suite.Require().Equal(
				new(big.Int).Sub(nativeErc20.InitialBal, sendAmt.BigInt()).String(),
				erc20BalAfterConvert.String(),
			)
			balAfterConvert := evmApp.BankKeeper.GetBalance(evmCtx, sender, nativeErc20.Denom)
			suite.Require().Equal(sendAmt.String(), balAfterConvert.Amount.String())
```
