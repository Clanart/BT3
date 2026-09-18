### Title
Tokenfactory denom allow-list restriction is not enforced on `MsgMultiSend` / `InputOutputCoins`, allowing unauthorized transfer of permissioned tokens - ([File: sei-cosmos/x/bank/keeper/send.go])

### Summary
Sei's bank module implements a per-denom transfer allow-list (`DenomAllowList`) that the tokenfactory module uses to restrict which addresses may send/receive a permissioned tokenfactory denom. This control is checked and enforced inside `BaseSendKeeper.SendCoins` (via `SendCoinsWithoutAccCreation`, which consults `IsInDenomAllowList`), but the sibling primitive `InputOutputCoins` — the function that backs `MsgMultiSend` — performs the same balance-moving logic (`SubUnlockedCoins` / `AddCoins`) without ever calling `IsInDenomAllowList`. Any unprivileged transaction sender can therefore move an allow-listed tokenfactory denom to or from an address that is not on the denom's allow list simply by using `MsgMultiSend` instead of `MsgSend`.

### Finding Description
The allow-list enforcement lives in `BaseSendKeeper.IsInDenomAllowList` [1](#0-0)  and is designed to gate transfers of tokenfactory denoms (`factory/<creator>/<subdenom>`) so that only allow-listed addresses can hold/move them, as exercised by `TestSendReceiverNotInAllowList` / `TestSendSenderNotInAllowList` [2](#0-1) [3](#0-2) , which show the check firing for `MsgSend`.

`InputOutputCoins`, the keeper method that implements `MsgMultiSend`, performs the exact same value-transfer semantics (subtract from each input, add to each output) but never references `IsInDenomAllowList` or any allow-list check at all: [4](#0-3) 

By contrast, the single-transfer path `SendCoins`/`SendCoinsWithoutAccCreation` in the same file is the one that is gated by the allow list (as proven by the referenced app tests and by `IsInDenomAllowList`'s own doc comment: "The check is performed only for token factory denoms... it checks if there is allow list for the given denom"). `InputOutputCoins` is a structurally separate code path that duplicates the balance-mutation logic instead of calling through `SendCoins`, so the control that the tokenfactory module relies on to restrict a permissioned denom is silently skipped whenever the same transfer is expressed as a multi-send.

This is the same bug class as the nebula-mesh advisory: a security-relevant deny/allow list is computed and enforced on one code path but a second, equally reachable code path that performs the guarded operation was never wired to check it — the control exists but is not applied everywhere it needs to be.

### Impact Explanation
Tokenfactory denom allow-lists are the compliance/authority control that lets a denom's admin restrict transfers to a specific address set (e.g., for a permissioned RWA or KYC-gated asset). Because `MsgMultiSend` bypasses this control, any holder of the restricted denom (or anyone who can otherwise get such a denom balance) can move the tokens to or from addresses never approved by the admin, defeating the transfer restriction entirely. This is an unauthorized-transfer/authority-bypass bug in the bank/tokenfactory subsystem, directly reachable by any unprivileged transaction sender holding the denom, and matches the "unauthorized transfer" / "bank/tokenfactory authority" impact category.

### Likelihood Explanation
`MsgMultiSend` is a standard, always-enabled Cosmos SDK bank message; no special permission is required to submit it beyond holding the coins involved. The bypass requires no contract deployment, precompile call, or special network condition — a normal signed transaction with one input/output pair recreates a plain send while skipping the allow-list gate. This makes the likelihood of exploitation high once an attacker holds any balance of an allow-listed tokenfactory denom.

### Recommendation
Route `InputOutputCoins` through the same allow-list enforcement as `SendCoins`/`SendCoinsWithoutAccCreation`: call `IsInDenomAllowList` (or a shared helper) against every input and output address for any denom with an active allow list before subtracting/adding balances, and add a regression test asserting `MsgMultiSend` is rejected under the same conditions the existing `TestSendReceiverNotInAllowList`/`TestSendSenderNotInAllowList` tests cover for `MsgSend`.

### Proof of Concept
1. Tokenfactory admin creates denom `factory/<admin>/restricted` and sets `DenomAllowList{Addresses: []string{admin.String()}}` via `SetDenomAllowList` (mirrors `TestSendReceiverNotInAllowList` setup) [5](#0-4) .
2. Confirm `MsgSend` from `admin` to a non-allow-listed `addr2` is rejected with "is not allowed to receive funds" as in the existing test [6](#0-5) .
3. Submit the equivalent transfer as `MsgMultiSend` with `Inputs: [{admin, coins}]`, `Outputs: [{addr2, coins}]`. Because `InputOutputCoins` never calls `IsInDenomAllowList`, the transaction succeeds and `addr2` (not on the allow list) receives the restricted denom, whereas the same transfer via `MsgSend` is blocked — demonstrating the enforcement gap at [4](#0-3) .

### Citations

**File:** sei-cosmos/x/bank/keeper/send.go (L100-160)
```go
// InputOutputCoins performs multi-send functionality. It accepts a series of
// inputs that correspond to a series of outputs. It returns an error if the
// inputs and outputs don't lineup or if any single transfer of tokens fails.
func (k BaseSendKeeper) InputOutputCoins(ctx sdk.Context, inputs []types.Input, outputs []types.Output) error {
	// Safety check ensuring that when sending coins the keeper must maintain the
	// Check supply invariant and validity of Coins.
	if err := types.ValidateInputsOutputs(inputs, outputs); err != nil {
		return err
	}
	for _, in := range inputs {
		inAddress, err := sdk.AccAddressFromBech32(in.Address)
		if err != nil {
			return err
		}

		err = k.SubUnlockedCoins(ctx, inAddress, in.Coins, true)
		if err != nil {
			return err
		}

		ctx.EventManager().EmitEvent(
			sdk.NewEvent(
				sdk.EventTypeMessage,
				sdk.NewAttribute(types.AttributeKeySender, in.Address),
			),
		)
	}

	for _, out := range outputs {
		outAddress, err := sdk.AccAddressFromBech32(out.Address)
		if err != nil {
			return err
		}
		err = k.AddCoins(ctx, outAddress, out.Coins, true)
		if err != nil {
			return err
		}

		ctx.EventManager().EmitEvent(
			sdk.NewEvent(
				types.EventTypeTransfer,
				sdk.NewAttribute(types.AttributeKeyRecipient, out.Address),
				sdk.NewAttribute(sdk.AttributeKeyAmount, out.Coins.String()),
			),
		)

		// Create account if recipient does not exist.
		//
		// NOTE: This should ultimately be removed in favor a more flexible approach
		// such as delegated fee messages.
		accExists := k.ak.HasAccount(ctx, outAddress)
		if !accExists {
			defer func() {
				recordNewAccounts(ctx.Context(), 1)
			}()
			k.ak.SetAccount(ctx, k.ak.NewAccountWithAddress(ctx, outAddress))
		}
	}

	return nil
}
```

**File:** sei-cosmos/x/bank/keeper/send.go (L506-524)
```go
func (k BaseSendKeeper) IsInDenomAllowList(ctx sdk.Context, addr sdk.AccAddress, coins sdk.Coins, cache map[string]AllowedAddresses) bool {
	for _, coin := range coins {
		// Skip if denom does not contain the token factory prefix
		if !strings.HasPrefix(coin.Denom, TokenFactoryPrefix) {
			continue
		}

		allowedAddresses := k.getAllowedAddresses(ctx, cache, coin.Denom)
		// skip if there is no allow list for the denom
		if len(allowedAddresses.set) == 0 {
			continue
		}

		if !allowedAddresses.contains(addr) {
			return false
		}
	}
	return true
}
```

**File:** sei-cosmos/x/bank/app_test.go (L117-149)
```go
func TestSendReceiverNotInAllowList(t *testing.T) {
	acc := &authtypes.BaseAccount{
		Address: addr1.String(),
	}

	genAccs := []authtypes.GenesisAccount{acc}
	a := app.SetupWithGenesisAccounts(t, genAccs)
	ctx := a.BaseApp.NewContext(false, tmproto.Header{})
	testDenom := "testDenom"
	factoryDenom := fmt.Sprintf("factory/%s/%s", addr1.String(), testDenom)

	require.NoError(t, apptesting.FundAccount(a.BankKeeper, ctx, addr1, sdk.NewCoins(sdk.NewInt64Coin(factoryDenom, 100))))
	a.BankKeeper.SetDenomAllowList(ctx, factoryDenom,
		types.AllowList{Addresses: []string{addr1.String()}})

	a.Commit(context.Background())

	res1 := a.AccountKeeper.GetAccount(ctx, addr1)
	require.NotNil(t, res1)
	require.Equal(t, acc, res1.(*authtypes.BaseAccount))

	origAccNum := res1.GetAccountNumber()
	origSeq := res1.GetSequence()

	sendMsg := types.NewMsgSend(addr1, addr2, sdk.Coins{sdk.NewInt64Coin(factoryDenom, 10)})
	header := tmproto.Header{ChainID: a.ChainID, Height: a.LastBlockHeight() + 1}
	txGen := app.MakeEncodingConfig().TxConfig
	_, _, err := app.SignCheckDeliver(t, txGen, a.BaseApp, header, []sdk.Msg{sendMsg}, []uint64{origAccNum}, []uint64{origSeq}, false, false, priv1)
	require.Error(t, err)
	require.Contains(t, err.Error(), fmt.Sprintf("%s is not allowed to receive funds", addr2))

	app.CheckBalance(t, a, addr1, sdk.Coins{sdk.NewInt64Coin(factoryDenom, 100)})
}
```

**File:** sei-cosmos/x/bank/app_test.go (L219-251)
```go
func TestSendSenderNotInAllowList(t *testing.T) {
	acc := &authtypes.BaseAccount{
		Address: addr1.String(),
	}

	genAccs := []authtypes.GenesisAccount{acc}
	a := app.SetupWithGenesisAccounts(t, genAccs)
	ctx := a.BaseApp.NewContext(false, tmproto.Header{})
	testDenom := "testDenom"
	factoryDenom := fmt.Sprintf("factory/%s/%s", addr1.String(), testDenom)

	require.NoError(t, apptesting.FundAccount(a.BankKeeper, ctx, addr1, sdk.NewCoins(sdk.NewInt64Coin(factoryDenom, 100))))
	a.BankKeeper.SetDenomAllowList(ctx, factoryDenom,
		types.AllowList{Addresses: []string{addr2.String()}})

	a.Commit(context.Background())

	res1 := a.AccountKeeper.GetAccount(ctx, addr1)
	require.NotNil(t, res1)
	require.Equal(t, acc, res1.(*authtypes.BaseAccount))

	origAccNum := res1.GetAccountNumber()
	origSeq := res1.GetSequence()

	sendMsg := types.NewMsgSend(addr1, addr2, sdk.Coins{sdk.NewInt64Coin(factoryDenom, 10)})
	header := tmproto.Header{ChainID: a.ChainID, Height: a.LastBlockHeight() + 1}
	txGen := app.MakeEncodingConfig().TxConfig
	_, _, err := app.SignCheckDeliver(t, txGen, a.BaseApp, header, []sdk.Msg{sendMsg}, []uint64{origAccNum}, []uint64{origSeq}, false, false, priv1)
	require.Error(t, err)
	require.Contains(t, err.Error(), fmt.Sprintf("%s is not allowed to send funds", addr1))

	app.CheckBalance(t, a, addr1, sdk.Coins{sdk.NewInt64Coin(factoryDenom, 100)})
}
```
