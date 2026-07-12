# Q1240: Keeper.VMConfig - Fork Rule Mismatch Changes Gas Cost And Refunds

## Question
Can an unprivileged attacker call a malicious contract from an unprivileged EOA through `EVM VMConfig creation for transaction execution` while controlling `access list` and `contract creation flag`, under the precondition that London and Prague rules are active on the target height, drive `EVMConfig -> NewEVM -> StateDB journal -> receipt/log/bloom construction` in `x/evm/keeper/config.go::Keeper.VMConfig` so that fork-rule mismatch changes gas cost and refunds, violating the invariant that nonce, contract address, logs, bloom, receipts, and gas must match go-ethereum semantics, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `x/evm/keeper/config.go::Keeper.VMConfig`
- Entrypoint: `EVM VMConfig creation for transaction execution`
- Attacker controls: `access list`, `contract creation flag`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: fork-rule mismatch changes gas cost and refunds through `EVMConfig -> NewEVM -> StateDB journal -> receipt/log/bloom construction`.
- Invariant to test: nonce, contract address, logs, bloom, receipts, and gas must match go-ethereum semantics.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: replay the same scenario through eth_call or estimateGas and through eth_sendRawTransaction and assert the only difference is persistence.
