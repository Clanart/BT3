### Title
Attacker can force a synchronous unbounded-denom balance migration during EVM address association, delaying block execution - ([File: utils/helpers/associate.go])

### Summary
The Canto coinswap report shows that unbounded iteration over `bank.GetAllBalances`/`IterateAccountBalances` can be weaponized by an attacker who donates a large number of distinct token denominations to a target address, causing the O(n) balance-iteration logic to blow up. Sei-chain has a structurally identical, more critically-reachable pattern in the EVM `Associate` flow: `MigrateBalance` in `utils/helpers/associate.go` calls `bankKeeper.SpendableCoins(ctx, castAddr)` and `bankKeeper.LockedCoins(ctx, castAddr)`, both of which internally iterate every coin denomination held by the "cast address" (`sdk.AccAddress(evmAddr[:])`). This migration is triggered automatically, without the victim's consent, the first time an address interacts with the chain either via an EVM tx or a Cosmos tx.

### Finding Description
`AssociationHelper.MigrateBalance` [1](#0-0)  reads `p.bankKeeper.SpendableCoins(ctx, castAddr)` and, if non-zero, calls `SendCoins` to move every coin from the cast address to the real Sei address. Both `SpendableCoins` and `LockedCoins` are built on top of the same `IterateAccountBalances`/`GetAllBalances`-style loop identified in the external report [2](#0-1) [3](#0-2) .

This migration path is reached from two unprivileged, attacker-triggerable entry points:
1. `EVMPreprocessDecorator.AnteHandle`, invoked on every EVM transaction, calls `associateHelper.AssociateAddresses` → `MigrateBalance` for any address that is not yet associated [4](#0-3) .
2. `EVMAddressDecorator.AnteHandle`, invoked on every plain Cosmos transaction, does the same for each tx signer that lacks an EVM association [5](#0-4) .

Because `sdk.AccAddress(evmAddr[:])` (the "cast address") is a valid bank account that anyone can send tokens to via ordinary `MsgSend` or IBC transfer — no whitelisting or pool-creation restriction exists here, unlike Canto's coinswap pools which are gated to whitelisted denoms — an attacker can pre-emptively donate an arbitrarily large number of distinct token/IBC denominations to a victim's cast address before the victim ever associates their EVM and Sei identities. When the victim later sends their first EVM or Cosmos transaction, the ante-handler-triggered `MigrateBalance` call must iterate over every one of those denominations to compute `SpendableCoins`/`LockedCoins` and then execute a `SendCoins` covering all of them.

Note that for the EVM path, this happens under an infinite gas meter (`ctx.WithGasMeter(sdk.NewInfiniteGasMeterWithMultiplier(ctx))` is set immediately before the association logic runs) [6](#0-5) , so the operation cannot revert on out-of-gas as the coinswap bug does — instead it will simply consume real wall-clock CPU time proportional to the number of denominations donated, executed synchronously inside a single transaction's ante-handler processing during block execution.

### Impact Explanation
Because the balance migration runs synchronously as part of ante-handler processing for a single transaction (both for EVM txs and Cosmos txs from an unassociated signer), and is not bounded by the tx's gas limit on the EVM path (infinite gas meter), an attacker can inflate the number of coin denominations sitting in a victim's un-associated cast address to a very large number, causing that victim's association transaction (unavoidable — it happens automatically on their first tx) to take substantially longer to process than a normal transaction. If this delay is large enough, it directly translates into increased block-processing time for whichever block includes the transaction, which can push block time delay beyond the 2.5-second threshold, and in extreme cases could also be used to grief a specific victim by making their address permanently expensive/slow to interact with (a milder form of freezing, since every subsequent unassociated tx from that account would trigger the same iteration until association succeeds).

### Likelihood Explanation
Likelihood is moderate: unlike the Canto coinswap case (where pool creation is denom-whitelisted and thus not exploitable, per Canto's own confirmation), there is no restriction here — any address can be the recipient of arbitrary token or IBC denominations via ordinary bank sends, and the migration is unconditionally triggered by the ante-handler pipeline the first time that address transacts, with no opt-out. An attacker only needs to know or predict a victim's future EVM/Sei address (e.g., cast addresses are deterministic from a well-known pubkey-derived EVM address before explicit association) and send it many low-value/no-value coin denominations.

### Recommendation
- Cap the number of distinct denominations considered during `MigrateBalance`/association, or move balance migration for accounts with excessive denom counts to an asynchronous/permissioned governance-driven cleanup path instead of automatic ante-handler execution.
- Consider charging real (non-infinite) gas for the association/migration step so cost scales with the actual work performed, discouraging the attack economically, or impose a hard cap on the number of coin denoms migrated per association call.
- Add a guard that rejects an account from receiving new balances if it already holds an anomalously large number of distinct denominations while remaining un-associated (similar in spirit to the "check only whitelisted denoms" recommendation from the original report).

### Proof of Concept
1. Compute a victim's future cast address: `castAddr = sdk.AccAddress(evmAddr[:])` for a target EVM address that has not yet called `Associate` or sent any transaction.
2. From an attacker-controlled account, issue a large number of `MsgSend` (or repeated IBC transfers with unique denom paths) to `castAddr`, each carrying a distinct denomination (e.g., thousands of unique IBC voucher denoms), at minimal cost per denom.
3. Wait for the victim to send their first EVM transaction (or any Cosmos transaction) from the associated Sei address — this is unavoidable, since it's how normal usage begins.
4. Observe that `EVMPreprocessDecorator`/`EVMAddressDecorator` triggers `AssociationHelper.MigrateBalance`, which calls `SpendableCoins`/`LockedCoins`/`SendCoins` over the full set of attacker-donated denominations [7](#0-6) , causing that single transaction's ante-handler processing to take substantially longer than normal, proportional to the number of injected denominations.

### Citations

**File:** utils/helpers/associate.go (L57-83)
```go
func (p AssociationHelper) MigrateBalance(ctx sdk.Context, evmAddr common.Address, seiAddr sdk.AccAddress, migrateUseiOnly bool) error {
	castAddr := sdk.AccAddress(evmAddr[:])
	if castAddr.Equals(seiAddr) {
		return nil
	}
	var castAddrBalances sdk.Coins
	if migrateUseiOnly {
		castAddrBalances = sdk.Coins{p.bankKeeper.GetBalance(ctx, castAddr, "usei")}
	} else {
		castAddrBalances = p.bankKeeper.SpendableCoins(ctx, castAddr)
	}
	if !castAddrBalances.IsZero() {
		if err := p.bankKeeper.SendCoins(ctx, castAddr, seiAddr, castAddrBalances); err != nil {
			return err
		}
	}
	castAddrWei := p.bankKeeper.GetWeiBalance(ctx, castAddr)
	if !castAddrWei.IsZero() {
		if err := p.bankKeeper.SendCoinsAndWei(ctx, castAddr, seiAddr, sdk.ZeroInt(), castAddrWei); err != nil {
			return err
		}
	}
	if p.bankKeeper.LockedCoins(ctx, castAddr).IsZero() {
		p.accountKeeper.RemoveAccount(ctx, authtypes.NewBaseAccountWithAddress(castAddr))
	}
	return nil
}
```

**File:** sei-cosmos/x/bank/keeper/view.go (L57-66)
```go
// GetAllBalances returns all the account balances for the given account address.
func (k BaseViewKeeper) GetAllBalances(ctx sdk.Context, addr sdk.AccAddress) sdk.Coins {
	balances := sdk.NewCoins()
	k.IterateAccountBalances(ctx, addr, func(balance sdk.Coin) bool {
		balances = append(balances, balance)
		return false
	})

	return balances.Sort()
}
```

**File:** sei-cosmos/x/bank/keeper/view.go (L110-127)
```go
// IterateAccountBalances iterates over the balances of a single account and
// provides the token balance to a callback. If true is returned from the
// callback, iteration is halted.
func (k BaseViewKeeper) IterateAccountBalances(ctx sdk.Context, addr sdk.AccAddress, cb func(sdk.Coin) bool) {
	accountStore := k.getAccountStore(ctx, addr)

	iterator := accountStore.Iterator(nil, nil)
	defer func() { _ = iterator.Close() }()

	for ; iterator.Valid(); iterator.Next() {
		var balance sdk.Coin
		k.cdc.MustUnmarshal(iterator.Value(), &balance)

		if cb(balance) {
			break
		}
	}
}
```

**File:** x/evm/ante/preprocess.go (L57-66)
```go
//nolint:revive
func (p *EVMPreprocessDecorator) AnteHandle(ctx sdk.Context, tx sdk.Tx, simulate bool, next sdk.AnteHandler) (sdk.Context, error) {
	msg := evmtypes.MustGetEVMTransactionMessage(tx)
	if err := Preprocess(ctx, msg, p.evmKeeper.ChainID(ctx), p.evmKeeper.EthBlockTestConfig.Enabled); err != nil {
		return ctx, err
	}

	// use infinite gas meter for EVM transaction because EVM handles gas checking from within
	ctx = ctx.WithGasMeter(sdk.NewInfiniteGasMeterWithMultiplier(ctx))

```

**File:** x/evm/ante/preprocess.go (L91-101)
```go
	} else if isAssociated {
		// noop; for readability
	} else {
		// not associatedTx and not already associated
		if err := associateHelper.AssociateAddresses(ctx, seiAddr, evmAddr, pubkey, false); err != nil {
			return ctx, err
		}
		if p.evmKeeper.EthReplayConfig.Enabled {
			p.evmKeeper.PrepareReplayedAddr(ctx, evmAddr)
		}
	}
```

**File:** x/evm/ante/preprocess.go (L303-347)
```go
//nolint:revive
func (p *EVMAddressDecorator) AnteHandle(ctx sdk.Context, tx sdk.Tx, simulate bool, next sdk.AnteHandler) (sdk.Context, error) {
	sigTx, ok := tx.(authsigning.SigVerifiableTx)
	if !ok {
		return ctx, sdkerrors.Wrap(sdkerrors.ErrTxDecode, "invalid tx type")
	}
	signers := sigTx.GetSigners()
	for _, signer := range signers {
		if evmAddr, associated := p.evmKeeper.GetEVMAddress(ctx, signer); associated {
			ctx.EventManager().EmitEvent(sdk.NewEvent(evmtypes.EventTypeSigner,
				sdk.NewAttribute(evmtypes.AttributeKeyEvmAddress, evmAddr.Hex()),
				sdk.NewAttribute(evmtypes.AttributeKeySeiAddress, signer.String())))
			continue
		}
		acc := p.accountKeeper.GetAccount(ctx, signer)
		if acc.GetPubKey() == nil {
			logger.Error("missing pubkey for signer", "signer", signer)
			ctx.EventManager().EmitEvent(sdk.NewEvent(evmtypes.EventTypeSigner,
				sdk.NewAttribute(evmtypes.AttributeKeySeiAddress, signer.String())))
			continue
		}
		pk, err := btcec.ParsePubKey(acc.GetPubKey().Bytes())
		if err != nil {
			logger.Debug("failed to parse pubkey for account, likely due to the fact that it isn't on secp256k1 curve", "account", acc.GetPubKey(), "err", err)
			ctx.EventManager().EmitEvent(sdk.NewEvent(evmtypes.EventTypeSigner,
				sdk.NewAttribute(evmtypes.AttributeKeySeiAddress, signer.String())))
			continue
		}
		evmAddr, err := helpers.PubkeyToEVMAddress(pk.SerializeUncompressed())
		if err != nil {
			logger.Error("failed to get EVM address from pubkey", "err", err)
			ctx.EventManager().EmitEvent(sdk.NewEvent(evmtypes.EventTypeSigner,
				sdk.NewAttribute(evmtypes.AttributeKeySeiAddress, signer.String())))
			continue
		}
		ctx.EventManager().EmitEvent(sdk.NewEvent(evmtypes.EventTypeSigner,
			sdk.NewAttribute(evmtypes.AttributeKeyEvmAddress, evmAddr.Hex()),
			sdk.NewAttribute(evmtypes.AttributeKeySeiAddress, signer.String())))
		p.evmKeeper.SetAddressMapping(ctx, signer, evmAddr)
		associationHelper := helpers.NewAssociationHelper(p.evmKeeper, p.evmKeeper.BankKeeper(), p.accountKeeper)
		if err := associationHelper.MigrateBalance(ctx, evmAddr, signer, false); err != nil {
			logger.Error("failed to migrate EVM address balance", "address", evmAddr, "err", err)
			return ctx, err
		}
	}
```
