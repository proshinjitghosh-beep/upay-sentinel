# One-page logic chain (upay guideline §10)
**Problem statement:** For upay wallet customers who are scam victims, money sent under social engineering is split across mule accounts and cashed out within minutes, causing irreversible loss and eroding trust. We will build Upay Sentinel, an AI-powered money-trail detector that uses transaction graphs and behaviour to place a temporary hold on suspicious cash-outs for analyst review, with success measured by fraud cash-out value held and false-hold rate.

| Step | Answer |
|---|---|
| 1. User | Fraud analysts (primary); scam victims and honest customers (beneficiaries) |
| 2. Problem | PIN/OTP-authorised scam transfers look normal; cash-out happens in minutes |
| 3. Why now | Transaction graphs plus anomaly models can score trails in real time |
| 4. Solution | Trail engine → risk score → temporary hold + face verification → analyst queue |
| 5. AI role | Detection: Isolation Forest anomaly score + graph analytics; explanation templates |
| 6. Impact | Share of fraud cash-out value held; wrongly-held honest accounts |
| 7. Data | Synthetic 10k transactions, 5% injected fraud, documented in README |
| 8. Validation | Held-out accounts vs rule-only baseline; later, controlled upay data and an analyst-review experiment |
| 9. Scale | Replace CSV with a transaction stream; keep C engine behind the same API; add governance and drift monitoring |
