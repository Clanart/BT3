# Q3059: Routing-To-Wrong Asset Across Mixed Context

## Question
Can an unprivileged attacker enter through `pallet_ismp::handle_unsigned(origin=None, messages) -> pallet_hyper_fungible_token::on_accept` with attacker-controlled send parameters, remote token mappings, precision values, authenticated inbound messages, and timeout inputs and mixing bytes that were valid in one proof, chain, module, order, or beneficiary context with metadata interpreted in another context, and make `convert_to_erc20` send callback or timeout handling through the wrong local asset or wrong pallet account context so `the pallet-side asset routing context` becomes inconsistent with `the exact asset and pallet account authenticated by the request path`, breaking the invariant that receive, response, and timeout callbacks must route to the exact local asset and pallet account implied by the authenticated message and leading to High: valid activity resolves to the wrong beneficiary, wrong token amount, wrong reward amount, or wrong order value?

## Target
- File/function: modules/pallets/hyper-fungible-token/src/impls.rs::convert_to_erc20
- Entrypoint: pallet_ismp::handle_unsigned(origin=None, messages) -> pallet_hyper_fungible_token::on_accept
- Attacker controls: send parameters, remote token mappings, precision values, authenticated inbound messages, and timeout inputs
- Exploit idea: Send callback or timeout handling through the wrong local asset or wrong pallet account context. Try a pair of otherwise valid artifacts where one verification step authenticates the old context and a later step consumes the new context.
- Invariant to test: receive, response, and timeout callbacks must route to the exact local asset and pallet account implied by the authenticated message
- Expected Immunefi impact: High: valid activity resolves to the wrong beneficiary, wrong token amount, wrong reward amount, or wrong order value.
- Fast validation: Vary asset ids and remote contracts across adjacent transfers and assert callbacks touch only the intended asset account. Build two neighboring valid contexts and mutate only the binding field while asserting state, receipts, and balances stay unchanged.
