### Title
Non-EVM-Native (IBC) Bank Balances Orphaned on SELFDESTRUCT, Recoverable via CREATE2 Redeployment — (`File: x/evm/statedb/statedb.go`)

---

### Summary

When an EVM contract that holds non-EVM-native Cosmos bank tokens (e.g., IBC-bridged assets) executes `SELFDESTRUCT`, `StateDB.Commit()` burns only the EVM denom balance and deletes the auth account, but leaves all other bank-module balances (IBC tokens, CosmWasm bridge tokens) as orphaned balances at the now-deleted address. Because the auth account is gone but the bank balance persists, an attacker who controls the original contract can redeploy a new contract at the same CREATE2 address and immediately access those orphaned funds — constituting unauthorized theft of Cosmos bank funds.

---

### Finding Description

In `StateDB.Commit()`, the selfdestruct path explicitly handles only the EVM denom:

```go
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
``` [1](#0-0) 

`DeleteAccount` removes the auth account and clears EVM storage, but never touches non-EVM-native bank balances:

```go
// NOTE: balance should be cleared separately
func (k *Keeper) DeleteAccount(ctx sdk.Context, addr common.Address) error {
    ...
    // clear storage
    k.ForEachStorage(ctx, addr, func(key, _ common.Hash) bool {
        k.SetState(ctx, addr, key, nil)
        return true
    })
    // remove auth account
    k.accountKeeper.RemoveAccount(ctx, acct)
    ...
}
``` [2](#0-1) 

In Cosmos SDK, bank balances are stored independently of auth accounts, keyed only by address. After `RemoveAccount`, the IBC token balance at that address persists in the bank store. When a new contract is subsequently deployed at the same address (via CREATE2 with the same salt and init code), the Cosmos SDK creates a fresh auth account at that address — and the new account immediately inherits the orphaned bank balance, making it accessible to the new contract.

The existing fix and tests (`TestSelfDestructPostDestructionBalanceBurned`, `test_selfdestruct_recreated_address_cannot_recover_funds`) only verify the EVM denom path; they do not cover non-EVM-native tokens. [3](#0-2) 

---

### Impact Explanation

**Critical — Unauthorized theft of Cosmos bank funds through Ethermint stateDB commit logic.**

An attacker who controls a contract holding IBC tokens can:
1. Deploy a malicious contract at a deterministic CREATE2 address.
2. Attract IBC token deposits from users (e.g., by posing as a DeFi vault).
3. Call `SELFDESTRUCT` on the contract (pre-EIP-6780 chains) or within the same deployment transaction (EIP-6780 chains with a self-destructing constructor).
4. The auth account is deleted; the EVM denom is burned; but IBC tokens remain in the bank store at that address.
5. Redeploy a new contract at the same CREATE2 address.
6. The new contract inherits the orphaned IBC bank balance and can drain it to the attacker.

This is a direct, unprivileged theft of Cosmos bank funds (IBC assets) through Ethermint's EVM state transition and stateDB commit path.

---

### Likelihood Explanation

**High.** The attack requires only:
- A contract with a `SELFDESTRUCT` path (trivial to write).
- IBC tokens sent to the contract (realistic for any bridge-integrated DeFi contract).
- CREATE2 redeployment (standard Solidity pattern).

No privileged roles, governance, or validator compromise is needed. The vulnerability is reachable via any standard EVM transaction. The code comment explicitly acknowledges the gap, confirming the developers are aware the non-EVM-native path is unhandled.

---

### Recommendation

In `StateDB.Commit()`, before calling `DeleteAccount`, enumerate **all** bank-module balances held by the selfdestructed address (not just the EVM denom) and burn or redirect each one. The Cosmos SDK `BankKeeper` provides `GetAllBalances(ctx, addr)` for this purpose:

```go
// Burn ALL bank balances, not just the EVM denom
allBalances := s.keeper.GetAllBalances(cacheCtx, cosmosAddr)
for _, coin := range allBalances {
    if _, err := s.keeper.SubBalance(cacheCtx, cosmosAddr, coin); err != nil {
        return errorsmod.Wrap(err, "failed to burn post-selfdestruct balance")
    }
}
```

Alternatively, if burning non-EVM-native tokens is undesirable (e.g., they should be returned to a bridge module), a configurable hook per denom can be used. The critical invariant is: **no bank balance of any denom must survive at a selfdestructed address**.

---

### Proof of Concept

**Step 1 — Deploy malicious vault contract via CREATE2:**
```solidity
contract MaliciousVault {
    address immutable factory;
    bytes32 immutable salt;
    constructor(address _factory, bytes32 _salt) {
        factory = _factory; salt = _salt;
    }
    // Accept IBC token deposits via Cosmos bank send
    function steal() external {
        selfdestruct(payable(address(0))); // pre-EIP-6780 chain
    }
}
```

**Step 2 — Users send IBC tokens (e.g., `ibc/...` denom) to the vault address via Cosmos `MsgSend`.**

**Step 3 — Attacker calls `steal()`.** In `StateDB.Commit()`:
- EVM denom burned ✓
- Auth account deleted ✓
- IBC bank balance **not burned** ✗ — remains at address X in bank store.

**Step 4 — Attacker redeploys at same CREATE2 address.** New auth account created at X. Bank module returns the orphaned IBC balance when queried for address X.

**Step 5 — New contract calls a Cosmos precompile or native action to transfer the IBC tokens to the attacker.**

The existing test `test_selfdestruct_recreated_address_cannot_recover_funds` passes only because it checks `w3.eth.get_balance` (EVM denom), not IBC token balances. [4](#0-3) [5](#0-4)

### Citations

**File:** x/evm/statedb/statedb.go (L800-825)
```go
		if obj.selfDestructed {
			// Burn any balance that arrived after SelfDestruct was called (e.g., via a
			// value-bearing CALL to the destroyed address within the same transaction).
			// SelfDestruct already burned the balance present at destruction time, but
			// subsequent AddBalance calls write to the bank without a matching burn.
			// DeleteAccount only removes auth metadata and storage; it never touches the
			// bank balance, so we must drain it here before removing the account.
			//
			// Both operations run inside a single CacheContext so that if DeleteAccount
			// fails after SubBalance, the partial burn is rolled back and the bank is
			// left consistent.
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
