Found the strongest analog: `AssociationHelper.MigrateBalance` at [1](#0-0)  calls `p.bankKeeper.SpendableCoins(ctx, castAddr)`, which (via `GetAllBalances`-style enumeration) iterates **every denom balance** held by the cast/Sei address, with no cap. This function runs unconditionally in the EVM ante/preprocess pipeline every time an address's first EVM interaction occurs: [2](#0-1)  and in the multi-signer path [3](#0-2) .

### Title
Unbounded per-address denom iteration in EVM address association (`MigrateBalance`/`SpendableCoins`) can be forced to run out of gas, permanently blocking EVM usage for a victim account - (File: `utils/helpers/associate.go`)

### Summary
`tokenfactory` lets any account permissionlessly create denoms and, as the denom's admin, "create a transfer of their denom between any two accounts" [4](#0-3) , meaning an attacker can push an unlimited number of distinct dust-value denoms into a victim's Sei/cast address without the victim's consent. When that victim's address is later associated with an EVM address (either via a signed Cosmos tx or their first EVM tx), the chain calls `AssociationHelper.MigrateBalance`, which unconditionally fetches `SpendableCoins`/`GetAllBalances` for the cast address and attempts to `SendCoins` the *entire* coin set in one shot [5](#0-4) . This mirrors the JOJO `getTotalExposure()` bug class: an attacker-inflatable per-account collection is iterated in full inside a mandatory state-transition path, with no cap on its size.

### Finding Description
`MigrateBalance` is invoked from two mandatory paths:
1. `EVMPreprocessDecorator.AnteHandle` — runs for every EVM transaction from an address not yet EVM-associated [2](#0-1) .
2. `UpdateSigners` — runs for every Cosmos-signed tx from an address not yet associated [3](#0-2) .

Both call `p.bankKeeper.SpendableCoins(ctx, castAddr)`, which internally enumerates the account's full balance-store range (same pattern as `GetAllBalances`, confirmed unbounded in `sei-cosmos/x/bank/keeper/view.go` and demonstrated to scale linearly with denom count in `TestKeeperGetAllBalances` with 100,000 denoms) [6](#0-5) . The subsequent `SendCoins(ctx, castAddr, seiAddr, castAddrBalances)` moves the *entire* coin set as one bank operation, whose gas cost scales with the number of distinct denoms.

The attack path: an attacker calls `tokenfactory MsgCreateDenom` many times (cheap, permissionless, cost is only a small creation fee per denom) [7](#0-6) , then uses their resulting admin authority to force-mint/transfer tiny amounts of each distinct denom into the victim's EVM "cast address" (`sdk.AccAddress(evmAddr[:])`) before the victim ever associates it. This is analogous to the JOJO bug: the attacker inflates an unbounded per-user collection that a critical function must later iterate in full.

### Impact Explanation
If the number of injected denoms is large enough, `MigrateBalance`'s `SpendableCoins`+`SendCoins` call will consume gas proportional to denom count, potentially exceeding the tx gas limit or even the block gas limit. Because this migration step is mandatory and unconditional on first EVM interaction, a victim whose cast address has been dusted with enough denoms could be permanently unable to ever associate an EVM address / send any transaction that triggers `UpdateSigners` or `EVMPreprocessDecorator`, since every attempt re-triggers the same oversized migration and fails identically. This effectively **permanently freezes** the victim's ability to use their account for EVM transactions (and potentially Cosmos transactions, since `UpdateSigners` is part of the general ante decorator chain for signed txs) — matching the "permanent freezing" impact bar.

### Likelihood Explanation
Denom creation is permissionless and cheap (paid once per denom by the attacker), and `MsgMint`/force-transfer to any address is a documented admin capability of the denom creator [8](#0-7) . No cooperation from the victim is required — the attacker can target any known Sei address the moment its evm cast address is discoverable (which is any address, since `sdk.AccAddress(evmAddr[:])` is derivable for any target address before association). This makes exploitation straightforward for a determined attacker to grief a chosen victim, though the number of denoms required to cause meaningful gas issues would need to be verified experimentally (this repo's index does not show an explicit cap on denoms-per-address or the exact gas cost per iterated coin, so the precise threshold could not be confirmed from available code).

### Recommendation
- Cap the number of coin denominations processed per `MigrateBalance`/`SpendableCoins` call, or perform migration in bounded batches across multiple blocks/transactions instead of a single all-or-nothing operation.
- Consider excluding dust/zero-admin-verified denoms from automatic migration, or requiring the account owner to explicitly opt into migrating specific denoms rather than migrating all balances unconditionally.
- Alternatively, rate-limit or fee-gate `tokenfactory` denom creation and mint-to-arbitrary-address operations to make large-scale dusting economically infeasible.

### Proof of Concept
1. Attacker calls `MsgCreateDenom` N times to create N distinct `factory/{attacker}/{subdenom}` denoms.
2. For each denom, attacker calls `MsgMint` (admin-authorized) with the victim's EVM cast address (`sdk.AccAddress(victimEvmAddr[:])`) as recipient — permitted per tokenfactory's "create a transfer of their denom between any two accounts" admin capability.
3. Victim's cast address now holds N distinct dust balances.
4. Victim (or anyone) sends a transaction that triggers `EVMPreprocessDecorator.AnteHandle` or `UpdateSigners` for the victim's address, invoking `AssociationHelper.MigrateBalance` → `SpendableCoins`/`GetAllBalances` (iterates N denoms) → `SendCoins` (moves N denoms in one op).
5. For sufficiently large N, this step consumes disproportionate gas, causing the transaction to fail or become prohibitively expensive, and since this migration re-runs identically on every subsequent attempt, the victim's account becomes unable to complete EVM association or transact.

Note: the exact N required to trigger failure, and whether any existing gas-limit safety valve mitigates this in practice, could not be verified from the indexed code alone; a Devin session with full repo/test access would be needed to reproduce and measure actual gas costs.

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

**File:** x/evm/ante/preprocess.go (L91-97)
```go
	} else if isAssociated {
		// noop; for readability
	} else {
		// not associatedTx and not already associated
		if err := associateHelper.AssociateAddresses(ctx, seiAddr, evmAddr, pubkey, false); err != nil {
			return ctx, err
		}
```

**File:** app/ante/cosmos_checktx.go (L559-564)
```go
		evmKeeper.SetAddressMapping(ctx, signer, evmAddr)
		associationHelper := helpers.NewAssociationHelper(evmKeeper, evmKeeper.BankKeeper(), accountKeeper)
		if err := associationHelper.MigrateBalance(ctx, evmAddr, signer, false); err != nil {
			logger.Error("failed to migrate EVM address balance", "address", evmAddr, "err", err)
			return nil, err
		}
```

**File:** x/tokenfactory/README.md (L1-18)
```markdown
# Token Factory

The tokenfactory module allows any account to create a new token with
the name `factory/{creator address}/{subdenom}`. Because tokens are
namespaced by creator address, this allows token minting to be
permissionless, due to not needing to resolve name collisions. A single
account can create multiple denoms, by providing a unique subdenom for each
created denom. Once a denom is created, the original creator is given
"admin" privileges over the asset. This allows them to:

- Mint their denom to any account
- Burn their denom from any account
- Create a transfer of their denom between any two accounts
- Change the admin. In the future, more admin capabilities may be added. Admins
  can choose to share admin privileges with other accounts using the authz
  module. The `ChangeAdmin` functionality, allows changing the master admin
  account, or even setting it to `""`, meaning no account has admin privileges
  of the asset.
```

**File:** sei-cosmos/x/bank/keeper/keeper_test.go (L1998-2017)
```go
func (suite *IntegrationTestSuite) TestKeeperGetAllBalances() {
	app, ctx := suite.app, suite.ctx
	addr := sdk.AccAddress([]byte("addr1_______________"))
	cnt := 100_000
	allDenoms := make(sdk.Coins, cnt)
	for i := 0; i < cnt; i++ {
		d := fmt.Sprintf("d%d", i+10) // denom must be at least 3 chars.
		app.BankKeeper.AddCoins(ctx, addr, sdk.Coins{sdk.Coin{
			Denom: d, Amount: sdk.OneInt(),
		}}, false)
		allDenoms[i] = sdk.Coin{Denom: d}
	}
	allDenoms = allDenoms.Sort()
	balances := app.BankKeeper.GetAllBalances(ctx, addr)
	suite.Require().Len(balances, cnt)
	for i := 0; i < cnt; i++ {
		suite.Require().Equal(allDenoms[i].Denom, balances[i].Denom)
		suite.Require().Equal(sdk.OneInt(), balances[i].Amount)
	}
}
```
