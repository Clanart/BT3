### Title
Front-Runnable Deterministic ERC20 Precompile Address Enables Griefing of IBC Token Registration and Ack-Based Fund Duplication - (File: `x/erc20/keeper/ibc_callbacks.go`, `x/erc20/keeper/token_pairs.go`, `x/erc20/types/token_pair.go`)

### Summary
The external report describes a griefing pattern: a state-mutating "enable" action is keyed by a value (`sessionKey`) that is predictable/known ahead of time and has no binding to the legitimate caller, letting an attacker front-run the real owner and permanently occupy that key. The Cosmos EVM analog is the automatic ERC20-extension registration performed in the ICS20 `OnRecvPacket` middleware: the ERC20 precompile address for a newly received IBC voucher denom is deterministically derived from public data (`NewTokenPairSTRv2(denom)` → `GetERC20Contract()`), and registration fails permanently if any code already exists at that address [1](#0-0) . An attacker can precompute this address and occupy it before the legitimate IBC transfer is relayed, causing the ERC20 registration step to fail on receipt.

### Finding Description
When a new IBC voucher denom is received for the first time, `Keeper.OnRecvPacket` in `x/erc20/keeper/ibc_callbacks.go` looks up the token pair; if not found and the denom is IBC-prefixed, it calls `RegisterERC20Extension(ctx, coin.Denom)`: [2](#0-1) 

`RegisterERC20Extension` calls `CreateNewTokenPair`, which derives a token pair (including the ERC20 contract address) purely from the `denom` string via `types.NewTokenPairSTRv2(denom)`, and errors out if an account already exists at the derived address with a code hash set: [1](#0-0) [3](#0-2) 

Because the derivation depends only on the IBC denom (itself derivable from public information: source channel/port and base denom, as demonstrated in the test suite computing the same address independently of any actual registration event) [4](#0-3) , the resulting ERC-20 contract address for any given denom/channel path is fully predictable before the first transfer for that denom is ever relayed — exactly like the predictable `sessionKey` in the source report. There is no check binding "who may occupy this address" to "who is entitled to register this denom's precompile," mirroring the missing `sessionKey`→SCW binding in the original report.

An attacker who front-runs this by placing EVM code (setting a code hash) at the deterministically-derived address before the legitimate IBC packet is processed will cause `CreateNewTokenPair` to permanently fail with `ErrTokenPairAlreadyExists` for that denom, and `OnRecvPacket` returns an error acknowledgement: [5](#0-4) 

Critically, this failure occurs *after* the base ICS20 `OnRecvPacket` (which mints the voucher coin to the receiver) has already executed in the same middleware chain: [6](#0-5) 

Since the base transfer callback's state changes (minting the voucher coin) are not shown to be wrapped in a discard-on-error cache context distinct from the erc20 middleware's own error return, an error acknowledgement returned solely due to the ERC20-registration failure will be relayed back to the sending chain as a packet failure. Per standard ICS20 semantics, the sending chain will process this as a failed transfer and refund/unlock the escrowed tokens to the original sender, while the receiving chain has already credited the voucher coin to the receiver — a duplication of spendable value across the two chains' state.

### Impact Explanation
This falls under the Critical impact category of "unauthorized ... duplication ... of spendable user value ... across IBC escrows." An unprivileged attacker can:
1. Predict the ERC20 precompile address for a not-yet-used IBC denom path (public information).
2. Occupy that address with code before the legitimate first transfer for that denom is processed.
3. Force every subsequent `OnRecvPacket` for that denom to fail at the ERC20-registration step, producing an error acknowledgement despite the voucher having already been minted to the receiver.
4. Trigger a refund on the source chain for a transfer whose destination-side effects were never reverted, creating duplicated value across both chains.

Separately, even without the duplication concern, this is at minimum a permanent, attacker-triggerable denial of the "automatic ERC20 conversion" feature for a given IBC denom — directly analogous to the "session key already exists, wallet permanently blocked from enabling it" griefing in the source report.

### Likelihood Explanation
The precompile address is derived deterministically from public data (denom/channel/port), requiring no privileged information. The attacker only needs to submit an ordinary EVM transaction to occupy the target address before the victim's IBC transfer packet is relayed and processed — a straightforward front-running opportunity similar to the mempool-observation attack described in the source report. The main uncertainty (not fully confirmed by the available code snippets) is whether the underlying ICS20 mint that occurs in `im.Module.OnRecvPacket` is committed unconditionally regardless of the final acknowledgement written by the erc20 middleware, or whether IBC-go's packet-handling wraps the entire callback chain (including this middleware) in a branched store that is only committed on overall ack success. This distinction determines whether the "duplication" impact materializes versus a lesser "permanent registration DoS" impact; I could not verify this with certainty from the retrieved code and it would need confirmation via the ibc-go core packet-receiving code path and/or a live test.

### Recommendation
- Ensure the base ICS20 mint and the erc20 middleware's registration/conversion step are executed within a single atomic branched context that is only committed if the final acknowledgement is a success (i.e., roll back the voucher mint if `RegisterERC20Extension` fails), preventing any possibility of duplicated value between chains.
- Alternatively/additionally, decouple the "denom received" path from "ERC20 auto-registration path" so that a failure to register the ERC20 extension does not cause the overall packet acknowledgement to fail — the user should simply receive the bank-denominated voucher (as already happens when `EnableErc20` is disabled), rather than causing an error ack that triggers a refund of an already-completed transfer.
- Add a check that the address returned by `NewTokenPairSTRv2` has no conflicting code deployed by an unrelated party before treating a pre-existing code hash as "token already exists," or provide a mechanism to permission/reserve the deterministic addresses so they cannot be squatted by ordinary EVM transactions.

### Proof of Concept
1. Attacker identifies (or predicts) an IBC channel/port and base denom that is about to be used for the first time to transfer tokens into the EVM chain (e.g., by observing a pending relay in the mempool or by proactively front-running a new channel's first transfer).
2. Attacker computes `ibcDenom := transfertypes.NewDenom(baseDenom, hop).IBCDenom()` and `addr := types.NewTokenPairSTRv2(ibcDenom).GetERC20Contract()` — the exact same deterministic derivation used in `RegisterERC20Extension`/`CreateNewTokenPair` [7](#0-6) .
3. Attacker submits an EVM transaction that deploys/sets code at `addr` (e.g., via `CREATE`/`CREATE2` or a `SetAccount`-style call reachable from user-controlled EVM execution) so that `k.evmKeeper.GetAccount(ctx, addr).HasCodeHash()` becomes true.
4. When the legitimate IBC transfer packet for that denom is relayed and `OnRecvPacket` runs, `RegisterERC20Extension` → `CreateNewTokenPair` fails with `ErrTokenPairAlreadyExists` [8](#0-7) , and `OnRecvPacket` returns an error acknowledgement [5](#0-4) , even though the underlying voucher mint from the base transfer callback already executed [6](#0-5) .
5. The source chain, upon receiving the error acknowledgement, refunds the sender, while the receiver on the destination chain retains the minted voucher — producing duplicated spendable value across chains (pending confirmation of the exact atomicity guarantees in the surrounding ibc-go packet-processing code, which was not fully available in the indexed context).

### Citations

**File:** x/erc20/keeper/token_pairs.go (L17-31)
```go
func (k Keeper) CreateNewTokenPair(ctx sdk.Context, denom string) (types.TokenPair, error) {
	pair, err := types.NewTokenPairSTRv2(denom)
	if err != nil {
		return types.TokenPair{}, err
	}
	account := k.evmKeeper.GetAccount(ctx, pair.GetERC20Contract())
	if account != nil && account.HasCodeHash() {
		return types.TokenPair{}, errorsmod.Wrapf(types.ErrTokenPairAlreadyExists, "token already exists for token %s", pair.Erc20Address)
	}
	err = k.SetToken(ctx, pair)
	if err != nil {
		return types.TokenPair{}, err
	}
	return pair, nil
}
```

**File:** x/erc20/keeper/ibc_callbacks.go (L95-116)
```go
	pairID := k.GetTokenPairID(ctx, coin.Denom)
	pair, found := k.GetTokenPair(ctx, pairID)
	switch {
	// Case 1. token pair is not registered and is an IBC Coin
	// by checking the prefix we ensure that only coins not native from this chain are evaluated.
	case !found && strings.HasPrefix(coin.Denom, "ibc/"):
		tokenPair, err := k.RegisterERC20Extension(ctx, coin.Denom)
		if err != nil {
			return channeltypes.NewErrorAcknowledgement(err)
		}

		ctx.EventManager().EmitEvents(
			sdk.Events{
				sdk.NewEvent(
					types.EventTypeRegisterERC20Extension,
					sdk.NewAttribute(types.AttributeCoinSourceChannel, packet.SourceChannel),
					sdk.NewAttribute(types.AttributeKeyERC20Token, tokenPair.Erc20Address),
					sdk.NewAttribute(types.AttributeKeyCosmosCoin, tokenPair.Denom),
				),
			},
		)
		return ack
```

**File:** x/erc20/keeper/dynamic_precompiles.go (L13-31)
```go
// RegisterERC20Extension creates and adds an ERC20 precompile interface for an IBC Coin.
//
// It derives the ERC-20 address from the token denomination and registers the
// EVM extension as an active dynamic precompile.
//
// CONTRACT: This must ONLY be called if there is no existing token pair for the given denom.
func (k Keeper) RegisterERC20Extension(ctx sdk.Context, denom string) (*types.TokenPair, error) {
	pair, err := k.CreateNewTokenPair(ctx, denom)
	if err != nil {
		return nil, err
	}

	// Add to existing EVM extensions
	if err := k.EnableDynamicPrecompile(ctx, pair.GetERC20Contract()); err != nil {
		return nil, err
	}

	return &pair, err
}
```

**File:** evmd/tests/ibc/ibc_middleware_test.go (L384-414)
```go
			// Validate successful callback
			evmApp := suite.evmChainA.App.(*evmd.EVMD)
			singleTokenRepresentation, err := types.NewTokenPairSTRv2(voucherDenom)
			suite.Require().NoError(err)
			erc20Contract := singleTokenRepresentation.GetERC20Contract()

			// Validate results
			if tc.expError == "" {
				suite.Require().True(ack.Success(), "Expected success but got failure")

				balAfterCallback := evmApp.Erc20Keeper.BalanceOf(evmCtx, contracts.ERC20MinterBurnerDecimalsContract.ABI, erc20Contract, contractAddr)
				suite.Require().Equal(sendAmt.String(), balAfterCallback.String())

				tokenPair, found := evmApp.Erc20Keeper.GetTokenPair(evmCtx, singleTokenRepresentation.GetID())
				suite.Require().True(found)
				suite.Require().Equal(voucherDenom, tokenPair.Denom)

				available := evmApp.Erc20Keeper.IsDynamicPrecompileAvailable(evmCtx, common.HexToAddress(tokenPair.Erc20Address))
				suite.Require().True(available)
			} else {
				suite.Require().False(ack.Success(), "Expected failure but got success")

				balAfterCallback := evmApp.Erc20Keeper.BalanceOf(evmCtx, contracts.ERC20MinterBurnerDecimalsContract.ABI, erc20Contract, contractAddr)
				suite.Require().Equal("0", balAfterCallback.String())

				ackObj, ok := ack.(channeltypes.Acknowledgement)
				suite.Require().True(ok)
				ackErr, ok := ackObj.Response.(*channeltypes.Acknowledgement_Error)
				suite.Require().True(ok)
				suite.Require().Contains(ackErr.Error, tc.expError)
			}
```

**File:** x/erc20/ibc_middleware.go (L53-66)
```go
func (im IBCMiddleware) OnRecvPacket(
	ctx sdk.Context,
	channelVersion string,
	packet channeltypes.Packet,
	relayer sdk.AccAddress,
) exported.Acknowledgement {
	ack := im.Module.OnRecvPacket(ctx, channelVersion, packet, relayer)

	// return if the acknowledgement is an error ACK
	if !ack.Success() {
		return ack
	}

	return im.keeper.OnRecvPacket(ctx, packet, ack)
```
