The code is explicit. Let me verify the exact lines and the `DeleteAccount` behavior to confirm the full picture.

The code is self-documenting on this point. Here is the complete analysis:

---

### Title
Non-EVM-Denom Bank Balances Permanently Orphaned After SELFDESTRUCT — (`x/evm/statedb/statedb.go`)

### Summary
`statedb.Commit()` only burns the EVM-denom balance of a selfdestructed contract. Any non-EVM-denom tokens (IBC, CosmWasm bridge coins, etc.) held at the contract's Cosmos address are left in the bank module after `DeleteAccount` removes the auth account, permanently orphaning those funds. The code itself documents this gap.

### Finding Description

In `statedb.Commit()`, the selfdestruct branch explicitly handles only the EVM denom: [1](#0-0) 

The comment at lines 813–815 reads verbatim:

> *Only the EVM denom is burned here. Non-EVM-native tokens (IBC, CosmWasm bridge) held by the destroyed address are not drained and may remain as orphaned bank balances.*

`DeleteAccount` then removes the auth account and EVM storage, but makes **no bank module call** for any denom: [2](#0-1) 

The bank module stores balances keyed by address, not by account object. After `RemoveAccount`, the IBC token balance remains in the bank KVStore at the contract's Cosmos address with no owning auth account.

The app includes a live IBC transfer keeper, so IBC tokens can legitimately arrive at any Cosmos address (including a contract's `sdk.AccAddress(evmAddr.Bytes())`): [3](#0-2) 

### Impact Explanation

**Orphaning (loss of funds):** Any IBC or other non-EVM-denom tokens held by a selfdestructed contract are permanently inaccessible. The auth account is gone; the bank module cannot send from an address without one. The tokens are effectively burned without being accounted for in the bank module's supply invariant.

**Theft via CREATE2 redeployment:** Because Cosmos SDK bank balances are keyed purely by address, if an attacker redeploys a new contract to the same CREATE2 address after the selfdestruct, a fresh `EthAccount` is created at that address. The bank module then allows spending from it, giving the new contract owner access to the previously orphaned IBC tokens. Concretely:

1. Attacker deploys contract C via CREATE2 (salt S).
2. IBC tokens arrive at C's Cosmos address (e.g., from a user deposit or IBC packet).
3. Attacker triggers SELFDESTRUCT on C within the same transaction (EIP-6780 applies; account is deleted at commit).
4. `statedb.Commit()` burns only the EVM denom; IBC tokens remain in the bank.
5. Attacker redeploys to the same CREATE2 address (same salt S, same init code).
6. New auth account is created at the same Cosmos address; bank balances are now accessible.
7. Attacker transfers the orphaned IBC tokens out.

### Likelihood Explanation

The orphaning path (steps 1–4) is reachable by any unprivileged user who can deploy a contract and trigger SELFDESTRUCT in the same transaction (standard EIP-6780 pattern). The theft path (steps 5–7) additionally requires that someone else's IBC tokens are held by the contract at destruction time — realistic for any DeFi contract that accepts multi-denom deposits. The `evmd` app ships with a live IBC transfer module, making this a concrete on-chain scenario.

### Recommendation

In the `obj.selfDestructed` branch of `statedb.Commit()`, iterate over **all** bank balances held by the contract's Cosmos address (not just `s.evmDenom`) and burn or redirect each one before calling `DeleteAccount`. The Cosmos SDK `bankKeeper.GetAllBalances(ctx, cosmosAddr)` returns the full coin set; each non-EVM denom should be sent to the community pool or burned via a module account, and the operation should be wrapped in the same `CacheContext` already used for the EVM denom burn.

### Proof of Concept

```
1. Deploy SelfDestructTarget via CREATE2 (salt=0x01) in a single tx.
2. Send 100 ibc/ATOM to the contract's sdk.AccAddress via MsgTransfer.
3. Call destroy() on the contract (SELFDESTRUCT, same tx as creation → EIP-6780 deletes account).
4. After block commit:
   - eth_getBalance(contractAddr) == 0  ✓ (EVM denom burned)
   - bankKeeper.GetBalance(cosmosAddr, "ibc/ATOM") == 100  ✗ (orphaned)
   - accountKeeper.GetAccount(cosmosAddr) == nil  ✓ (auth account deleted)
5. Deploy new contract at same CREATE2 address (same salt, same init code).
6. bankKeeper.GetBalance(cosmosAddr, "ibc/ATOM") == 100 — now accessible to new owner.
```

The existing test `TestSelfDestructPostDestructionBalanceBurned` in `x/evm/statedb/statedb_test.go` only checks the EVM denom (`"uphoton"`); adding an assertion for a second denom would immediately demonstrate the orphaning. [4](#0-3)

### Citations

**File:** x/evm/statedb/statedb.go (L811-825)
```go
			cosmosAddr := sdk.AccAddress(obj.Address().Bytes())
			cacheCtx, writeCache := s.origCtx.CacheContext()
			// Only the EVM denom is burned here. Non-EVM-native tokens (IBC, CosmWasm
			// bridge) held by the destroyed address are not drained and may remain as
			// orphaned bank balances.
			if remaining := s.keeper.GetBalance(cacheCtx, cosmosAddr, s.evmDenom); remaining.Sign() > 0 {
				coin := sdk.NewCoin(s.evmDenom, sdkmath.NewIntFromBigInt(remaining.ToBig()))
				if _, err := s.keeper.SubBalance(cacheCtx, cosmosAddr, coin); err != nil {
					return errorsmod.Wrap(err, "failed to burn post-selfdestruct balance")
				}
			}
			if err := s.keeper.DeleteAccount(cacheCtx, obj.Address()); err != nil {
				return errorsmod.Wrap(err, "failed to delete account")
			}
			writeCache()
```

**File:** x/evm/keeper/statedb.go (L189-222)
```go
// DeleteAccount handles contract's suicide call:
// - remove code
// - remove states
// - remove auth account
//
// NOTE: balance should be cleared separately
func (k *Keeper) DeleteAccount(ctx sdk.Context, addr common.Address) error {
	cosmosAddr := sdk.AccAddress(addr.Bytes())
	acct := k.accountKeeper.GetAccount(ctx, cosmosAddr)
	if acct == nil {
		return nil
	}

	// NOTE: only Ethereum accounts (contracts) can be selfdestructed
	_, ok := acct.(ethermint.EthAccountI)
	if !ok {
		return errorsmod.Wrapf(types.ErrInvalidAccount, "type %T, address %s", acct, addr)
	}

	// clear storage
	k.ForEachStorage(ctx, addr, func(key, _ common.Hash) bool {
		k.SetState(ctx, addr, key, nil)
		return true
	})

	// remove auth account
	k.accountKeeper.RemoveAccount(ctx, acct)

	k.debugLog(ctx, "account suicided",
		"ethereum-address", addr,
		"cosmos-address", cosmosAddr,
	)

	return nil
```

**File:** evmd/app.go (L504-516)
```go
	// Create Transfer Keepers
	app.TransferKeeper = ibctransferkeeper.NewKeeper(
		appCodec, app.AccountKeeper.AddressCodec(), runtime.NewKVStoreService(keys[ibctransfertypes.StoreKey]),
		app.IBCKeeper.ChannelKeeper, app.MsgServiceRouter(),
		app.AccountKeeper, app.BankKeeper, authAddr,
	)
	transferModule := transfer.NewAppModule(app.TransferKeeper)
	transferIBCModule := transfer.NewIBCModule(app.TransferKeeper)

	// Create static IBC router, add transfer route, then set and seal it
	ibcRouter := porttypes.NewRouter()
	ibcRouter.AddRoute(ibctransfertypes.ModuleName, transferIBCModule)
	app.IBCKeeper.SetRouter(ibcRouter)
```

**File:** x/evm/statedb/statedb_test.go (L1053-1091)
```go
// TestSelfDestructPostDestructionBalanceBurned verifies that any balance credited to a
// self-destructed address within the same transaction is burned at commit time rather
// than left as an orphaned bank balance recoverable by recreating the address.
func (suite *StateDBTestSuite) TestSelfDestructPostDestructionBalanceBurned() {
	raw, ctx, keeper := setupTestEnv(suite.T())

	// Setup: create a contract account with initial balance and code.
	db := statedb.New(ctx, keeper, emptyTxConfig)
	db.CreateAccount(address)
	db.CreateContract(address)
	db.SetCode(address, []byte("contract code"), 0)
	db.AddBalance(address, uint256.NewInt(100), tracing.BalanceChangeTransfer)
	suite.Require().NoError(db.Commit())

	ctx, keeper = newTestKeeper(suite.T(), raw)

	// Phase 1: Self-destruct the contract; its initial balance (100) must be burned.
	db = statedb.New(ctx, keeper, emptyTxConfig)
	db.SelfDestruct(address)
	suite.Require().True(db.HasSelfDestructed(address))
	suite.Require().Equal(uint256.NewInt(0), db.GetBalance(address))

	// Phase 2: Send value to the already-destroyed address in the same transaction.
	// This simulates a CALL with value to a self-destructed contract.
	postDestructValue := uint256.NewInt(500)
	db.AddBalance(address, postDestructValue, tracing.BalanceChangeTransfer)
	suite.Require().Equal(postDestructValue, db.GetBalance(address))

	suite.Require().NoError(db.Commit())

	// After commit: account metadata must be gone.
	ctx, keeper = newTestKeeper(suite.T(), raw)
	suite.Require().Nil(keeper.GetAccount(ctx, address))

	// The post-destruction balance must be burned (zero), not preserved.
	cosmosAddr := sdk.AccAddress(address.Bytes())
	balance := keeper.GetBalance(ctx, cosmosAddr, "uphoton")
	suite.Require().True(balance.IsZero(), "post-selfdestruct balance must be burned at commit")
}
```
