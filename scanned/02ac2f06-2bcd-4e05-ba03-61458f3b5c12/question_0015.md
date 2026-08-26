# Q0015: BNBZapper.zapInToken - safeApprove without reset on the router

## Question
Note that in rewards/BNBZapper.sol, zapInToken() calls IERC20(fromToken).safeApprove(address(ROUTER), amount) with no prior zeroing, so any allowance residue left by a router that under-consumes permanently disables zapping for that token. Can an attacker holding only tokens bought on market reach it via `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` under the router leaves a non-zero allowance after the swap and force `routePairAddresses[token]` apart from `the path built by _findRouteToBnb`, breaking the invariant that an approval on a repeated swap path must be idempotent for High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: rewards/BNBZapper.sol -> `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` (mechanism: safeApprove without reset on the router)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: fromToken, amount, minRec and receiver, all unrestricted
- Exploit idea: zapInToken() calls IERC20(fromToken).safeApprove(address(ROUTER), amount) with no prior zeroing, so any allowance residue left by a router that under-consumes permanently disables zapping for that token. Precondition: the router leaves a non-zero allowance after the swap.
- Invariant to test: an approval on a repeated swap path must be idempotent; concretely, `routePairAddresses[token]` must stay reconciled with `the path built by _findRouteToBnb`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Foundry fork test against the deployed pool: set up the router leaves a non-zero allowance after the swap, snapshot `routePairAddresses[token]` and `the path built by _findRouteToBnb`, run the attacker's `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
