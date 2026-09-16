### Title
Gasless module's `Disable` flag is not checked before granting balance-check exemption and bundling privileges to swap/approve transactions - (File: kaiax/gasless/impl/tx_pool.go)

### Summary
The `GaslessConfig.Disable` flag is meant to turn off the whole gasless module (KIP-247), e.g. when a consensus node/lender has insufficient balance to fund `LendTxGenerator`. However, the tx-pool integration hooks that grant gasless transactions their special privileges — skipping the normal sender-balance check and admitting them into bundle tracking — never consult `IsDisabled()`.

### Finding Description
`GaslessModule.Init` sets `g.GaslessConfig.Disable = true` when a consensus node lacks the minimum lender balance [1](#0-0) , and exposes this via `IsDisabled()` [2](#0-1) .

The tx pool asks every registered module `IsModuleTx(tx)` to decide whether to treat a transaction specially (i.e., skip the standard balance check via `GetCheckBalance`) [3](#0-2) . The gasless module's `IsModuleTx` only checks whether the transaction structurally looks like an approve/swap transaction — it does not check `g.IsDisabled()` at all: [4](#0-3) . Likewise, `GetCheckBalance()` performs the gasless-specific (weaker) balance checks (`checkBalanceForApprove`/`checkBalanceForSwap`) instead of the normal `senderBalance.Cmp(tx.Cost())` check, again without consulting `Disable` [5](#0-4) .

This mirrors the reported bug class exactly: a pause/disable flag (`whenNotPartyBActionsPaused` / `GaslessConfig.Disable`) is enforced on the module's "front door" entry points but a second, still-reachable path (`deposit` / the tx-pool `IsModuleTx`/`GetCheckBalance` hooks) is not gated by the same flag, allowing the guarded behavior to still occur while the module is supposed to be turned off.

### Impact Explanation
When the module is disabled (most importantly the automatic disable-on-insufficient-lender-balance case), transactions that look like gasless approve/swap transactions still: (1) skip the mandatory sender balance check normally enforced by `blockchain/tx_pool.go`'s `validateTx`, and (2) get tracked/promoted as bundle transactions (`IsBundleTx`, `IsReady`, `PreAddTx`), consuming bundle-tracking capacity and pool promotion rules meant only for legitimate, funded gasless flows. This can let underfunded senders get transactions accepted into the pool/pending queue that would otherwise be rejected for insufficient funds, and can cause pool state/queue behavior to diverge from the intended "module disabled" semantics across nodes (nodes with the module enabled vs. disabled would treat the same transaction differently only with respect to the explicit `Disable` state, but the pool-level exemption is unconditionally applied regardless of `Disable`).

### Likelihood Explanation
This requires no special privilege — any transaction sender can craft a normal ERC-20 `approve`/`swapForGas`-shaped transaction targeting the allowed token/router addresses; the disable state is a runtime condition (e.g., CN lender balance dropping) that is outside the attacker's control but plausible in production, and once it occurs the bypass is automatic and requires no additional attacker action beyond submitting an otherwise-normal gasless-shaped transaction.

### Recommendation
Add an explicit `g.IsDisabled()` check at the top of `IsModuleTx` (and/or before invoking `GetCheckBalance`) in `kaiax/gasless/impl/tx_pool.go`, returning `false` immediately when the module is disabled, so that disabled-module transactions fall through to the standard tx-pool balance check and are not treated as bundle/module transactions.

### Proof of Concept
1. Configure/observe a consensus node where `GaslessModule.Init` sets `Disable = true` due to insufficient lender balance [1](#0-0) .
2. Submit an approve or swapForGas-shaped transaction against the whitelisted token/router with a sender balance insufficient to cover `tx.Cost()`.
3. In `blockchain/tx_pool.go`'s `validateTx`, `module.IsModuleTx(tx)` returns `true` (it never checks `Disable`) [4](#0-3) , so `shouldSkipBalanceCheck` is set and the module's `GetCheckBalance` (weaker check) runs instead of the standard cost check [3](#0-2) .
4. The transaction is admitted to the pool despite the gasless module being disabled and despite failing the standard balance requirement.

Note: verifying whether the block-building bundle-extraction path (worker) similarly ignores `Disable` could not be completed within the available investigation window; this is flagged as an area for further confirmation.

### Citations

**File:** kaiax/gasless/impl/init.go (L87-95)
```go
	// Disable module if CN (lender) does not have sufficient balance
	if g.NodeType == common.CONSENSUSNODE {
		nodeAddr := crypto.PubkeyToAddress(opts.NodeKey.PublicKey)
		balance := g.getCurrentStateBalance(nodeAddr)
		if balance.Cmp(GaslessLenderMinBal) < 0 {
			g.GaslessConfig.Disable = true
			logger.Warn("disabling gasless module due to insufficient balance", "node", nodeAddr.Hex(), "balance", balance.String())
		}
	}
```

**File:** kaiax/gasless/impl/init.go (L100-102)
```go
func (g *GaslessModule) IsDisabled() bool {
	return g.GaslessConfig.Disable
}
```

**File:** blockchain/tx_pool.go (L918-932)
```go
	// If module recognizes the tx, run an alternative balance check and then skip the default balance check later.
	shouldSkipBalanceCheck := false
	for _, module := range pool.modules {
		if module.IsModuleTx(tx) {
			if checkBalance := module.GetCheckBalance(); checkBalance != nil {
				shouldSkipBalanceCheck = true
				err := checkBalance(tx)
				if err != nil {
					logger.Trace("[tx_pool] invalid funds of module transaction sender", "from", from, "txhash", tx.Hash().Hex())
					return err
				}
			}
			break
		}
	}
```

**File:** kaiax/gasless/impl/tx_pool.go (L55-60)
```go
func (g *GaslessModule) IsModuleTx(tx *types.Transaction) bool {
	if tx == nil {
		return false
	}
	return g.IsApproveTx(tx) || g.IsSwapTx(tx)
}
```

**File:** kaiax/gasless/impl/tx_pool.go (L62-72)
```go
func (g *GaslessModule) GetCheckBalance() func(tx *types.Transaction) error {
	return func(tx *types.Transaction) error {
		if approveArgs, ok := decodeApproveTx(tx, g.signer); ok {
			return g.checkBalanceForApprove(approveArgs)
		}
		if swapArgs, ok := decodeSwapTx(tx, g.signer); ok {
			return g.checkBalanceForSwap(swapArgs, tx.Nonce())
		}
		return errors.New("not a gasless transaction") // should not happen because IsModuleTx is called before GetCheckBalance
	}
}
```
