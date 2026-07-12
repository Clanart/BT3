# Q1583: Keeper.EVMBlockConfig - Coinbase Derived From Proposer Address Inconsistently

## Question
Can an unprivileged attacker call a malicious contract from an unprivileged EOA through `per-block EVM config construction for transaction execution` while controlling `contract creation flag` and `gas limit`, under the precondition that a contract performs nested CALL/CREATE and reverts one frame, drive `EVMConfig -> NewEVM -> StateDB journal -> receipt/log/bloom construction` in `x/evm/keeper/config.go::Keeper.EVMBlockConfig` so that CoinBase derived from proposer address inconsistently, violating the invariant that failed EVM or hook execution must not persist unauthorized balance, code, or storage mutation, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `x/evm/keeper/config.go::Keeper.EVMBlockConfig`
- Entrypoint: `per-block EVM config construction for transaction execution`
- Attacker controls: `contract creation flag`, `gas limit`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: CoinBase derived from proposer address inconsistently through `EVMConfig -> NewEVM -> StateDB journal -> receipt/log/bloom construction`.
- Invariant to test: failed EVM or hook execution must not persist unauthorized balance, code, or storage mutation.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: write a focused Go unit test around the target function and assert bank supply, sender balance, nonce, code hash, logs, and receipt status before and after.
