### Title
Stale gasless swap balance/allowance checks allow proposer gas-lending loss with rebasing/fee-on-transfer tokens - (File: `kaiax/gasless/impl/tx_pool.go`)

### Summary
The `kaiax/gasless` module admits `GaslessApproveTx`/`GaslessSwapTx` bundles into the tx pool based on a one-time snapshot of the ERC-20 token's `balanceOf`/`allowance` taken at admission time, then relies on that stale check to gate promotion and bundling with a block-proposer-funded gas advance (`LendTxGenerator`). Because `AllowedTokens` defaults to permitting **all** ERC-20 tokens [1](#0-0) , a non-standard token (rebasing or fee-on-transfer) can change the sender's real balance/allowance between the pool-admission check and the block-inclusion execution, causing the swap to revert on-chain after the proposer has already advanced gas to the user, with no repayment.

### Finding Description
`checkBalanceForSwap` reads `tokenContract.Allowance(...)` and `tokenContract.BalanceOf(...)` once, at the moment the swap tx is checked for pool admission, and treats these values as valid preconditions for promoting the bundle to pending/ready state: [2](#0-1) 

This mirrors the external report's root cause: the code assumes a token balance/allowance recorded at one point in time remains valid later, which does not hold for rebasing tokens (balance changes via a `rebase()`-like mechanism) or fee-on-transfer tokens. The gasless bundle can sit in the queue/pending pool for up to `QueueTimeout`/`PendingTimeout` (10s each, i.e., across multiple blocks) before being included [3](#0-2) , and the pool's own README documents that these checks are heuristics, not authoritative for execution: "Sender balance check is omitted for gasless transactions" is only avoided at the mempool level via `GetCheckBalance`, and the actual bundle execution defers to on-chain enforcement [4](#0-3) .

Critically, the module's block-building flow prepends a `LendTxGenerator` transaction (which unconditionally sends native KAIA gas to the user) ahead of the approve/swap pair: `[LendTxGenerator, GaslessApproveTx, GaslessSwapTx]` [5](#0-4) . The `LendTxGenerator` transaction is a distinct, already-applied transaction — if the subsequent `GaslessSwapTx` reverts because the sender's real token balance/allowance no longer matches what was checked at admission time (e.g., due to an intervening rebase or fee-on-transfer skew), the on-chain `amountRepay` to the proposer inside `swapForGas` never executes, but the lent KAIA has already been irreversibly transferred to the user.

### Impact Explanation
This allows an unprivileged gasless user to cause the block proposer to lose the native KAIA gas it fronted via `LendTxGenerator`, without repayment, whenever a whitelisted (or default-allowed, since `AllowedTokens` is `nil`/all by default) token exhibits non-standard balance semantics between admission-time check and execution-time settlement. This is a direct value-extraction/fee-delegation abuse against the block proposer, analogous in root cause to the H-01 report: reliance on a point-in-time balance/allowance snapshot for a token whose balance can change out-of-band, used to authorize a value transfer (gas lending) that is not re-verified at execution.

### Likelihood Explanation
Exploitability requires the gasless-enabled token to have rebasing or fee-on-transfer semantics and for the token to remain in `AllowedTokens` (default is all tokens allowed) [6](#0-5) . A malicious user can also intentionally trigger the balance drift themselves (e.g., front-running their own approve/swap submission with a transfer or a rebase-triggering call, exactly as demonstrated in the external report's PoC), making this fully attacker-controlled rather than dependent on chance. The bundle's multi-second queue/pending dwell time [3](#0-2)  gives ample window for such manipulation.

### Recommendation
Do not trust the admission-time `BalanceOf`/`Allowance` snapshot as sufficient for authorizing the gas-lending bundle. Options: (1) re-verify actual token balance/allowance immediately before executing `LendTxGenerator` at block-building time (as close as possible to the swap execution), (2) make `LendTxGenerator` conditional/atomic with the swap's success (e.g., bundle-revert-together semantics so a failed swap also fails/excludes the lend tx from the block), or (3) exclude tokens with non-standard balance mechanics (rebasing/fee-on-transfer) from `AllowedTokens` by default and document the risk for operators who whitelist additional tokens.

### Proof of Concept
1. Operator configures `--gasless.allowed-tokens` to include (or leaves default `all`, allowing) a rebasing ERC-20 token.
2. Attacker submits `GaslessApproveTx` (approve max to swap router) with sufficient balance; passes `checkBalanceForApprove` [7](#0-6) .
3. Attacker submits `GaslessSwapTx` with `amountIn` matching current balance/allowance; passes `checkBalanceForSwap` at admission time [2](#0-1)  and is promoted to pending, bundled as `[LendTxGenerator, ApproveTx, SwapTx]`.
4. Before block inclusion (during the up-to-10s queue/pending window), attacker triggers a rebase (or moves/loses balance via a fee-on-transfer path) that reduces their real token balance/allowance below `amountIn`.
5. Block is built: `LendTxGenerator` executes, sending KAIA gas to attacker's address. `GaslessSwapTx` then reverts on-chain (real `transferFrom`/`balanceOf` check fails inside `swapForGas`), so `amountRepay` is never paid to the proposer.
6. Net effect: attacker keeps the lent KAIA gas and pays nothing back — value extracted from the block proposer's fee-delegation mechanism.

### Citations

**File:** kaiax/gasless/config.go (L26-33)
```go
var (
	AllowedTokensFlag = &cli.StringSliceFlag{
		Name:     "gasless.allowed-tokens",
		Usage:    "allow token addresses for gasless module, allow all tokens if all",
		Value:    cli.NewStringSlice("all"),
		Aliases:  []string{"kaiax.module.gasless.allowed-tokens"},
		Category: "KAIAX",
	}
```

**File:** kaiax/gasless/config.go (L71-88)
```go
type GaslessConfig struct {
	// all tokens are allowed if AllowedTokens is nil while all are disallowed if empty slice
	AllowedTokens         []common.Address `toml:",omitempty"`
	Disable               bool
	MaxBundleTxsInPending uint
	MaxBundleTxsInQueue   uint
	BalanceCheckLevel     int
}

func DefaultGaslessConfig() *GaslessConfig {
	return &GaslessConfig{
		AllowedTokens:         nil,
		Disable:               false,
		MaxBundleTxsInPending: 100,
		MaxBundleTxsInQueue:   200,
		BalanceCheckLevel:     BalanceCheckLevelAll,
	}
}
```

**File:** kaiax/gasless/impl/tx_pool.go (L33-35)
```go
	QueueTimeout   = 10 * time.Second // bundle tx is removed from txpool.queue after QueueTimeout
	PendingTimeout = 10 * time.Second // bundle tx is removed from txpool.pending after PendingTimeout
	KnownTxTimeout = 30 * time.Second // bundle tx is removed from knownTxs after KnownTxTimeout
```

**File:** kaiax/gasless/impl/tx_pool.go (L74-100)
```go
func (g *GaslessModule) checkBalanceForApprove(approveArgs *ApproveArgs) error {
	token := approveArgs.Token
	bc := backends.NewBlockchainContractBackend(g.Chain, nil, nil)

	if g.GaslessConfig.ShouldCheckSenderCode() {
		if g.getCurrentHasCode(approveArgs.Sender) {
			return errors.New("sender with code is not allowed")
		}
	}

	if g.GaslessConfig.ShouldCheckToken() {
		tokenContract, err := sc_erc20.NewERC20(token, bc)
		if err != nil {
			return err
		}

		// tx.token.balanceOf(sender) > 0
		tokenBalance, err := tokenContract.BalanceOf(nil, approveArgs.Sender)
		if err != nil {
			return err
		}
		if tokenBalance.Sign() <= 0 {
			return fmt.Errorf("insufficient sender token balance: token=%s, have=%s, want=nonzero", token.Hex(), tokenBalance.String())
		}
	}
	return nil
}
```

**File:** kaiax/gasless/impl/tx_pool.go (L144-172)
```go
	if g.GaslessConfig.ShouldCheckToken() {

		tokenContract, err := sc_erc20.NewERC20(token, bc)
		if err != nil {
			return err
		}

		// If SwapTx.nonce is the sender's next nonce, then there is no room for ApproveTx proceeding SwapTx.
		senderNonce := g.getCurrentStateNonce(swapArgs.Sender)
		noApproveTxPreceeds := swapNonce == senderNonce
		if noApproveTxPreceeds {
			// tx.token.allowance(sender, router) >= tx.amountIn
			approval, err := tokenContract.Allowance(nil, swapArgs.Sender, swapRouter)
			if err != nil {
				return err
			}
			if approval.Cmp(swapArgs.AmountIn) < 0 {
				return fmt.Errorf("insufficient approval: approval=%s, want=%s", approval.String(), swapArgs.AmountIn.String())
			}
		}

		// tx.token.balanceOf(sender) >= tx.amountIn
		balance, err := tokenContract.BalanceOf(nil, swapArgs.Sender)
		if err != nil {
			return err
		}
		if balance.Cmp(swapArgs.AmountIn) < 0 {
			return fmt.Errorf("insufficient balance: balance=%s, want=%s", balance.String(), swapArgs.AmountIn.String())
		}
```

**File:** kaiax/gasless/README.md (L25-36)
```markdown
#### Balance check

Sender balance check is omitted for gasless transactions (see `GetCheckBalance()`).

### Block building rules

Upon detection of GaslessTxs, the following logics are executed:

- Per sender, if exists, GaslessApproveTx is relocated before GaslessSwapTx.
- LendTxGenerator is prepended before GaslessApproveTx.
- A new bundle is generated which contain either `[LendTxGenerator, GaslessApproveTx, GaslessSwapTx]` or `[LendTxGenerator, GaslessSwapTx]`
- If the bundle has conflict with any previous bundles, it is excluded from the returned bundle list.
```
