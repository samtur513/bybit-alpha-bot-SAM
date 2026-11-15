# Execution evidence and assumptions
No actual orders. Public market data only. Portfolio ledger estimates are not exchange fills.

| Item | Historical2022-2023 | Forward |
|---|---|---|
|Spot venue, OHLC|Official Bybit raw snapshots, continuous verified series|Completed candles actually received, raw hashes/receive times|
|Historical publication latency|MODELED close+5s; actual historical availability UNKNOWN|OBSERVED HTTP receive time, separate server/book timestamp|
|min order|C5 MODELED|MODEL_C5 fixed5; OBSERVED_BOOK current observed minOrderAmt|
|Quantity/quote step|Parent current basePrecision/quotePrecision MODELED historically|Current observed Spot fields; no linear qtyStep substitution|
|max quantity/status|Historical UNKNOWN|Current maxMarketOrderQty/status checked|
|Tick price|No invented historical limit order|Market quote VWAP, no limit order submitted; tick grid/acceptance not proved|
|Fees|MODELED taker0.10%, acquired base on buy/received quote on sell|Same MODELED fee; user/account/region/VIP fees UNKNOWN|
|Slippage|MODELED2/5bp each side|MODEL midpoint+2/5bp; OBSERVED displayed VWAP+2/5bp buffer|
|Liquidity/queue|UNKNOWN from OHLC|50-level snapshot, not durable availability, no queue or fill proof|
|Other protection/region/account restrictions|UNKNOWN|Not all exchange/account checks can be verified without orders/account access|
|Capital|Actual simulated cash/qty, no debt, continuous two years|Independent actual virtual balances and persistent cash/dust|
|Funding/borrow|None, unlevered Spot|None; no lending/leverage/short|

Buy quote budgets are floored to quote precision; gross base is floored to base precision, fee deducted in base. Unspent fractions remain cash. This refund/fee-rounding model is explicitly an assumption; real fee precision and execution fragmentation may differ. Sells floor gross base and deduct quote fees. Below-minimum or insufficient-depth/max-quantity failures retain the actual position. No ghost orders are accumulated.

Observed-book virtual estimates use multiple displayed levels where needed; insufficient depth rejects the whole leg. The snapshot may change before a hypothetical order arrives. Execution timing includes actual HTTP receipt and skew bounds; a quote timestamp is not an observed trade. Finite price protection, dynamic market-order rejection, partial acceptance, hidden/RPI depth, network-to-matching-engine latency and user eligibility are not established. EXECUTION gate is PARTIAL_OBSERVED_FILTERS / REAL_EXECUTION_UNVERIFIED.

MTM, hypothetical liquidatable cash and unliquidated/dust values are separate. A large position blocked by depth/filter is not mislabelled dust; dust is below-minimum remainder. Shallow BBO risk checks estimate only MTM. No forced time-stop or parameter changes are introduced.

Primary sources: [Kline](https://bybit-exchange.github.io/docs/v5/market/kline), [Orderbook](https://bybit-exchange.github.io/docs/v5/market/orderbook), [Spot instruments](https://bybit-exchange.github.io/docs/v5/market/instrument), [Order units](https://bybit-exchange.github.io/docs/v5/order/create-order), [Fee currency](https://www.bybit.com/en/help-center/article/Bybit-Spot-Fees-Explained). Source rules do not imply historical account-specific fees or guaranteed acceptance.
