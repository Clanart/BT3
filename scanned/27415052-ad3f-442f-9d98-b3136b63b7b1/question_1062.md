# Q1062: NewDynamicFeeChecker - Pre London Fallback Charges Validator Min Gas Instead Of Global Basefee

## Question
Can an unprivileged attacker submit a valid transaction with adversarial gas and fee fields through `Cosmos tx dynamic-fee checker with EVM fee market params` while controlling `tip cap` and `multi-message ordering`, under the precondition that the sender has just enough EVM-denom balance for the advertised cost, drive `NewDynamicFeeChecker -> DeductFeeDecorator -> EthereumTx -> ResetGasMeterAndConsumeGas` in `ante/evm/fee_checker.go::NewDynamicFeeChecker` so that pre-London fallback charges validator min gas instead of global baseFee, violating the invariant that baseFee and effectiveGasPrice must be consistent across ante, execution, and refund, and causing a realistic Cronos critical impact by enabling unauthorized EVM-denom movement, fee/refund misaccounting, or account code/nonce mutation that leads to direct user-fund loss?

## Target
- File/function: `ante/evm/fee_checker.go::NewDynamicFeeChecker`
- Entrypoint: `Cosmos tx dynamic-fee checker with EVM fee market params`
- Attacker controls: `tip cap`, `multi-message ordering`; public inputs only, no privileged roles, leaked keys, governance/admin actions, docs/tests/mocks/scripts, or disabled configs.
- Exploit idea: pre-London fallback charges validator min gas instead of global baseFee through `NewDynamicFeeChecker -> DeductFeeDecorator -> EthereumTx -> ResetGasMeterAndConsumeGas`.
- Invariant to test: baseFee and effectiveGasPrice must be consistent across ante, execution, and refund.
- Expected Immunefi impact: HackenProof Cronos Critical - direct unintentional withdrawal, draining, or loss of user funds on the in-scope Ethermint/Cronos blockchain target.
- Fast validation: run a local integration test that submits the crafted raw tx through JSON-RPC and compares committed state with direct keeper queries.
